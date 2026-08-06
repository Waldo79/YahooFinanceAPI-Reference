#!/usr/bin/env python3
"""Read-only size and structure audit for the Yahoo long-history SQLite archive.

Version 0.1.0-candidate.3. This utility never contacts Yahoo and opens the
selected SQLite database in URI read-only mode with PRAGMA query_only enabled.
It writes text, CSV, and JSON reports outside the synchronized repository.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

UTILITY_VERSION = "0.1.0-candidate.3"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LOCAL_CONFIG_FILE = REPOSITORY_ROOT / "config" / "local" / "history_capture_local.json"
OUTPUT_ROOT_ENVIRONMENT_VARIABLE = "YAHOO_HISTORY_CAPTURE_ROOT"
DATABASE_FILENAME = "history.sqlite"
_DEFAULT_ARCHIVE_PARENT = (
    REPOSITORY_ROOT.parent.parent
    if REPOSITORY_ROOT.parent.name.casefold() == "code"
    else REPOSITORY_ROOT.parent
)
DEFAULT_EXTERNAL_ROOT = _DEFAULT_ARCHIVE_PARENT / "Captures" / "long-history"
DEFAULT_SAMPLE_ROWS = 15_000


class AuditError(RuntimeError):
    """Raised when a safe read-only audit cannot be completed."""


@dataclass(frozen=True)
class FileFingerprint:
    name: str
    exists: bool
    bytes: int
    modified_time_ns: int


@dataclass(frozen=True)
class ObjectSize:
    name: str
    object_type: str
    table_name: str
    pages: int
    bytes: int
    payload_bytes: int
    unused_bytes: int
    percent_of_database: float


@dataclass(frozen=True)
class TableSummary:
    table_name: str
    row_count: int | None
    columns: int
    indexes: int
    object_bytes: int
    associated_index_bytes: int
    combined_bytes: int
    percent_of_database: float


@dataclass(frozen=True)
class IndexSummary:
    index_name: str
    table_name: str
    unique: bool
    origin: str
    partial: bool
    columns: str
    bytes: int
    percent_of_database: float


@dataclass(frozen=True)
class ColumnSummary:
    table_name: str
    ordinal: int
    column_name: str
    declared_type: str
    not_null: bool
    primary_key_position: int
    default_value: str
    payload_candidate: bool


@dataclass(frozen=True)
class TextSampleSummary:
    table_name: str
    column_name: str
    sampled_rows: int
    non_null_values: int
    distinct_values: int
    average_characters: float
    maximum_characters: int
    repeated_fraction: float
    projected_text_bytes: int | None
    sample_method: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def filename_utc(value: datetime) -> str:
    return format_utc(value).replace(":", "-")


def normalize_path(path: Path, *, relative_to: Path | None = None) -> Path:
    expanded = Path(os.path.expandvars(str(path))).expanduser()
    if not expanded.is_absolute() and relative_to is not None:
        expanded = relative_to / expanded
    return expanded.resolve(strict=False)


def path_is_within(path: Path, parent: Path) -> bool:
    try:
        normalize_path(path).relative_to(normalize_path(parent))
        return True
    except ValueError:
        return False


def load_local_output_root(config_path: Path = LOCAL_CONFIG_FILE) -> Path | None:
    resolved = normalize_path(config_path)
    if not resolved.exists():
        return None
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditError(f"Cannot read local history config {resolved}: {exc}") from exc
    value = payload.get("output_root") if isinstance(payload, Mapping) else None
    if not isinstance(value, str) or not value.strip():
        raise AuditError(f"Local history config is missing a non-empty output_root: {resolved}")
    return normalize_path(Path(value.strip()), relative_to=resolved.parent)


def resolve_archive_root() -> tuple[Path, str]:
    environment_value = os.environ.get(OUTPUT_ROOT_ENVIRONMENT_VARIABLE, "").strip()
    if environment_value:
        return normalize_path(Path(environment_value)), "environment"
    local = load_local_output_root()
    if local is not None:
        return local, "local_config"
    return normalize_path(DEFAULT_EXTERNAL_ROOT), "safe_external_default"


def resolve_database_path(explicit: str | None) -> tuple[Path, str]:
    if explicit:
        return normalize_path(Path(explicit)), "command_line"
    root, source = resolve_archive_root()
    return root / DATABASE_FILENAME, source


def validate_output_directory(path: Path) -> Path:
    resolved = normalize_path(path)
    if path_is_within(resolved, REPOSITORY_ROOT):
        raise AuditError(
            "SQLite audit reports must remain outside the synchronized repository: "
            f"{resolved}"
        )
    resolved.mkdir(parents=True, exist_ok=False)
    probe = resolved / ".write-test"
    probe.write_text("audit-write-test\n", encoding="utf-8")
    probe.unlink()
    return resolved


def default_output_directory(database_path: Path, started_at: datetime) -> Path:
    return database_path.parent / "audits" / f"{filename_utc(started_at)}_sqlite-audit"


def fingerprint_files(database_path: Path) -> list[FileFingerprint]:
    paths = [
        database_path,
        Path(str(database_path) + "-wal"),
        Path(str(database_path) + "-shm"),
    ]
    fingerprints: list[FileFingerprint] = []
    for path in paths:
        try:
            stat = path.stat()
            fingerprints.append(FileFingerprint(path.name, True, stat.st_size, stat.st_mtime_ns))
        except FileNotFoundError:
            fingerprints.append(FileFingerprint(path.name, False, 0, 0))
    return fingerprints


def main_database_unchanged(before: Sequence[FileFingerprint], after: Sequence[FileFingerprint]) -> bool:
    before_map = {item.name: item for item in before}
    after_map = {item.name: item for item in after}
    main_name = before[0].name
    return before_map.get(main_name) == after_map.get(main_name)


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def connect_read_only(database_path: Path) -> sqlite3.Connection:
    resolved = normalize_path(database_path)
    if not resolved.is_file():
        raise AuditError(f"SQLite database does not exist: {resolved}")
    uri = resolved.as_uri() + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise AuditError(f"Cannot open SQLite database in read-only mode: {resolved}: {exc}") from exc
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA temp_store = MEMORY")
    if int(connection.execute("PRAGMA query_only").fetchone()[0]) != 1:
        connection.close()
        raise AuditError("SQLite did not accept PRAGMA query_only=ON.")
    return connection


def pragma_scalar(connection: sqlite3.Connection, name: str) -> Any:
    return connection.execute(f"PRAGMA {name}").fetchone()[0]


def collect_database_summary(connection: sqlite3.Connection, database_path: Path) -> dict[str, Any]:
    page_size = int(pragma_scalar(connection, "page_size"))
    page_count = int(pragma_scalar(connection, "page_count"))
    freelist_count = int(pragma_scalar(connection, "freelist_count"))
    database_bytes_from_pages = page_size * page_count
    return {
        "database_file_name": database_path.name,
        "database_file_bytes": database_path.stat().st_size,
        "page_size": page_size,
        "page_count": page_count,
        "freelist_count": freelist_count,
        "free_bytes": page_size * freelist_count,
        "free_percent": round((freelist_count / page_count * 100.0) if page_count else 0.0, 4),
        "database_bytes_from_pages": database_bytes_from_pages,
        "journal_mode": str(pragma_scalar(connection, "journal_mode")),
        "auto_vacuum": int(pragma_scalar(connection, "auto_vacuum")),
        "encoding": str(pragma_scalar(connection, "encoding")),
        "user_version": int(pragma_scalar(connection, "user_version")),
        "schema_version": int(pragma_scalar(connection, "schema_version")),
        "application_id": int(pragma_scalar(connection, "application_id")),
        "query_only": int(pragma_scalar(connection, "query_only")),
    }


def collect_master_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT type, name, tbl_name, rootpage, COALESCE(sql, '') AS sql
        FROM sqlite_master
        WHERE name NOT LIKE 'sqlite_stat%'
        ORDER BY type, name
        """
    ).fetchall()
    return [dict(row) for row in rows]


def collect_object_sizes(
    connection: sqlite3.Connection,
    master_rows: Sequence[Mapping[str, Any]],
    database_bytes: int,
) -> tuple[list[ObjectSize], str]:
    master = {str(row["name"]): row for row in master_rows}
    try:
        rows = connection.execute(
            """
            SELECT name,
                   COUNT(*) AS pages,
                   SUM(pgsize) AS bytes,
                   SUM(payload) AS payload_bytes,
                   SUM(unused) AS unused_bytes
            FROM dbstat
            GROUP BY name
            ORDER BY bytes DESC, name
            """
        ).fetchall()
        source = "dbstat"
    except sqlite3.DatabaseError:
        return [], "unavailable"

    output: list[ObjectSize] = []
    for row in rows:
        name = str(row["name"])
        info = master.get(name, {})
        object_type = str(info.get("type", "internal"))
        table_name = str(info.get("tbl_name", name))
        bytes_value = int(row["bytes"] or 0)
        output.append(
            ObjectSize(
                name=name,
                object_type=object_type,
                table_name=table_name,
                pages=int(row["pages"] or 0),
                bytes=bytes_value,
                payload_bytes=int(row["payload_bytes"] or 0),
                unused_bytes=int(row["unused_bytes"] or 0),
                percent_of_database=round((bytes_value / database_bytes * 100.0) if database_bytes else 0.0, 4),
            )
        )
    return output, source


def table_names(master_rows: Sequence[Mapping[str, Any]]) -> list[str]:
    return [str(row["name"]) for row in master_rows if row["type"] == "table" and not str(row["name"]).startswith("sqlite_")]


def collect_columns(connection: sqlite3.Connection, tables: Sequence[str]) -> list[ColumnSummary]:
    output: list[ColumnSummary] = []
    payload_terms = ("raw", "body", "json", "payload", "blob")
    for table in tables:
        rows = connection.execute(f"PRAGMA table_info({quote_identifier(table)})").fetchall()
        for row in rows:
            name = str(row["name"])
            declared = str(row["type"] or "")
            lowered = name.casefold()
            output.append(
                ColumnSummary(
                    table_name=table,
                    ordinal=int(row["cid"]),
                    column_name=name,
                    declared_type=declared,
                    not_null=bool(row["notnull"]),
                    primary_key_position=int(row["pk"]),
                    default_value="" if row["dflt_value"] is None else str(row["dflt_value"]),
                    payload_candidate=("BLOB" in declared.upper() or any(term in lowered for term in payload_terms)),
                )
            )
    return output


def collect_indexes(
    connection: sqlite3.Connection,
    tables: Sequence[str],
    object_sizes: Sequence[ObjectSize],
    database_bytes: int,
) -> list[IndexSummary]:
    size_by_name = {item.name: item.bytes for item in object_sizes}
    output: list[IndexSummary] = []
    for table in tables:
        rows = connection.execute(f"PRAGMA index_list({quote_identifier(table)})").fetchall()
        for row in rows:
            index_name = str(row["name"])
            columns_rows = connection.execute(f"PRAGMA index_info({quote_identifier(index_name)})").fetchall()
            columns = ", ".join(str(col["name"]) for col in columns_rows if col["name"] is not None)
            bytes_value = int(size_by_name.get(index_name, 0))
            output.append(
                IndexSummary(
                    index_name=index_name,
                    table_name=table,
                    unique=bool(row["unique"]),
                    origin=str(row["origin"]),
                    partial=bool(row["partial"]),
                    columns=columns,
                    bytes=bytes_value,
                    percent_of_database=round((bytes_value / database_bytes * 100.0) if database_bytes else 0.0, 4),
                )
            )
    return sorted(output, key=lambda item: (-item.bytes, item.index_name))


def collect_row_counts(connection: sqlite3.Connection, tables: Sequence[str], *, exact: bool) -> dict[str, int | None]:
    if not exact:
        return {table: None for table in tables}
    output: dict[str, int | None] = {}
    for table in tables:
        output[table] = int(connection.execute(f"SELECT COUNT(*) FROM {quote_identifier(table)}").fetchone()[0])
    return output


def collect_table_summaries(
    tables: Sequence[str],
    columns: Sequence[ColumnSummary],
    indexes: Sequence[IndexSummary],
    object_sizes: Sequence[ObjectSize],
    row_counts: Mapping[str, int | None],
    database_bytes: int,
) -> list[TableSummary]:
    object_by_name = {item.name: item.bytes for item in object_sizes}
    column_counts = Counter(item.table_name for item in columns)
    indexes_by_table: dict[str, list[IndexSummary]] = {}
    for index in indexes:
        indexes_by_table.setdefault(index.table_name, []).append(index)
    output: list[TableSummary] = []
    for table in tables:
        table_bytes = int(object_by_name.get(table, 0))
        index_bytes = sum(item.bytes for item in indexes_by_table.get(table, []))
        combined = table_bytes + index_bytes
        output.append(
            TableSummary(
                table_name=table,
                row_count=row_counts.get(table),
                columns=column_counts[table],
                indexes=len(indexes_by_table.get(table, [])),
                object_bytes=table_bytes,
                associated_index_bytes=index_bytes,
                combined_bytes=combined,
                percent_of_database=round((combined / database_bytes * 100.0) if database_bytes else 0.0, 4),
            )
        )
    return sorted(output, key=lambda item: (-item.combined_bytes, item.table_name))


def _sample_rowids(connection: sqlite3.Connection, table: str, sample_rows: int) -> tuple[list[sqlite3.Row], str]:
    if sample_rows <= 0:
        return [], "disabled"
    quoted = quote_identifier(table)
    try:
        bounds = connection.execute(f"SELECT MIN(rowid), MAX(rowid) FROM {quoted}").fetchone()
        minimum = bounds[0]
        maximum = bounds[1]
    except sqlite3.DatabaseError:
        rows = connection.execute(f"SELECT * FROM {quoted} LIMIT ?", (sample_rows,)).fetchall()
        return rows, "first_rows"
    if minimum is None or maximum is None:
        return [], "empty"
    windows = 3
    per_window = max(1, sample_rows // windows)
    starts = [int(minimum), int((minimum + maximum) // 2), max(int(minimum), int(maximum) - per_window + 1)]
    selected: list[sqlite3.Row] = []
    seen: set[int] = set()
    for start in starts:
        rows = connection.execute(
            f"SELECT rowid AS __audit_rowid__, * FROM {quoted} WHERE rowid >= ? ORDER BY rowid LIMIT ?",
            (start, per_window),
        ).fetchall()
        for row in rows:
            rowid = int(row["__audit_rowid__"])
            if rowid not in seen:
                seen.add(rowid)
                selected.append(row)
                if len(selected) >= sample_rows:
                    return selected, "three_rowid_windows"
    return selected, "three_rowid_windows"


def collect_text_samples(
    connection: sqlite3.Connection,
    tables: Sequence[str],
    columns: Sequence[ColumnSummary],
    row_counts: Mapping[str, int | None],
    sample_rows: int,
) -> list[TextSampleSummary]:
    columns_by_table: dict[str, list[ColumnSummary]] = {}
    for column in columns:
        if "TEXT" in column.declared_type.upper() or column.declared_type == "":
            columns_by_table.setdefault(column.table_name, []).append(column)
    output: list[TextSampleSummary] = []
    for table in tables:
        selected_columns = columns_by_table.get(table, [])
        if not selected_columns:
            continue
        rows, method = _sample_rowids(connection, table, sample_rows)
        for column in selected_columns:
            values = [row[column.column_name] for row in rows if row[column.column_name] is not None]
            texts = [str(value) for value in values]
            non_null = len(texts)
            total_chars = sum(len(value) for value in texts)
            distinct = len(set(texts))
            average = total_chars / non_null if non_null else 0.0
            repeated_fraction = 1.0 - (distinct / non_null) if non_null else 0.0
            row_count = row_counts.get(table)
            projected = int(round(average * row_count)) if row_count is not None else None
            output.append(
                TextSampleSummary(
                    table_name=table,
                    column_name=column.column_name,
                    sampled_rows=len(rows),
                    non_null_values=non_null,
                    distinct_values=distinct,
                    average_characters=round(average, 3),
                    maximum_characters=max((len(value) for value in texts), default=0),
                    repeated_fraction=round(repeated_fraction, 6),
                    projected_text_bytes=projected,
                    sample_method=method,
                )
            )
    return sorted(
        output,
        key=lambda item: (-(item.projected_text_bytes or 0), item.table_name, item.column_name),
    )


def run_integrity_check(connection: sqlite3.Connection, mode: str) -> list[str]:
    if mode == "skip":
        return ["SKIPPED"]
    pragma = "integrity_check" if mode == "full" else "quick_check"
    return [str(row[0]) for row in connection.execute(f"PRAGMA {pragma}").fetchall()]


def write_csv(path: Path, rows: Iterable[Mapping[str, Any]], fieldnames: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def dataclass_rows(items: Iterable[Any]) -> list[dict[str, Any]]:
    return [asdict(item) for item in items]


def human_bytes(value: int | None) -> str:
    if value is None:
        return "not calculated"
    units = ("B", "KB", "MB", "GB", "TB")
    number = float(value)
    for unit in units:
        if abs(number) < 1024.0 or unit == units[-1]:
            return f"{number:,.2f} {unit}"
        number /= 1024.0
    return f"{number:,.2f} TB"


def build_findings(
    summary: Mapping[str, Any],
    tables: Sequence[TableSummary],
    indexes: Sequence[IndexSummary],
    columns: Sequence[ColumnSummary],
    text_samples: Sequence[TextSampleSummary],
    object_size_source: str,
) -> list[str]:
    findings: list[str] = []
    if object_size_source == "dbstat" and tables:
        largest = tables[0]
        findings.append(
            f"Largest table family: {largest.table_name} plus its indexes uses "
            f"{human_bytes(largest.combined_bytes)} ({largest.percent_of_database:.2f}% of database pages)."
        )
    if indexes:
        total_index = sum(item.bytes for item in indexes)
        findings.append(
            f"Indexes use {human_bytes(total_index)} "
            f"({(total_index / int(summary['database_bytes_from_pages']) * 100.0) if summary['database_bytes_from_pages'] else 0.0:.2f}% of database pages)."
        )
    column_keys = {(item.table_name, item.column_name) for item in columns}
    repeated_bar_columns = [
        name
        for name in ("datetime_utc", "first_seen_run_id", "last_seen_run_id", "source_file", "source_sha256")
        if ("bars", name) in column_keys
    ]
    if repeated_bar_columns:
        findings.append(
            "The bars table repeats text provenance/date columns on every price row: "
            + ", ".join(repeated_bar_columns)
            + ". A normalized source/run table and deriving UTC text from timestamp_utc are likely optimization targets."
        )
    blob_columns = [item for item in columns if "BLOB" in item.declared_type.upper()]
    payload_columns = [item for item in columns if item.payload_candidate]
    if not blob_columns:
        findings.append("No declared BLOB columns were found; compressed raw HTTP bodies are not stored as SQLite BLOBs.")
    if payload_columns:
        findings.append(
            "JSON/raw-named columns present in SQLite: "
            + ", ".join(f"{item.table_name}.{item.column_name}" for item in payload_columns)
            + ". These should be reviewed separately from the external .json.gz evidence."
        )
    high_repeat = [
        item
        for item in text_samples
        if item.non_null_values >= 100 and item.repeated_fraction >= 0.95 and (item.projected_text_bytes or 0) >= 1_000_000
    ]
    if high_repeat:
        findings.append(
            "Sampled highly repeated text columns with material projected character volume: "
            + ", ".join(f"{item.table_name}.{item.column_name}" for item in high_repeat[:10])
            + "."
        )
    if float(summary["free_percent"]) >= 5.0:
        findings.append(
            f"Freelist pages are {summary['free_percent']:.2f}% of the database. VACUUM might reclaim space, but this audit does not modify the archive."
        )
    else:
        findings.append(
            f"Freelist pages are only {summary['free_percent']:.2f}% of the database; ordinary free-page reclamation alone is unlikely to explain the size."
        )
    return findings


def write_reports(
    output_dir: Path,
    *,
    database_summary: Mapping[str, Any],
    master_rows: Sequence[Mapping[str, Any]],
    object_sizes: Sequence[ObjectSize],
    object_size_source: str,
    tables: Sequence[TableSummary],
    indexes: Sequence[IndexSummary],
    columns: Sequence[ColumnSummary],
    text_samples: Sequence[TextSampleSummary],
    integrity_mode: str,
    integrity_results: Sequence[str],
    findings: Sequence[str],
    before: Sequence[FileFingerprint],
    after: Sequence[FileFingerprint],
    started_at: datetime,
    completed_at: datetime,
    database_source: str,
) -> dict[str, Any]:
    summary_rows = [{"metric": key, "value": value} for key, value in database_summary.items()]
    write_csv(output_dir / "database_summary.csv", summary_rows, ("metric", "value"))
    write_csv(
        output_dir / "schema_objects.csv",
        master_rows,
        ("type", "name", "tbl_name", "rootpage", "sql"),
    )
    write_csv(
        output_dir / "object_sizes.csv",
        dataclass_rows(object_sizes),
        ("name", "object_type", "table_name", "pages", "bytes", "payload_bytes", "unused_bytes", "percent_of_database"),
    )
    write_csv(
        output_dir / "tables.csv",
        dataclass_rows(tables),
        ("table_name", "row_count", "columns", "indexes", "object_bytes", "associated_index_bytes", "combined_bytes", "percent_of_database"),
    )
    write_csv(
        output_dir / "indexes.csv",
        dataclass_rows(indexes),
        ("index_name", "table_name", "unique", "origin", "partial", "columns", "bytes", "percent_of_database"),
    )
    write_csv(
        output_dir / "columns.csv",
        dataclass_rows(columns),
        ("table_name", "ordinal", "column_name", "declared_type", "not_null", "primary_key_position", "default_value", "payload_candidate"),
    )
    write_csv(
        output_dir / "text_storage_sample.csv",
        dataclass_rows(text_samples),
        ("table_name", "column_name", "sampled_rows", "non_null_values", "distinct_values", "average_characters", "maximum_characters", "repeated_fraction", "projected_text_bytes", "sample_method"),
    )

    unchanged = main_database_unchanged(before, after)
    manifest = {
        "utility_version": UTILITY_VERSION,
        "started_at_utc": format_utc(started_at),
        "completed_at_utc": format_utc(completed_at),
        "database_file_name": database_summary["database_file_name"],
        "database_path_source": database_source,
        "absolute_database_path_persisted": False,
        "connection_mode": "URI mode=ro plus PRAGMA query_only=ON",
        "network_access": False,
        "object_size_source": object_size_source,
        "integrity_check_mode": integrity_mode,
        "integrity_check_results": list(integrity_results),
        "main_database_unchanged": unchanged,
        "before_files": [asdict(item) for item in before],
        "after_files": [asdict(item) for item in after],
        "report_files": sorted(path.name for path in output_dir.iterdir() if path.is_file()),
    }
    (output_dir / "audit_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    lines = [
        "Yahoo Long-History SQLite Read-Only Audit",
        f"Utility version: {UTILITY_VERSION}",
        f"Started UTC: {format_utc(started_at)}",
        f"Completed UTC: {format_utc(completed_at)}",
        "Connection: URI mode=ro; PRAGMA query_only=ON",
        "Network access: none",
        f"Database file: {database_summary['database_file_name']}",
        f"Database size: {human_bytes(int(database_summary['database_file_bytes']))}",
        f"Page size: {database_summary['page_size']:,} bytes",
        f"Pages: {database_summary['page_count']:,}",
        f"Freelist pages: {database_summary['freelist_count']:,} ({database_summary['free_percent']:.4f}%)",
        f"Object size source: {object_size_source}",
        f"Integrity check ({integrity_mode}): {'; '.join(integrity_results)}",
        f"Main database unchanged during audit: {unchanged}",
        "",
        "Largest table families",
    ]
    for item in tables[:12]:
        row_count = "not calculated" if item.row_count is None else f"{item.row_count:,} rows"
        lines.append(
            f"- {item.table_name}: {human_bytes(item.combined_bytes)}; {row_count}; "
            f"table {human_bytes(item.object_bytes)}, indexes {human_bytes(item.associated_index_bytes)}"
        )
    lines.extend(["", "Largest indexes"])
    for item in indexes[:12]:
        lines.append(f"- {item.index_name} on {item.table_name} ({item.columns}): {human_bytes(item.bytes)}")
    lines.extend(["", "Findings"])
    lines.extend(f"- {finding}" for finding in findings)
    lines.extend(
        [
            "",
            "Safety conclusion",
            "- No database-changing SQL is issued by this utility.",
            "- No VACUUM, REINDEX, ANALYZE, checkpoint, migration, or optimization is performed.",
            "- Optimization decisions should be made only from a copy after this report is reviewed.",
        ]
    )
    (output_dir / "audit_report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", help="SQLite database path. Defaults to configured long-history/history.sqlite.")
    parser.add_argument("--output-dir", help="New external directory for audit reports.")
    parser.add_argument(
        "--sample-rows",
        type=int,
        default=DEFAULT_SAMPLE_ROWS,
        help=f"Maximum sampled rows per table for text repetition estimates (default: {DEFAULT_SAMPLE_ROWS}).",
    )
    parser.add_argument(
        "--integrity-check",
        choices=("quick", "full", "skip"),
        default="quick",
        help="Read-only SQLite integrity check (default: quick).",
    )
    parser.add_argument(
        "--skip-exact-row-counts",
        action="store_true",
        help="Avoid full COUNT(*) scans; table row_count fields will be blank.",
    )
    return parser


def run_audit(args: argparse.Namespace) -> tuple[Path, dict[str, Any]]:
    if args.sample_rows < 0:
        raise AuditError("--sample-rows cannot be negative.")
    started_at = utc_now()
    database_path, database_source = resolve_database_path(args.database)
    database_path = normalize_path(database_path)
    if path_is_within(database_path, REPOSITORY_ROOT):
        raise AuditError(f"The long-history database must remain outside the synchronized repository: {database_path}")
    output_candidate = normalize_path(Path(args.output_dir)) if args.output_dir else default_output_directory(database_path, started_at)
    before = fingerprint_files(database_path)
    connection = connect_read_only(database_path)
    try:
        database_summary = collect_database_summary(connection, database_path)
        master_rows = collect_master_rows(connection)
        object_sizes, object_size_source = collect_object_sizes(
            connection,
            master_rows,
            int(database_summary["database_bytes_from_pages"]),
        )
        tables_names = table_names(master_rows)
        columns = collect_columns(connection, tables_names)
        indexes = collect_indexes(
            connection,
            tables_names,
            object_sizes,
            int(database_summary["database_bytes_from_pages"]),
        )
        row_counts = collect_row_counts(connection, tables_names, exact=not args.skip_exact_row_counts)
        tables = collect_table_summaries(
            tables_names,
            columns,
            indexes,
            object_sizes,
            row_counts,
            int(database_summary["database_bytes_from_pages"]),
        )
        text_samples = collect_text_samples(connection, tables_names, columns, row_counts, args.sample_rows)
        integrity_results = run_integrity_check(connection, args.integrity_check)
        findings = build_findings(database_summary, tables, indexes, columns, text_samples, object_size_source)
    finally:
        connection.close()
    after = fingerprint_files(database_path)
    if not main_database_unchanged(before, after):
        raise AuditError(
            "The main SQLite file changed while the audit was running. Another process may have written to it; "
            "no report was created."
        )
    output_dir = validate_output_directory(output_candidate)
    completed_at = utc_now()
    manifest = write_reports(
        output_dir,
        database_summary=database_summary,
        master_rows=master_rows,
        object_sizes=object_sizes,
        object_size_source=object_size_source,
        tables=tables,
        indexes=indexes,
        columns=columns,
        text_samples=text_samples,
        integrity_mode=args.integrity_check,
        integrity_results=integrity_results,
        findings=findings,
        before=before,
        after=after,
        started_at=started_at,
        completed_at=completed_at,
        database_source=database_source,
    )
    return output_dir, manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        database_path, source = resolve_database_path(args.database)
        print(f"SQLite database: {database_path} ({source})")
        print("Audit mode: read-only; no network access; no database optimization")
        output_dir, manifest = run_audit(args)
    except (AuditError, OSError, sqlite3.Error, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"Audit folder: {output_dir}")
    print(f"Integrity check: {'; '.join(manifest['integrity_check_results'])}")
    print(f"Main database unchanged: {manifest['main_database_unchanged']}")
    print("Primary report: audit_report.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
