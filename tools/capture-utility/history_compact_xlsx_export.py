#!/usr/bin/env python3
"""Export a verified compact Yahoo long-history database to streaming XLSX.

Version 0.1.0-candidate.11. The verified ``history_compact.sqlite`` database is
opened with SQLite URI ``mode=ro`` and ``PRAGMA query_only=ON``. The exporter
uses only the Python standard library, performs no network requests, and writes
only a new external export folder. It never changes, moves, replaces, or deletes
``history_compact.sqlite`` or the legacy ``history.sqlite`` database.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sqlite3
import sys
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence
from xml.sax.saxutils import escape

UTILITY_VERSION = "0.1.0-candidate.11"
COMPACT_SCHEMA_NAME = "compact_long_history"
COMPACT_SCHEMA_VERSION = "1"
COMPACT_DATABASE_FILENAME = "history_compact.sqlite"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LOCAL_CONFIG_FILE = REPOSITORY_ROOT / "config" / "local" / "history_capture_local.json"
OUTPUT_ROOT_ENVIRONMENT_VARIABLE = "YAHOO_HISTORY_CAPTURE_ROOT"
_DEFAULT_ARCHIVE_PARENT = (
    REPOSITORY_ROOT.parent.parent
    if REPOSITORY_ROOT.parent.name.casefold() == "code"
    else REPOSITORY_ROOT.parent
)
DEFAULT_EXTERNAL_ROOT = _DEFAULT_ARCHIVE_PARENT / "Captures" / "long-history"
EXCEL_MAX_ROWS = 1_048_576
DEFAULT_MAX_DATA_ROWS_PER_SHEET = 1_000_000


class ExportError(RuntimeError):
    """Raised when a compact-history XLSX export cannot safely continue."""


@dataclass(frozen=True)
class FileFingerprint:
    name: str
    exists: bool
    bytes: int
    modified_time_ns: int


@dataclass(frozen=True)
class SheetInfo:
    name: str
    rows: int
    data_rows: int
    columns: int


@dataclass(frozen=True)
class ExportFilters:
    symbols: tuple[str, ...]
    intervals: tuple[str, ...]
    start_epoch: int | None
    end_epoch_exclusive: int | None
    include_revisions: bool


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
        raise ExportError(f"Cannot read local history config {resolved}: {exc}") from exc
    value = payload.get("output_root") if isinstance(payload, Mapping) else None
    if not isinstance(value, str) or not value.strip():
        raise ExportError(f"Local history config is missing a non-empty output_root: {resolved}")
    return normalize_path(Path(value.strip()), relative_to=resolved.parent)


def resolve_archive_root() -> tuple[Path, str]:
    environment_value = os.environ.get(OUTPUT_ROOT_ENVIRONMENT_VARIABLE, "").strip()
    if environment_value:
        return normalize_path(Path(environment_value)), "environment"
    local = load_local_output_root()
    if local is not None:
        return local, "local_config"
    return normalize_path(DEFAULT_EXTERNAL_ROOT), "safe_external_default"


def fingerprint_files(database_path: Path) -> list[FileFingerprint]:
    candidates = [database_path, Path(str(database_path) + "-wal"), Path(str(database_path) + "-shm")]
    output: list[FileFingerprint] = []
    for path in candidates:
        try:
            stat = path.stat()
            output.append(FileFingerprint(path.name, True, stat.st_size, stat.st_mtime_ns))
        except FileNotFoundError:
            output.append(FileFingerprint(path.name, False, 0, 0))
    return output


def main_database_unchanged(before: Sequence[FileFingerprint], after: Sequence[FileFingerprint]) -> bool:
    if not before or not after:
        return False
    before_map = {item.name: item for item in before}
    after_map = {item.name: item for item in after}
    return before_map.get(before[0].name) == after_map.get(after[0].name)


def read_compact_meta(connection: sqlite3.Connection) -> dict[str, str]:
    return {str(row[0]): str(row[1]) for row in connection.execute("SELECT key, value FROM meta")}


def validate_compact_schema(connection: sqlite3.Connection, *, require_verified: bool = True) -> dict[str, str]:
    tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    required = {
        "meta", "archive_meta", "symbols", "intervals", "run_ids", "sources",
        "event_types", "runs", "symbol_state", "bars", "bar_revisions", "events",
        "event_revisions", "symbol_runs",
    }
    missing = sorted(required - tables)
    if missing:
        raise ExportError(f"Compact database is missing required tables: {', '.join(missing)}")
    meta = read_compact_meta(connection)
    if meta.get("schema_name") != COMPACT_SCHEMA_NAME:
        raise ExportError(
            f"Unexpected compact schema name: {meta.get('schema_name')!r}; expected {COMPACT_SCHEMA_NAME!r}."
        )
    if meta.get("schema_version") != COMPACT_SCHEMA_VERSION:
        raise ExportError(
            f"Unsupported compact schema version: {meta.get('schema_version')!r}; expected {COMPACT_SCHEMA_VERSION!r}."
        )
    if require_verified and meta.get("build_status") not in {"VERIFIED_COMPLETE", "ACTIVE_COMPACT"}:
        raise ExportError(
            f"Compact database is not verified for export; build_status={meta.get('build_status')!r}."
        )
    return meta


def connect_read_only(database_path: Path) -> sqlite3.Connection:
    resolved = normalize_path(database_path)
    if path_is_within(resolved, REPOSITORY_ROOT):
        raise ExportError(f"Compact history databases must remain outside the repository: {resolved}")
    if not resolved.is_file():
        raise ExportError(f"Compact database does not exist: {resolved}")
    try:
        connection = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise ExportError(f"Cannot open compact database in read-only mode: {exc}") from exc
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA temp_store=MEMORY")
    if int(connection.execute("PRAGMA query_only").fetchone()[0]) != 1:
        connection.close()
        raise ExportError("SQLite did not accept PRAGMA query_only=ON.")
    validate_compact_schema(connection)
    return connection


def compact_database_candidates(archive_root: Path) -> list[Path]:
    root = normalize_path(archive_root)
    candidates = list((root / "compact-rebuilds").glob(f"*/{COMPACT_DATABASE_FILENAME}"))
    verified: list[Path] = []
    for candidate in candidates:
        try:
            con = connect_read_only(candidate)
            con.close()
            verified.append(candidate)
        except (sqlite3.Error, ExportError, OSError):
            continue
    return sorted(verified, key=lambda path: path.stat().st_mtime_ns, reverse=True)


def resolve_compact_database(
    explicit: Path | None,
    rebuild_dir: Path | None,
) -> tuple[Path, str, Path]:
    if explicit is not None and rebuild_dir is not None:
        raise ExportError("Use either --database or --rebuild-dir, not both.")
    archive_root, archive_source = resolve_archive_root()
    if explicit is not None:
        path = normalize_path(explicit)
        source = "command_line_database"
    elif rebuild_dir is not None:
        path = normalize_path(rebuild_dir) / COMPACT_DATABASE_FILENAME
        source = "command_line_rebuild_dir"
    else:
        candidates = compact_database_candidates(archive_root)
        if not candidates:
            raise ExportError(
                f"No verified {COMPACT_DATABASE_FILENAME} was found under {archive_root / 'compact-rebuilds'}."
            )
        path = candidates[0]
        source = f"latest_verified_compact_rebuild:{archive_source}"
    con = connect_read_only(path)
    con.close()
    return path, source, archive_root


def validate_new_output_directory(path: Path) -> Path:
    resolved = normalize_path(path)
    if path_is_within(resolved, REPOSITORY_ROOT):
        raise ExportError(f"Long-history export files must remain outside the repository: {resolved}")
    resolved.mkdir(parents=True, exist_ok=False)
    probe = resolved / ".write-test"
    probe.write_text("xlsx-export-write-test\n", encoding="utf-8")
    probe.unlink()
    return resolved


def parse_iso_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ExportError(f"{label} must use YYYY-MM-DD: {value!r}") from exc


def date_start_epoch(value: date) -> int:
    return int(datetime.combine(value, time.min, tzinfo=timezone.utc).timestamp())


def end_date_exclusive_epoch(value: date) -> int:
    return date_start_epoch(value + timedelta(days=1))


def epoch_to_datetime(value: int | None) -> datetime | None:
    if value is None:
        return None
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    try:
        return epoch + timedelta(seconds=int(value))
    except (OverflowError, ValueError) as exc:
        raise ExportError(f"History timestamp is outside the supported range: {value}") from exc


def parse_symbol_text(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def read_symbol_file(path: Path) -> list[str]:
    resolved = normalize_path(path)
    if not resolved.is_file():
        raise ExportError(f"Symbol file does not exist: {resolved}")
    symbols: list[str] = []
    with resolved.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        rows = list(reader)
    if not rows:
        return symbols
    start = 1 if rows[0] and rows[0][0].strip().casefold() == "symbol" else 0
    for row in rows[start:]:
        if row and row[0].strip():
            symbols.append(row[0].strip())
    return symbols


def deduplicate(values: Iterable[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            output.append(value)
    return output


def resolve_filters(connection: sqlite3.Connection, args: argparse.Namespace) -> ExportFilters:
    requested_symbols = deduplicate(
        parse_symbol_text(args.symbols)
        + (read_symbol_file(args.symbols_file) if args.symbols_file else [])
    )
    available = {str(row[0]): int(row[1]) for row in connection.execute("SELECT symbol, symbol_id FROM symbols")}
    available_folded = {symbol.casefold(): symbol for symbol in available}
    if requested_symbols:
        unknown = [symbol for symbol in requested_symbols if symbol.casefold() not in available_folded]
        if unknown:
            raise ExportError(f"Requested symbols are not present in the compact database: {', '.join(unknown)}")
        symbols = [available_folded[symbol.casefold()] for symbol in requested_symbols]
    else:
        symbols = sorted(available, key=lambda item: item.encode("utf-8"))
    if args.smoke:
        symbols = symbols[:5]
    available_intervals = {str(row[0]) for row in connection.execute("SELECT interval FROM intervals")}
    intervals = deduplicate(args.interval or [])
    unknown_intervals = [item for item in intervals if item not in available_intervals]
    if unknown_intervals:
        raise ExportError(f"Requested intervals are not present in the compact database: {', '.join(unknown_intervals)}")
    start_epoch = date_start_epoch(parse_iso_date(args.start_date, "--start-date")) if args.start_date else None
    end_epoch = end_date_exclusive_epoch(parse_iso_date(args.through_date, "--through-date")) if args.through_date else None
    if start_epoch is not None and end_epoch is not None and start_epoch >= end_epoch:
        raise ExportError("--start-date must not be later than --through-date.")
    return ExportFilters(tuple(symbols), tuple(intervals), start_epoch, end_epoch, bool(args.include_revisions))


def where_clause(filters: ExportFilters, *, timestamp_expression: str) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if filters.symbols:
        placeholders = ",".join("?" for _ in filters.symbols)
        clauses.append(f"s.symbol IN ({placeholders})")
        params.extend(filters.symbols)
    if filters.intervals:
        placeholders = ",".join("?" for _ in filters.intervals)
        clauses.append(f"i.interval IN ({placeholders})")
        params.extend(filters.intervals)
    if filters.start_epoch is not None:
        clauses.append(f"{timestamp_expression} >= ?")
        params.append(filters.start_epoch)
    if filters.end_epoch_exclusive is not None:
        clauses.append(f"{timestamp_expression} < ?")
        params.append(filters.end_epoch_exclusive)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def count_rows(connection: sqlite3.Connection, table: str, filters: ExportFilters, timestamp_column: str) -> int:
    where, params = where_clause(filters, timestamp_expression=f"x.{timestamp_column}")
    query = f"""
        SELECT COUNT(*)
        FROM {table} x
        JOIN symbols s ON s.symbol_id=x.symbol_id
        JOIN intervals i ON i.interval_id=x.interval_id
        {where}
    """
    return int(connection.execute(query, params).fetchone()[0])


def source_counts(connection: sqlite3.Connection, filters: ExportFilters) -> dict[str, int]:
    return {
        "symbols": len(filters.symbols),
        "bars": count_rows(connection, "bars", filters, "timestamp_utc"),
        "events": count_rows(connection, "events", filters, "event_timestamp_utc"),
        "bar_revisions": count_rows(connection, "bar_revisions", filters, "timestamp_utc") if filters.include_revisions else 0,
        "event_revisions": count_rows(connection, "event_revisions", filters, "event_timestamp_utc") if filters.include_revisions else 0,
    }


def symbol_summary_rows(connection: sqlite3.Connection, filters: ExportFilters) -> Iterator[Sequence[Any]]:
    symbol_placeholders = ",".join("?" for _ in filters.symbols)
    interval_clause = ""
    outer_params: list[Any] = list(filters.symbols)
    if filters.intervals:
        interval_placeholders = ",".join("?" for _ in filters.intervals)
        interval_clause = f" AND i.interval IN ({interval_placeholders})"
        outer_params.extend(filters.intervals)

    bar_date_clauses: list[str] = []
    event_date_clauses: list[str] = []
    bar_params: list[Any] = []
    event_params: list[Any] = []
    if filters.start_epoch is not None:
        bar_date_clauses.append("timestamp_utc >= ?")
        event_date_clauses.append("event_timestamp_utc >= ?")
        bar_params.append(filters.start_epoch)
        event_params.append(filters.start_epoch)
    if filters.end_epoch_exclusive is not None:
        bar_date_clauses.append("timestamp_utc < ?")
        event_date_clauses.append("event_timestamp_utc < ?")
        bar_params.append(filters.end_epoch_exclusive)
        event_params.append(filters.end_epoch_exclusive)
    bar_where = (" WHERE " + " AND ".join(bar_date_clauses)) if bar_date_clauses else ""
    event_where = (" WHERE " + " AND ".join(event_date_clauses)) if event_date_clauses else ""

    query = f"""
        WITH filtered_bars AS (
            SELECT symbol_id, interval_id, COUNT(*) AS bar_count,
                   MIN(timestamp_utc) AS first_bar, MAX(timestamp_utc) AS last_bar
            FROM bars{bar_where}
            GROUP BY symbol_id, interval_id
        ),
        filtered_events AS (
            SELECT symbol_id, interval_id, COUNT(*) AS event_count
            FROM events{event_where}
            GROUP BY symbol_id, interval_id
        )
        SELECT s.symbol, i.interval,
               COALESCE(fb.bar_count, 0), fb.first_bar, fb.last_bar,
               COALESCE(fe.event_count, 0),
               COALESCE(st.full_refresh_required, 0),
               COALESCE(st.full_refresh_reason, ''),
               st.last_checked_at_utc
        FROM symbols s
        CROSS JOIN intervals i
        LEFT JOIN filtered_bars fb ON fb.symbol_id=s.symbol_id AND fb.interval_id=i.interval_id
        LEFT JOIN filtered_events fe ON fe.symbol_id=s.symbol_id AND fe.interval_id=i.interval_id
        LEFT JOIN symbol_state st ON st.symbol_id=s.symbol_id AND st.interval_id=i.interval_id
        WHERE s.symbol IN ({symbol_placeholders}) {interval_clause}
          AND (fb.bar_count IS NOT NULL OR fe.event_count IS NOT NULL OR st.symbol_id IS NOT NULL)
        ORDER BY s.symbol COLLATE BINARY, i.interval COLLATE BINARY
    """
    params = bar_params + event_params + outer_params
    for row in connection.execute(query, params):
        yield (
            row[0], row[1], int(row[2]), epoch_to_datetime(row[3]), epoch_to_datetime(row[4]),
            int(row[5]), bool(row[6]), row[7], row[8],
        )


def bars_rows(connection: sqlite3.Connection, filters: ExportFilters) -> Iterator[Sequence[Any]]:
    where, params = where_clause(filters, timestamp_expression="b.timestamp_utc")
    query = f"""
        SELECT s.symbol, i.interval, b.timestamp_utc,
               b.open, b.high, b.low, b.close, b.adjclose, b.volume,
               first_run.run_id, last_run.run_id,
               src.source_file, hex(src.source_sha256)
        FROM bars b
        JOIN symbols s ON s.symbol_id=b.symbol_id
        JOIN intervals i ON i.interval_id=b.interval_id
        JOIN run_ids first_run ON first_run.run_key=b.first_seen_run_key
        JOIN run_ids last_run ON last_run.run_key=b.last_seen_run_key
        JOIN sources src ON src.source_id=b.source_id
        {where}
        ORDER BY s.symbol COLLATE BINARY, i.interval COLLATE BINARY, b.timestamp_utc
    """
    for row in connection.execute(query, params):
        yield (row[0], row[1], epoch_to_datetime(row[2]), *row[3:12], str(row[12]).lower())


def events_rows(connection: sqlite3.Connection, filters: ExportFilters) -> Iterator[Sequence[Any]]:
    where, params = where_clause(filters, timestamp_expression="e.event_timestamp_utc")
    query = f"""
        SELECT s.symbol, i.interval, et.event_type, e.event_timestamp_utc,
               e.event_key, e.event_json, first_run.run_id, last_run.run_id,
               src.source_file, hex(src.source_sha256)
        FROM events e
        JOIN symbols s ON s.symbol_id=e.symbol_id
        JOIN intervals i ON i.interval_id=e.interval_id
        JOIN event_types et ON et.event_type_id=e.event_type_id
        JOIN run_ids first_run ON first_run.run_key=e.first_seen_run_key
        JOIN run_ids last_run ON last_run.run_key=e.last_seen_run_key
        JOIN sources src ON src.source_id=e.source_id
        {where}
        ORDER BY s.symbol COLLATE BINARY, i.interval COLLATE BINARY,
                 e.event_timestamp_utc, et.event_type COLLATE BINARY, e.event_key COLLATE BINARY
    """
    for row in connection.execute(query, params):
        yield (row[0], row[1], row[2], epoch_to_datetime(row[3]), *row[4:9], str(row[9]).lower())


def bar_revision_rows(connection: sqlite3.Connection, filters: ExportFilters) -> Iterator[Sequence[Any]]:
    where, params = where_clause(filters, timestamp_expression="br.timestamp_utc")
    query = f"""
        SELECT br.revision_id, r.run_id, s.symbol, i.interval, br.timestamp_utc,
               br.detected_at_utc, br.action, br.changed_fields_json,
               br.old_values_json, br.new_values_json
        FROM bar_revisions br
        JOIN run_ids r ON r.run_key=br.run_key
        JOIN symbols s ON s.symbol_id=br.symbol_id
        JOIN intervals i ON i.interval_id=br.interval_id
        {where}
        ORDER BY br.revision_id
    """
    for row in connection.execute(query, params):
        yield (row[0], row[1], row[2], row[3], epoch_to_datetime(row[4]), *row[5:])


def event_revision_rows(connection: sqlite3.Connection, filters: ExportFilters) -> Iterator[Sequence[Any]]:
    where, params = where_clause(filters, timestamp_expression="er.event_timestamp_utc")
    query = f"""
        SELECT er.revision_id, r.run_id, s.symbol, i.interval, et.event_type,
               er.event_timestamp_utc, er.detected_at_utc, er.action,
               er.old_event_json, er.new_event_json
        FROM event_revisions er
        JOIN run_ids r ON r.run_key=er.run_key
        JOIN symbols s ON s.symbol_id=er.symbol_id
        JOIN intervals i ON i.interval_id=er.interval_id
        JOIN event_types et ON et.event_type_id=er.event_type_id
        {where}
        ORDER BY er.revision_id
    """
    for row in connection.execute(query, params):
        yield (row[0], row[1], row[2], row[3], row[4], epoch_to_datetime(row[5]), *row[6:])


def column_name(index: int) -> str:
    value = index + 1
    result = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def xml_text(value: str) -> str:
    def valid_xml_character(character: str) -> bool:
        codepoint = ord(character)
        return (
            character in "\t\n\r"
            or 0x20 <= codepoint <= 0xD7FF
            or 0xE000 <= codepoint <= 0xFFFD
            or 0x10000 <= codepoint <= 0x10FFFF
        )

    cleaned = "".join(character for character in value if valid_xml_character(character))
    return escape(cleaned, {'"': '&quot;'})


def excel_serial(value: datetime) -> float:
    utc_value = value.astimezone(timezone.utc).replace(tzinfo=None)
    base = datetime(1899, 12, 30)
    delta = utc_value - base
    return delta.days + (delta.seconds + delta.microseconds / 1_000_000) / 86_400


def cell_xml(reference: str, value: Any, style: int = 0) -> str:
    if value is None:
        return ""
    style_attr = f' s="{style}"' if style else ""
    if isinstance(value, datetime):
        return f'<c r="{reference}"{style_attr}><v>{excel_serial(value):.12g}</v></c>'
    if isinstance(value, bool):
        return f'<c r="{reference}" t="b"{style_attr}><v>{1 if value else 0}</v></c>'
    if isinstance(value, int):
        return f'<c r="{reference}"{style_attr}><v>{value}</v></c>'
    if isinstance(value, float) and math.isfinite(value):
        return f'<c r="{reference}"{style_attr}><v>{value:.15g}</v></c>'
    text = str(value)
    preserve = ' xml:space="preserve"' if text[:1].isspace() or text[-1:].isspace() else ""
    return f'<c r="{reference}" t="inlineStr"{style_attr}><is><t{preserve}>{xml_text(text)}</t></is></c>'


class SheetBuilder:
    def __init__(
        self,
        temp_dir: Path,
        name: str,
        headers: Sequence[str],
        widths: Sequence[float],
        styles: Sequence[int],
    ) -> None:
        if len(headers) != len(widths) or len(headers) != len(styles):
            raise ExportError(f"Sheet definition lengths do not match for {name}.")
        self.name = name
        self.headers = list(headers)
        self.widths = list(widths)
        self.styles = list(styles)
        self.path = temp_dir / f"{name}.rows.xml"
        self.handle = self.path.open("w", encoding="utf-8", newline="\n")
        self.row_count = 0
        self.write_row(headers, header=True)

    def write_row(self, values: Sequence[Any], *, header: bool = False) -> None:
        if len(values) != len(self.headers):
            raise ExportError(f"Row for sheet {self.name} has {len(values)} columns; expected {len(self.headers)}.")
        self.row_count += 1
        row_number = self.row_count
        cells: list[str] = []
        for index, value in enumerate(values):
            style = 1 if header else self.styles[index]
            xml = cell_xml(f"{column_name(index)}{row_number}", value, style)
            if xml:
                cells.append(xml)
        self.handle.write(f'<row r="{row_number}">{"".join(cells)}</row>\n')

    def close(self) -> SheetInfo:
        self.handle.close()
        return SheetInfo(self.name, self.row_count, max(0, self.row_count - 1), len(self.headers))


def sheet_xml(builder: SheetBuilder) -> Iterator[bytes]:
    last_cell = f"{column_name(len(builder.headers) - 1)}{max(1, builder.row_count)}"
    cols = "".join(
        f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>'
        for index, width in enumerate(builder.widths, start=1)
    )
    prefix = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="A1:{last_cell}"/>'
        '<sheetViews><sheetView workbookViewId="0">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        '<selection pane="bottomLeft" activeCell="A2" sqref="A2"/>'
        '</sheetView></sheetViews><sheetFormatPr defaultRowHeight="15"/>'
        f'<cols>{cols}</cols><sheetData>'
    )
    yield prefix.encode("utf-8")
    with builder.path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            yield chunk
    suffix = f'</sheetData><autoFilter ref="A1:{last_cell}"/></worksheet>'
    yield suffix.encode("utf-8")


def add_stream_to_zip(archive: zipfile.ZipFile, arcname: str, chunks: Iterable[bytes]) -> None:
    with archive.open(arcname, "w") as target:
        for chunk in chunks:
            target.write(chunk)


def styles_xml() -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="2"><numFmt numFmtId="164" formatCode="yyyy-mm-dd hh:mm:ss"/><numFmt numFmtId="165" formatCode="0.##########"/></numFmts>
<fonts count="2"><font><sz val="11"/><name val="Calibri"/><family val="2"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/><family val="2"/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF1F4E78"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="5">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFill="1" applyFont="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="1" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
<dxfs count="0"/><tableStyles count="0" defaultTableStyle="TableStyleMedium2" defaultPivotStyle="PivotStyleLight16"/>
</styleSheet>'''


def workbook_xml(sheet_names: Sequence[str]) -> str:
    sheets = "".join(
        f'<sheet name="{xml_text(name)}" sheetId="{index}" r:id="rId{index}"/>'
        for index, name in enumerate(sheet_names, start=1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<bookViews><workbookView xWindow="0" yWindow="0" windowWidth="24000" windowHeight="12000"/></bookViews>'
        f'<sheets>{sheets}</sheets><calcPr calcId="191029" fullCalcOnLoad="1"/></workbook>'
    )


def workbook_rels_xml(sheet_count: int) -> str:
    relationships = "".join(
        f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, sheet_count + 1)
    )
    relationships += (
        f'<Relationship Id="rId{sheet_count + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'{relationships}</Relationships>'
    )


def content_types_xml(sheet_count: int) -> str:
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, sheet_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        f'{overrides}</Types>'
    )


def package_rels_xml() -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>'''


def core_xml(created: datetime) -> str:
    stamp = created.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<dc:title>Yahoo Long-History Compact Export</dc:title><dc:creator>YahooFinanceAPI-Reference</dc:creator><cp:lastModifiedBy>YahooFinanceAPI-Reference</cp:lastModifiedBy>
<dcterms:created xsi:type="dcterms:W3CDTF">{stamp}</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">{stamp}</dcterms:modified></cp:coreProperties>'''


def app_xml(sheet_names: Sequence[str]) -> str:
    titles = "".join(f'<vt:lpstr>{xml_text(name)}</vt:lpstr>' for name in sheet_names)
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
<Application>YahooFinanceAPI-Reference</Application><DocSecurity>0</DocSecurity><ScaleCrop>false</ScaleCrop>
<HeadingPairs><vt:vector size="2" baseType="variant"><vt:variant><vt:lpstr>Worksheets</vt:lpstr></vt:variant><vt:variant><vt:i4>{len(sheet_names)}</vt:i4></vt:variant></vt:vector></HeadingPairs>
<TitlesOfParts><vt:vector size="{len(sheet_names)}" baseType="lpstr">{titles}</vt:vector></TitlesOfParts><Company></Company><LinksUpToDate>false</LinksUpToDate><SharedDoc>false</SharedDoc><HyperlinksChanged>false</HyperlinksChanged><AppVersion>16.0300</AppVersion></Properties>'''


def write_xlsx(path: Path, builders: Sequence[SheetBuilder], created: datetime) -> list[SheetInfo]:
    infos = [builder.close() for builder in builders]
    names = [info.name for info in infos]
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
        archive.writestr("[Content_Types].xml", content_types_xml(len(builders)))
        archive.writestr("_rels/.rels", package_rels_xml())
        archive.writestr("docProps/core.xml", core_xml(created))
        archive.writestr("docProps/app.xml", app_xml(names))
        archive.writestr("xl/workbook.xml", workbook_xml(names))
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml(len(builders)))
        archive.writestr("xl/styles.xml", styles_xml())
        for index, builder in enumerate(builders, start=1):
            add_stream_to_zip(archive, f"xl/worksheets/sheet{index}.xml", sheet_xml(builder))
    with zipfile.ZipFile(path, "r") as archive:
        bad = archive.testzip()
        if bad is not None:
            raise ExportError(f"Generated XLSX failed ZIP verification at {bad}.")
    return infos


def split_rows_into_sheets(
    temp_dir: Path,
    base_name: str,
    headers: Sequence[str],
    widths: Sequence[float],
    styles: Sequence[int],
    rows: Iterable[Sequence[Any]],
    max_data_rows: int,
) -> list[SheetBuilder]:
    builders: list[SheetBuilder] = []
    current: SheetBuilder | None = None
    for row in rows:
        if current is None or current.row_count - 1 >= max_data_rows:
            name = base_name if not builders else f"{base_name}_{len(builders) + 1:03d}"
            current = SheetBuilder(temp_dir, name, headers, widths, styles)
            builders.append(current)
        current.write_row(row)
    if current is None:
        current = SheetBuilder(temp_dir, base_name, headers, widths, styles)
        builders.append(current)
    return builders


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_report(path: Path, manifest: Mapping[str, Any]) -> None:
    counts = manifest["source_counts"]
    lines = [
        "Yahoo Long-History Compact XLSX Export",
        f"Utility version: {manifest['utility_version']}",
        f"Started UTC: {manifest['started_at_utc']}",
        f"Completed UTC: {manifest['completed_at_utc']}",
        f"Source database file: {manifest['source_database_file']}",
        f"Database resolution: {manifest['database_resolution']}",
        f"Source database unchanged: {manifest['source_database_unchanged']}",
        f"Workbook: {manifest['workbook_file']}",
        f"Workbook bytes: {manifest['workbook_bytes']}",
        f"Workbook SHA-256: {manifest['workbook_sha256']}",
        f"Symbols selected: {counts['symbols']}",
        f"Bars exported: {counts['bars']}",
        f"Events exported: {counts['events']}",
        f"Bar revisions exported: {counts['bar_revisions']}",
        f"Event revisions exported: {counts['event_revisions']}",
        f"Quick check: {manifest['quick_check']}",
        "",
        "Sheets:",
    ]
    lines.extend(
        f"- {sheet['name']}: {sheet['data_rows']} data row(s), {sheet['columns']} column(s)"
        for sheet in manifest["sheets"]
    )
    lines.extend([
        "",
        "Safety conclusion",
        "- The verified compact database was opened read-only with PRAGMA query_only=ON.",
        "- The legacy history.sqlite database was not opened or changed.",
        "- Only a new external export folder and workbook were written.",
        "- No database promotion, replacement, rename, move, or deletion was performed.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def export_workbook(
    connection: sqlite3.Connection,
    output_path: Path,
    filters: ExportFilters,
    max_data_rows: int,
    started_at: datetime,
    database_resolution: str,
    source_database_file: str,
) -> tuple[list[SheetInfo], dict[str, int]]:
    counts = source_counts(connection, filters)
    with tempfile.TemporaryDirectory(prefix=".history-xlsx-", dir=output_path.parent) as temp_name:
        temp_dir = Path(temp_name)
        builders: list[SheetBuilder] = []

        summary = SheetBuilder(temp_dir, "Summary", ("Field", "Value"), (34, 72), (0, 0))
        summary_rows = [
            ("Utility version", UTILITY_VERSION),
            ("Created UTC", format_utc(started_at)),
            ("Source database file", source_database_file),
            ("Database resolution", database_resolution),
            ("Schema", f"{COMPACT_SCHEMA_NAME} v{COMPACT_SCHEMA_VERSION}"),
            ("Symbols selected", counts["symbols"]),
            ("Intervals", ", ".join(filters.intervals) if filters.intervals else "all"),
            ("Start UTC", format_utc(epoch_to_datetime(filters.start_epoch)) if filters.start_epoch is not None else "earliest"),
            ("Through UTC date", format_utc(epoch_to_datetime(filters.end_epoch_exclusive - 1)) if filters.end_epoch_exclusive is not None else "latest"),
            ("Bars", counts["bars"]),
            ("Events", counts["events"]),
            ("Include revisions", filters.include_revisions),
            ("Bar revisions", counts["bar_revisions"]),
            ("Event revisions", counts["event_revisions"]),
            ("Safety", "Read-only source; no network; new external export only"),
        ]
        for row in summary_rows:
            summary.write_row(row)
        builders.append(summary)

        builders.extend(split_rows_into_sheets(
            temp_dir, "Symbols",
            ("Symbol", "Interval", "Bars", "First Bar UTC", "Last Bar UTC", "Events", "Full Refresh Required", "Full Refresh Reason", "Last Checked UTC"),
            (18, 12, 12, 22, 22, 12, 20, 36, 24),
            (0, 0, 3, 2, 2, 3, 0, 0, 0),
            symbol_summary_rows(connection, filters), max_data_rows,
        ))
        builders.extend(split_rows_into_sheets(
            temp_dir, "Bars",
            ("Symbol", "Interval", "Timestamp UTC", "Open", "High", "Low", "Close", "Adjusted Close", "Volume", "First Seen Run", "Last Seen Run", "Source File", "Source SHA-256"),
            (18, 10, 22, 14, 14, 14, 14, 16, 14, 30, 30, 52, 66),
            (0, 0, 2, 4, 4, 4, 4, 4, 3, 0, 0, 0, 0),
            bars_rows(connection, filters), max_data_rows,
        ))
        builders.extend(split_rows_into_sheets(
            temp_dir, "Events",
            ("Symbol", "Interval", "Event Type", "Event Timestamp UTC", "Event Key", "Event JSON", "First Seen Run", "Last Seen Run", "Source File", "Source SHA-256"),
            (18, 10, 16, 22, 36, 72, 30, 30, 52, 66),
            (0, 0, 0, 2, 0, 0, 0, 0, 0, 0),
            events_rows(connection, filters), max_data_rows,
        ))
        if filters.include_revisions:
            builders.extend(split_rows_into_sheets(
                temp_dir, "BarRevisions",
                ("Revision ID", "Run ID", "Symbol", "Interval", "Timestamp UTC", "Detected UTC", "Action", "Changed Fields JSON", "Old Values JSON", "New Values JSON"),
                (14, 30, 18, 10, 22, 24, 16, 36, 60, 60),
                (3, 0, 0, 0, 2, 0, 0, 0, 0, 0),
                bar_revision_rows(connection, filters), max_data_rows,
            ))
            builders.extend(split_rows_into_sheets(
                temp_dir, "EventRevisions",
                ("Revision ID", "Run ID", "Symbol", "Interval", "Event Type", "Event Timestamp UTC", "Detected UTC", "Action", "Old Event JSON", "New Event JSON"),
                (14, 30, 18, 10, 16, 22, 24, 16, 60, 60),
                (3, 0, 0, 0, 0, 2, 0, 0, 0, 0),
                event_revision_rows(connection, filters), max_data_rows,
            ))
        infos = write_xlsx(output_path, builders, started_at)
    return infos, counts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, help="Explicit verified history_compact.sqlite path.")
    parser.add_argument("--rebuild-dir", type=Path, help="Compact rebuild folder containing history_compact.sqlite.")
    parser.add_argument("--output-dir", type=Path, help="New external output folder. Defaults under long-history/exports.")
    parser.add_argument("--output-name", default="Yahoo_Long_History.xlsx", help="Workbook filename; .xlsx is added when omitted.")
    parser.add_argument("--symbols", help="Comma-separated symbols. Default: all symbols.")
    parser.add_argument("--symbols-file", type=Path, help="CSV or one-column text file of symbols.")
    parser.add_argument("--interval", action="append", help="Interval to export; repeat for more than one. Default: all.")
    parser.add_argument("--start-date", help="Inclusive UTC date, YYYY-MM-DD.")
    parser.add_argument("--through-date", help="Inclusive UTC date, YYYY-MM-DD.")
    parser.add_argument("--include-revisions", action="store_true", help="Include bar and event revision sheets.")
    parser.add_argument("--smoke", action="store_true", help="Export the first five selected symbols.")
    parser.add_argument("--dry-run", action="store_true", help="Resolve and count only; write no export files.")
    parser.add_argument(
        "--max-data-rows-per-sheet", type=int, default=DEFAULT_MAX_DATA_ROWS_PER_SHEET,
        help=f"Split data sheets after this many rows (1 to {EXCEL_MAX_ROWS - 1}).",
    )
    return parser


def run_export(args: argparse.Namespace) -> tuple[Path | None, dict[str, Any]]:
    if not 1 <= args.max_data_rows_per_sheet <= EXCEL_MAX_ROWS - 1:
        raise ExportError(f"--max-data-rows-per-sheet must be from 1 through {EXCEL_MAX_ROWS - 1}.")
    started_at = utc_now()
    database_path, resolution, archive_root = resolve_compact_database(args.database, args.rebuild_dir)
    before = fingerprint_files(database_path)
    connection = connect_read_only(database_path)
    try:
        meta = validate_compact_schema(connection)
        filters = resolve_filters(connection, args)
        counts = source_counts(connection, filters)
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        if quick_check != "ok":
            raise ExportError(f"Compact database quick_check failed: {quick_check}")
        if args.dry_run:
            after = fingerprint_files(database_path)
            unchanged = main_database_unchanged(before, after)
            if not unchanged:
                raise ExportError("Source compact database changed during dry-run inspection.")
            plan = {
                "utility_version": UTILITY_VERSION,
                "source_database_file": database_path.name,
                "database_resolution": resolution,
                "schema_name": meta.get("schema_name"),
                "schema_version": meta.get("schema_version"),
                "build_status": meta.get("build_status"),
                "filters": asdict(filters),
                "source_counts": counts,
                "max_data_rows_per_sheet": args.max_data_rows_per_sheet,
                "network_requests_sent": 0,
                "files_written": 0,
                "source_database_unchanged": True,
            }
            return None, plan

        output_dir = args.output_dir or (
            archive_root / "exports" / f"{filename_utc(started_at)}_compact-xlsx-export"
        )
        output_dir = validate_new_output_directory(output_dir)
        output_name = args.output_name.strip()
        if not output_name:
            raise ExportError("--output-name must not be blank.")
        if not output_name.casefold().endswith(".xlsx"):
            output_name += ".xlsx"
        if Path(output_name).name != output_name:
            raise ExportError("--output-name must be a filename, not a path.")
        workbook_path = output_dir / output_name
        infos, counts = export_workbook(
            connection, workbook_path, filters, args.max_data_rows_per_sheet,
            started_at, resolution, database_path.name,
        )
    finally:
        connection.close()

    completed_at = utc_now()
    after = fingerprint_files(database_path)
    unchanged = main_database_unchanged(before, after)
    if not unchanged:
        raise ExportError("Source compact database changed during export.")
    manifest: dict[str, Any] = {
        "utility_version": UTILITY_VERSION,
        "started_at_utc": format_utc(started_at),
        "completed_at_utc": format_utc(completed_at),
        "source_database_file": database_path.name,
        "database_resolution": resolution,
        "schema_name": meta.get("schema_name"),
        "schema_version": meta.get("schema_version"),
        "build_status": meta.get("build_status"),
        "source_database_unchanged": unchanged,
        "quick_check": quick_check,
        "filters": asdict(filters),
        "source_counts": counts,
        "workbook_file": workbook_path.name,
        "workbook_bytes": workbook_path.stat().st_size,
        "workbook_sha256": sha256_file(workbook_path),
        "sheets": [asdict(info) for info in infos],
        "network_requests_sent": 0,
        "legacy_database_opened": False,
        "database_files_moved_or_deleted": False,
    }
    (output_dir / "export-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    write_report(output_dir / "export-report.txt", manifest)
    return output_dir, manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        output_dir, manifest = run_export(args)
    except (ExportError, OSError, sqlite3.Error, zipfile.BadZipFile) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.dry_run:
        print(json.dumps(manifest, indent=2, sort_keys=True))
    else:
        print(f"Verified compact database: {manifest['source_database_file']} ({manifest['database_resolution']})")
        print(f"Export folder: {output_dir}")
        print(f"Workbook: {manifest['workbook_file']}")
        print(f"Bars exported: {manifest['source_counts']['bars']}")
        print(f"Events exported: {manifest['source_counts']['events']}")
        print(f"Source compact database unchanged: {manifest['source_database_unchanged']}")
        print("Overall verification: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
