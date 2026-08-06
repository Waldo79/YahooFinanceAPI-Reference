#!/usr/bin/env python3
"""Incrementally synchronize a verified compact Yahoo long-history database.

Version 0.1.0-candidate.10. By default this utility copies the verified
``history_compact.sqlite`` to a separate validation folder and applies a small
incremental synchronization to the copy. The source compact database is opened
read-only and fingerprinted before and after the validation. Direct in-place
updates require both ``--in-place`` and ``--acknowledge-in-place-update``.

The utility reuses the established Yahoo Chart request, retry, parsing, raw
capture, and redaction code from ``yahoo_history_capture.py``. It makes no
changes to the legacy ``history.sqlite`` database.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import importlib.util
import json
import math
import os
import shutil
import sqlite3
import sys
import threading
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.request import urlopen

UTILITY_VERSION = "0.1.0-candidate.10"
COMPACT_SCHEMA_NAME = "compact_long_history"
COMPACT_SCHEMA_VERSION = "1"
COMPACT_DATABASE_FILENAME = "history_compact.sqlite"
VALIDATION_DATABASE_FILENAME = "history_compact_validation.sqlite"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _load_adjacent(name: str, filename: str) -> Any:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load required dependency: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


history = _load_adjacent("_compact_history_capture_dependency", "yahoo_history_capture.py")
rebuild = _load_adjacent("_compact_rebuild_dependency", "history_sqlite_compact_rebuild.py")
fast = history.fast

HistoryInputError = history.HistoryInputError
YahooSessionError = history.YahooSessionError


class CompactUpdateError(RuntimeError):
    """Raised when a compact-schema update cannot safely continue."""


@dataclass(frozen=True)
class FileFingerprint:
    name: str
    exists: bool
    bytes: int
    modified_time_ns: int


@dataclass
class CompactLookups:
    symbols: dict[str, int]
    intervals: dict[str, int]
    runs: dict[str, int]
    event_types: dict[str, int]
    sources: dict[tuple[str, str], int]


@dataclass
class CompactRunState:
    archive_root: Path
    run_dir: Path
    database_path: Path
    checkpoint_path: Path
    checkpoint_lock: threading.Lock
    source_database_path: Path
    validation_copy: bool


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
        raise CompactUpdateError(f"Compact database is missing required tables: {', '.join(missing)}")
    meta = read_compact_meta(connection)
    if meta.get("schema_name") != COMPACT_SCHEMA_NAME:
        raise CompactUpdateError(
            f"Unexpected compact schema name: {meta.get('schema_name')!r}; expected {COMPACT_SCHEMA_NAME!r}."
        )
    if meta.get("schema_version") != COMPACT_SCHEMA_VERSION:
        raise CompactUpdateError(
            f"Unsupported compact schema version: {meta.get('schema_version')!r}; expected {COMPACT_SCHEMA_VERSION!r}."
        )
    if require_verified and meta.get("build_status") not in {"VERIFIED_COMPLETE", "ACTIVE_COMPACT"}:
        raise CompactUpdateError(
            f"Compact database is not verified for updates; build_status={meta.get('build_status')!r}."
        )
    return meta


def connect_read_only(database_path: Path) -> sqlite3.Connection:
    resolved = normalize_path(database_path)
    if path_is_within(resolved, REPOSITORY_ROOT):
        raise CompactUpdateError(f"Compact history databases must remain outside the repository: {resolved}")
    if not resolved.is_file():
        raise CompactUpdateError(f"Compact database does not exist: {resolved}")
    connection = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA foreign_keys=ON")
    validate_compact_schema(connection)
    return connection


def connect_writable(database_path: Path) -> sqlite3.Connection:
    resolved = normalize_path(database_path)
    if path_is_within(resolved, REPOSITORY_ROOT):
        raise CompactUpdateError(f"Compact history databases must remain outside the repository: {resolved}")
    if not resolved.is_file():
        raise CompactUpdateError(f"Compact database does not exist: {resolved}")
    connection = sqlite3.connect(resolved)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA temp_store=MEMORY")
    connection.execute("PRAGMA cache_size=-131072")
    validate_compact_schema(connection)
    return connection


def compact_database_candidates(archive_root: Path) -> list[Path]:
    root = normalize_path(archive_root)
    candidates = list((root / "compact-rebuilds").glob(f"*/{COMPACT_DATABASE_FILENAME}"))
    verified: list[Path] = []
    for candidate in candidates:
        try:
            con = sqlite3.connect(candidate.as_uri() + "?mode=ro", uri=True)
            try:
                con.row_factory = sqlite3.Row
                validate_compact_schema(con)
            finally:
                con.close()
            verified.append(candidate)
        except (sqlite3.Error, CompactUpdateError, OSError):
            continue
    return sorted(verified, key=lambda path: path.stat().st_mtime_ns, reverse=True)


def resolve_compact_database(explicit: Path | None, rebuild_dir: Path | None) -> tuple[Path, str, Path]:
    if explicit is not None and rebuild_dir is not None:
        raise CompactUpdateError("Use either --database or --rebuild-dir, not both.")
    archive_root, archive_source = history.resolve_output_root(None, config_path=history.LOCAL_CONFIG_FILE)
    if explicit is not None:
        path = normalize_path(explicit)
        source = "command_line_database"
    elif rebuild_dir is not None:
        path = normalize_path(rebuild_dir) / COMPACT_DATABASE_FILENAME
        source = "command_line_rebuild_dir"
    else:
        candidates = compact_database_candidates(archive_root)
        if not candidates:
            raise CompactUpdateError(
                f"No verified {COMPACT_DATABASE_FILENAME} was found under {archive_root / 'compact-rebuilds'}."
            )
        path = candidates[0]
        source = f"latest_verified_compact_rebuild:{archive_source}"
    con = connect_read_only(path)
    con.close()
    return path, source, archive_root


def sqlite_backup_copy(source: Path, target: Path) -> None:
    if target.exists():
        raise CompactUpdateError(f"Validation database already exists: {target}")
    source_con = connect_read_only(source)
    try:
        target.parent.mkdir(parents=True, exist_ok=False)
        target_con = sqlite3.connect(target)
        try:
            source_con.backup(target_con, pages=4096)
            target_con.commit()
        finally:
            target_con.close()
    finally:
        source_con.close()


def create_run_state(
    source_database: Path,
    archive_root: Path,
    *,
    started_at: datetime,
    in_place: bool,
    resume_run: Path | None = None,
) -> CompactRunState:
    source_database = normalize_path(source_database)
    if resume_run is not None:
        run_dir = normalize_path(resume_run)
        if path_is_within(run_dir, REPOSITORY_ROOT):
            raise CompactUpdateError(f"Compact update runs must remain outside the repository: {run_dir}")
        if not run_dir.is_dir() or not (run_dir / "checkpoint.jsonl").is_file():
            raise CompactUpdateError(f"Resume folder is missing checkpoint.jsonl: {run_dir}")
        info_path = run_dir / "compact-update-plan.json"
        if not info_path.is_file():
            raise CompactUpdateError(f"Resume folder is missing compact-update-plan.json: {run_dir}")
        plan = json.loads(info_path.read_text(encoding="utf-8"))
        database_path = run_dir / VALIDATION_DATABASE_FILENAME if plan.get("validation_copy") else source_database
        return CompactRunState(
            archive_root=archive_root,
            run_dir=run_dir,
            database_path=database_path,
            checkpoint_path=run_dir / "checkpoint.jsonl",
            checkpoint_lock=threading.Lock(),
            source_database_path=source_database,
            validation_copy=bool(plan.get("validation_copy")),
        )

    if in_place:
        run_dir = source_database.parent / "incremental-runs" / f"{filename_utc(started_at)}_compact-history-run"
        run_dir.mkdir(parents=True, exist_ok=False)
        database_path = source_database
        validation_copy = False
    else:
        run_dir = source_database.parent / "incremental-validations" / f"{filename_utc(started_at)}_compact-sync-validation"
        sqlite_backup_copy(source_database, run_dir / VALIDATION_DATABASE_FILENAME)
        database_path = run_dir / VALIDATION_DATABASE_FILENAME
        validation_copy = True
    return CompactRunState(
        archive_root=archive_root,
        run_dir=run_dir,
        database_path=database_path,
        checkpoint_path=run_dir / "checkpoint.jsonl",
        checkpoint_lock=threading.Lock(),
        source_database_path=source_database,
        validation_copy=validation_copy,
    )


def _lookup_table(connection: sqlite3.Connection, table: str, id_column: str, value_column: str) -> dict[str, int]:
    return {str(row[value_column]): int(row[id_column]) for row in connection.execute(f"SELECT {id_column}, {value_column} FROM {table}")}


def load_lookups(connection: sqlite3.Connection) -> CompactLookups:
    source_rows = connection.execute("SELECT source_id, source_file, source_sha256 FROM sources")
    return CompactLookups(
        symbols=_lookup_table(connection, "symbols", "symbol_id", "symbol"),
        intervals=_lookup_table(connection, "intervals", "interval_id", "interval"),
        runs=_lookup_table(connection, "run_ids", "run_key", "run_id"),
        event_types=_lookup_table(connection, "event_types", "event_type_id", "event_type"),
        sources={
            (str(row["source_file"]), rebuild.blob_to_sha(row["source_sha256"]) or ""): int(row["source_id"])
            for row in source_rows
        },
    )


def ensure_lookup(
    connection: sqlite3.Connection,
    cache: dict[str, int],
    *,
    table: str,
    id_column: str,
    value_column: str,
    value: str,
) -> int:
    if value in cache:
        return cache[value]
    connection.execute(f"INSERT OR IGNORE INTO {table}({value_column}) VALUES(?)", (value,))
    row = connection.execute(
        f"SELECT {id_column} FROM {table} WHERE {value_column}=?", (value,)
    ).fetchone()
    if row is None:
        raise CompactUpdateError(f"Could not resolve {table}.{value_column}={value!r}")
    cache[value] = int(row[0])
    return cache[value]


def ensure_symbol(connection: sqlite3.Connection, lookups: CompactLookups, symbol: str) -> int:
    return ensure_lookup(connection, lookups.symbols, table="symbols", id_column="symbol_id", value_column="symbol", value=symbol)


def ensure_interval(connection: sqlite3.Connection, lookups: CompactLookups, interval: str) -> int:
    return ensure_lookup(connection, lookups.intervals, table="intervals", id_column="interval_id", value_column="interval", value=interval)


def ensure_run(connection: sqlite3.Connection, lookups: CompactLookups, run_id: str) -> int:
    return ensure_lookup(connection, lookups.runs, table="run_ids", id_column="run_key", value_column="run_id", value=run_id)


def ensure_event_type(connection: sqlite3.Connection, lookups: CompactLookups, event_type: str) -> int:
    return ensure_lookup(
        connection, lookups.event_types, table="event_types", id_column="event_type_id",
        value_column="event_type", value=event_type,
    )


def ensure_source(
    connection: sqlite3.Connection,
    lookups: CompactLookups,
    source_file: str,
    source_sha256: str,
) -> int:
    key = (source_file, source_sha256)
    if key in lookups.sources:
        return lookups.sources[key]
    blob = rebuild.sha_to_blob(source_sha256)
    connection.execute(
        "INSERT OR IGNORE INTO sources(source_file, source_sha256) VALUES(?, ?)",
        (source_file, blob),
    )
    row = connection.execute(
        "SELECT source_id FROM sources WHERE source_file=? AND source_sha256=?",
        (source_file, blob),
    ).fetchone()
    if row is None:
        raise CompactUpdateError(f"Could not resolve source provenance for {source_file}")
    lookups.sources[key] = int(row[0])
    return lookups.sources[key]


def compact_state_for_symbols(
    database_path: Path,
    symbols: Sequence[str],
    interval: str,
) -> dict[str, dict[str, Any]]:
    if not symbols:
        return {}
    connection = connect_read_only(database_path)
    try:
        placeholders = ",".join("?" for _ in symbols)
        query = f"""
            SELECT s.symbol, i.interval, st.last_bar_timestamp, st.last_checked_at_utc,
                   lsr.run_id AS last_success_run_id, br.run_id AS baseline_run_id,
                   st.full_refresh_required, st.full_refresh_reason
            FROM symbol_state st
            JOIN symbols s ON s.symbol_id=st.symbol_id
            JOIN intervals i ON i.interval_id=st.interval_id
            LEFT JOIN run_ids lsr ON lsr.run_key=st.last_success_run_key
            LEFT JOIN run_ids br ON br.run_key=st.baseline_run_key
            WHERE i.interval=? AND s.symbol IN ({placeholders})
        """
        return {str(row["symbol"]): dict(row) for row in connection.execute(query, (interval, *symbols))}
    finally:
        connection.close()


def build_tasks(
    symbols: Sequence[str],
    *,
    mode: str,
    interval: str,
    overlap_days: int,
    request_end_epoch: int,
    database_path: Path,
) -> list[Any]:
    if mode not in history.VALID_MODES:
        raise CompactUpdateError(f"Unsupported mode: {mode}")
    states = compact_state_for_symbols(database_path, symbols, interval)
    selected = list(symbols)
    if mode == "refresh-flagged":
        selected = [s for s in symbols if s in states and bool(states[s]["full_refresh_required"])]
    tasks: list[Any] = []
    for sequence, symbol in enumerate(selected, start=1):
        state = states.get(symbol)
        prior_latest = int(state["last_bar_timestamp"]) if state and state["last_bar_timestamp"] is not None else None
        prior_flag = bool(state["full_refresh_required"]) if state else False
        full_range = mode in {"baseline", "refresh-flagged"} or prior_latest is None
        start_epoch = None if full_range else max(0, prior_latest - overlap_days * 86400)
        effective_mode = "baseline-fallback" if mode == "sync" and prior_latest is None else mode
        tasks.append(history.HistoryTask(
            task_key=f"compact-history-{sequence:06d}-{fast.safe_filename(symbol)}",
            task_sequence=sequence,
            symbol=symbol,
            interval=interval,
            mode=effective_mode,
            full_range=full_range,
            request_start_epoch=start_epoch,
            request_end_epoch=request_end_epoch,
            prior_latest_epoch=prior_latest,
            prior_full_refresh_required=prior_flag,
        ))
    return tasks


def _bar_values(bar: Any) -> dict[str, Any]:
    keys = ("open", "high", "low", "close", "adjclose", "volume")
    return {key: getattr(bar, key) if hasattr(bar, key) else bar[key] for key in keys}


def _numbers_equal(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(float(left), float(right), rel_tol=1e-12, abs_tol=1e-12)
    return left == right


def _changed_fields(old_values: Mapping[str, Any], new_values: Mapping[str, Any]) -> list[str]:
    return [key for key in old_values if not _numbers_equal(old_values[key], new_values[key])]


def record_bar_revision(
    connection: sqlite3.Connection,
    *,
    run_key: int,
    symbol_id: int,
    interval_id: int,
    timestamp: int,
    action: str,
    changed_fields: Sequence[str],
    old_values: Mapping[str, Any] | None,
    new_values: Mapping[str, Any] | None,
    detected_at_utc: str,
) -> None:
    connection.execute(
        """
        INSERT INTO bar_revisions(
            run_key, symbol_id, interval_id, timestamp_utc, detected_at_utc,
            action, changed_fields_json, old_values_json, new_values_json
        ) VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            run_key, symbol_id, interval_id, timestamp, detected_at_utc, action,
            json.dumps(list(changed_fields), separators=(",", ":")),
            json.dumps(dict(old_values), sort_keys=True, separators=(",", ":")) if old_values is not None else None,
            json.dumps(dict(new_values), sort_keys=True, separators=(",", ":")) if new_values is not None else None,
        ),
    )


def apply_parsed_history_compact(
    connection: sqlite3.Connection,
    lookups: CompactLookups,
    *,
    run_id: str,
    task: Any,
    parsed: Any,
    source_file: str,
    source_sha256: str,
    detected_at_utc: str,
) -> Any:
    stats = history.ApplyStats(bars_returned=len(parsed.bars), events_returned=len(parsed.events))
    if parsed.classification != "SUCCESS_HISTORY_RETURNED":
        return stats
    symbol_id = ensure_symbol(connection, lookups, task.symbol)
    interval_id = ensure_interval(connection, lookups, task.interval)
    run_key = ensure_run(connection, lookups, run_id)
    source_id = ensure_source(connection, lookups, source_file, source_sha256)

    existing_rows = {
        int(row["timestamp_utc"]): row
        for row in connection.execute(
            "SELECT * FROM bars WHERE symbol_id=? AND interval_id=?", (symbol_id, interval_id)
        )
    }
    returned_timestamps: set[int] = set()
    adjustment_revision = False
    for bar in parsed.bars:
        returned_timestamps.add(bar.timestamp_utc)
        existing = existing_rows.get(bar.timestamp_utc)
        new_values = _bar_values(bar)
        if existing is None:
            connection.execute(
                """
                INSERT INTO bars(
                    symbol_id, interval_id, timestamp_utc, open, high, low, close,
                    adjclose, volume, first_seen_run_key, last_seen_run_key, source_id
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    symbol_id, interval_id, bar.timestamp_utc, bar.open, bar.high, bar.low,
                    bar.close, bar.adjclose, bar.volume, run_key, run_key, source_id,
                ),
            )
            stats.new_bars += 1
            continue
        old_values = _bar_values(existing)
        changed = _changed_fields(old_values, new_values)
        if changed:
            record_bar_revision(
                connection, run_key=run_key, symbol_id=symbol_id, interval_id=interval_id,
                timestamp=bar.timestamp_utc, action="REVISED", changed_fields=changed,
                old_values=old_values, new_values=new_values, detected_at_utc=detected_at_utc,
            )
            connection.execute(
                """
                UPDATE bars SET open=?, high=?, low=?, close=?, adjclose=?, volume=?,
                    last_seen_run_key=?, source_id=?
                WHERE symbol_id=? AND interval_id=? AND timestamp_utc=?
                """,
                (
                    bar.open, bar.high, bar.low, bar.close, bar.adjclose, bar.volume,
                    run_key, source_id, symbol_id, interval_id, bar.timestamp_utc,
                ),
            )
            stats.revised_bars += 1
            adjustment_revision = adjustment_revision or "adjclose" in changed
        else:
            connection.execute(
                """UPDATE bars SET last_seen_run_key=?, source_id=?
                   WHERE symbol_id=? AND interval_id=? AND timestamp_utc=?""",
                (run_key, source_id, symbol_id, interval_id, bar.timestamp_utc),
            )
            stats.unchanged_bars += 1

    if parsed.bars:
        coverage_start = min(bar.timestamp_utc for bar in parsed.bars)
        coverage_end = max(bar.timestamp_utc for bar in parsed.bars)
        if task.full_range:
            missing = [ts for ts in existing_rows if ts not in returned_timestamps]
        else:
            missing = [
                ts for ts in existing_rows
                if coverage_start <= ts <= coverage_end and ts not in returned_timestamps
            ]
        for timestamp in sorted(missing):
            record_bar_revision(
                connection, run_key=run_key, symbol_id=symbol_id, interval_id=interval_id,
                timestamp=timestamp, action="MISSING_FROM_REFRESH", changed_fields=(),
                old_values=_bar_values(existing_rows[timestamp]), new_values=None,
                detected_at_utc=detected_at_utc,
            )
        stats.missing_bars = len(missing)

    event_change = False
    for event in parsed.events:
        event_type_id = ensure_event_type(connection, lookups, event.event_type)
        existing = connection.execute(
            """SELECT * FROM events WHERE symbol_id=? AND interval_id=?
               AND event_type_id=? AND event_key=?""",
            (symbol_id, interval_id, event_type_id, event.event_key),
        ).fetchone()
        if existing is None:
            same_slot = connection.execute(
                """SELECT * FROM events WHERE symbol_id=? AND interval_id=? AND event_type_id=?
                   AND event_timestamp_utc=? ORDER BY event_key LIMIT 1""",
                (symbol_id, interval_id, event_type_id, event.event_timestamp_utc),
            ).fetchone()
            if same_slot is not None and same_slot["event_json"] != event.event_json:
                connection.execute(
                    """INSERT INTO event_revisions(
                        run_key, symbol_id, interval_id, event_type_id, event_timestamp_utc,
                        detected_at_utc, action, old_event_json, new_event_json
                    ) VALUES(?,?,?,?,?,?,'REVISED',?,?)""",
                    (
                        run_key, symbol_id, interval_id, event_type_id, event.event_timestamp_utc,
                        detected_at_utc, same_slot["event_json"], event.event_json,
                    ),
                )
                connection.execute(
                    "DELETE FROM events WHERE symbol_id=? AND interval_id=? AND event_type_id=? AND event_key=?",
                    (symbol_id, interval_id, event_type_id, same_slot["event_key"]),
                )
                connection.execute(
                    """INSERT INTO events(
                        symbol_id, interval_id, event_type_id, event_timestamp_utc, event_key,
                        event_json, first_seen_run_key, last_seen_run_key, source_id
                    ) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        symbol_id, interval_id, event_type_id, event.event_timestamp_utc,
                        event.event_key, event.event_json, same_slot["first_seen_run_key"],
                        run_key, source_id,
                    ),
                )
                stats.revised_events += 1
            else:
                connection.execute(
                    """INSERT INTO events(
                        symbol_id, interval_id, event_type_id, event_timestamp_utc, event_key,
                        event_json, first_seen_run_key, last_seen_run_key, source_id
                    ) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        symbol_id, interval_id, event_type_id, event.event_timestamp_utc,
                        event.event_key, event.event_json, run_key, run_key, source_id,
                    ),
                )
                stats.new_events += 1
            event_change = True
        elif existing["event_json"] != event.event_json:
            connection.execute(
                """INSERT INTO event_revisions(
                    run_key, symbol_id, interval_id, event_type_id, event_timestamp_utc,
                    detected_at_utc, action, old_event_json, new_event_json
                ) VALUES(?,?,?,?,?,?,'REVISED',?,?)""",
                (
                    run_key, symbol_id, interval_id, event_type_id, event.event_timestamp_utc,
                    detected_at_utc, existing["event_json"], event.event_json,
                ),
            )
            connection.execute(
                """UPDATE events SET event_json=?, last_seen_run_key=?, source_id=?
                   WHERE symbol_id=? AND interval_id=? AND event_type_id=? AND event_key=?""",
                (
                    event.event_json, run_key, source_id, symbol_id, interval_id,
                    event_type_id, event.event_key,
                ),
            )
            stats.revised_events += 1
            event_change = True
        else:
            connection.execute(
                """UPDATE events SET last_seen_run_key=?, source_id=?
                   WHERE symbol_id=? AND interval_id=? AND event_type_id=? AND event_key=?""",
                (run_key, source_id, symbol_id, interval_id, event_type_id, event.event_key),
            )
            stats.unchanged_events += 1

    reasons: list[str] = []
    if task.mode == "sync" and event_change:
        reasons.append("CORPORATE_ACTION_CHANGE")
    if task.mode == "sync" and adjustment_revision:
        reasons.append("ADJUSTED_HISTORY_CHANGE")
    if stats.missing_bars:
        reasons.append("BAR_MISSING_FROM_REFRESH")
    stats.full_refresh_required = bool(reasons)
    stats.full_refresh_reason = ",".join(reasons)
    return stats


def classify_http_result(http: Any, task: Any) -> Any:
    if http.http_status is None:
        return history.ParsedHistory(
            "NETWORK_OR_TIMEOUT_ERROR", None, [], [], {}, error_description=http.error_message
        )
    if 200 <= http.http_status < 300:
        return history.parse_chart_response(
            http.body, requested_symbol=task.symbol, requested_interval=task.interval
        )
    description = http.error_message
    code = None
    try:
        payload = json.loads((http.body or b"").decode("utf-8"))
        error = payload.get("chart", {}).get("error") if isinstance(payload, Mapping) else None
        if isinstance(error, Mapping):
            code = str(error.get("code")) if error.get("code") is not None else None
            description = str(error.get("description")) if error.get("description") is not None else description
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        pass
    text = (description or "").casefold()
    no_history_phrases = (
        "no data found", "no chart data", "data doesn't exist", "data does not exist",
        "symbol may be delisted", "possibly delisted", "not found for this symbol",
    )
    range_phrases = (
        "requested range", "range must be", "must be within the last",
        "data not available for starttime", "data not available for start time",
        "period1", "period2",
    )
    if http.http_status == 422 and any(phrase in text for phrase in no_history_phrases):
        classification = "NO_CHART_HISTORY_AVAILABLE"
    elif http.http_status == 422 and any(phrase in text for phrase in range_phrases):
        classification = "REQUEST_RANGE_NOT_SUPPORTED"
    elif code or description:
        classification = "YAHOO_ERROR_OBJECT"
    else:
        classification = f"HTTP_ERROR_{http.http_status}_UNCLASSIFIED"
    return history.ParsedHistory(
        classification, None, [], [], {}, error_code=code, error_description=description
    )


def source_file_for_run(run_state: CompactRunState, raw_relative: str) -> str:
    # Preserve a portable path relative to the compact rebuild folder.
    try:
        relative_run = run_state.run_dir.relative_to(run_state.source_database_path.parent)
        return (relative_run / raw_relative).as_posix()
    except ValueError:
        return f"{run_state.run_dir.name}/{raw_relative}"


def process_symbol_result(
    connection: sqlite3.Connection,
    lookups: CompactLookups,
    *,
    run_id: str,
    run_state: CompactRunState,
    task: Any,
    http: Any,
) -> Any:
    compatible_state = history.RunState(
        output_root=run_state.archive_root,
        run_dir=run_state.run_dir,
        database_path=run_state.database_path,
        checkpoint_path=run_state.checkpoint_path,
    )
    raw_file, raw_sha, compressed_sha, raw_bytes, compressed_bytes, metadata_file = history.write_raw_and_metadata(
        compatible_state, task, http
    )
    parsed = classify_http_result(http, task)
    detected_at = format_utc(utc_now())
    stats = history.ApplyStats()
    with connection:
        run_key = ensure_run(connection, lookups, run_id)
        symbol_id = ensure_symbol(connection, lookups, task.symbol)
        interval_id = ensure_interval(connection, lookups, task.interval)
        source_id: int | None = None
        portable_source: str | None = None
        if raw_file and raw_sha:
            portable_source = source_file_for_run(run_state, raw_file)
            source_id = ensure_source(connection, lookups, portable_source, raw_sha)
            stats = apply_parsed_history_compact(
                connection, lookups, run_id=run_id, task=task, parsed=parsed,
                source_file=portable_source, source_sha256=raw_sha, detected_at_utc=detected_at,
            )
        previous_state = connection.execute(
            "SELECT * FROM symbol_state WHERE symbol_id=? AND interval_id=?",
            (symbol_id, interval_id),
        ).fetchone()
        latest_row = connection.execute(
            "SELECT MAX(timestamp_utc) FROM bars WHERE symbol_id=? AND interval_id=?",
            (symbol_id, interval_id),
        ).fetchone()
        current_latest = latest_row[0] if latest_row else None
        success = parsed.classification in {
            "SUCCESS_HISTORY_RETURNED", "NO_HISTORY_DATA", "SYMBOL_NOT_AVAILABLE",
            "NO_CHART_HISTORY_AVAILABLE",
        }
        existing_flag = bool(previous_state["full_refresh_required"]) if previous_state else False
        existing_reason = str(previous_state["full_refresh_reason"]) if previous_state else ""
        if task.mode == "refresh-flagged" and success:
            final_flag = stats.full_refresh_required
            final_reason = stats.full_refresh_reason
        else:
            final_flag = existing_flag or stats.full_refresh_required
            reasons = [part for part in (existing_reason, stats.full_refresh_reason) if part]
            final_reason = ",".join(dict.fromkeys(",".join(reasons).split(","))) if reasons else ""
        baseline_key = previous_state["baseline_run_key"] if previous_state else None
        if baseline_key is None and task.full_range and success:
            baseline_key = run_key
        connection.execute(
            """
            INSERT INTO symbol_state(
                symbol_id, interval_id, last_bar_timestamp, last_checked_at_utc,
                last_success_run_key, baseline_run_key, full_refresh_required, full_refresh_reason
            ) VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(symbol_id, interval_id) DO UPDATE SET
                last_bar_timestamp=excluded.last_bar_timestamp,
                last_checked_at_utc=excluded.last_checked_at_utc,
                last_success_run_key=CASE WHEN ? THEN excluded.last_success_run_key ELSE symbol_state.last_success_run_key END,
                baseline_run_key=COALESCE(symbol_state.baseline_run_key, excluded.baseline_run_key),
                full_refresh_required=excluded.full_refresh_required,
                full_refresh_reason=excluded.full_refresh_reason
            """,
            (
                symbol_id, interval_id, current_latest, detected_at, run_key if success else None,
                baseline_key, int(final_flag), final_reason, int(success),
            ),
        )
        connection.execute(
            """
            INSERT OR REPLACE INTO symbol_runs(
                run_key, task_key, task_sequence, symbol_id, interval_id, mode, full_range,
                request_start_epoch, request_end_epoch, classification, http_status,
                bars_returned, new_bars, revised_bars, unchanged_bars, missing_bars,
                events_returned, new_events, revised_events, unchanged_events,
                full_refresh_required, full_refresh_reason, raw_source_id,
                raw_file_fallback, raw_sha256_fallback, elapsed_ms, attempts, error_description
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_key, task.task_key, task.task_sequence, symbol_id, interval_id, task.mode,
                int(task.full_range), task.request_start_epoch, task.request_end_epoch,
                parsed.classification, http.http_status, stats.bars_returned, stats.new_bars,
                stats.revised_bars, stats.unchanged_bars, stats.missing_bars,
                stats.events_returned, stats.new_events, stats.revised_events,
                stats.unchanged_events, int(final_flag), final_reason, source_id,
                None if source_id is not None else raw_file,
                None if source_id is not None else raw_sha,
                http.elapsed_ms, len(http.attempts), parsed.error_description,
            ),
        )
    return history.SymbolResult(
        task_key=task.task_key,
        task_sequence=task.task_sequence,
        symbol=task.symbol,
        interval=task.interval,
        mode=task.mode,
        full_range=task.full_range,
        request_start_epoch=task.request_start_epoch,
        request_end_epoch=task.request_end_epoch,
        classification=parsed.classification,
        http_status=http.http_status,
        returned_symbol=parsed.returned_symbol,
        bars_returned=stats.bars_returned,
        new_bars=stats.new_bars,
        revised_bars=stats.revised_bars,
        unchanged_bars=stats.unchanged_bars,
        missing_bars=stats.missing_bars,
        events_returned=stats.events_returned,
        new_events=stats.new_events,
        revised_events=stats.revised_events,
        unchanged_events=stats.unchanged_events,
        full_refresh_required=bool(final_flag),
        full_refresh_reason=final_reason,
        raw_file=raw_file,
        raw_uncompressed_sha256=raw_sha,
        raw_compressed_sha256=compressed_sha,
        raw_uncompressed_bytes=raw_bytes,
        raw_compressed_bytes=compressed_bytes,
        metadata_file=metadata_file,
        elapsed_ms=http.elapsed_ms,
        attempts=len(http.attempts),
        error_description=parsed.error_description,
    )


def append_checkpoint(run_state: CompactRunState, result: Any) -> None:
    payload = {
        "task_key": result.task_key,
        "symbol": result.symbol,
        "classification": result.classification,
        "http_status": result.http_status,
        "raw_file": result.raw_file,
        "completed_at_utc": format_utc(utc_now()),
    }
    with run_state.checkpoint_lock:
        with run_state.checkpoint_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")


def load_completed_task_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    completed: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CompactUpdateError(f"Invalid checkpoint JSON at line {line_number}: {exc}") from exc
        if isinstance(payload, Mapping) and isinstance(payload.get("task_key"), str):
            completed.add(str(payload["task_key"]))
    return completed


def task_plan_sha256(tasks: Sequence[Any]) -> str:
    canonical = json.dumps([asdict(task) for task in tasks], sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def write_or_validate_plan(
    run_state: CompactRunState,
    *,
    tasks: Sequence[Any],
    input_file: Path,
    mode: str,
    interval: str,
    overlap_days: int,
    started_at: datetime,
    resumed: bool,
) -> dict[str, Any]:
    path = run_state.run_dir / "compact-update-plan.json"
    expected = {
        "utility_version": UTILITY_VERSION,
        "source_compact_database_file": run_state.source_database_path.name,
        "working_database_file": run_state.database_path.name,
        "validation_copy": run_state.validation_copy,
        "mode": mode,
        "interval": interval,
        "overlap_days": overlap_days,
        "input_file_name": input_file.name,
        "task_count": len(tasks),
        "task_plan_sha256": task_plan_sha256(tasks),
        "started_at_utc": format_utc(started_at),
        "absolute_local_path_persisted": False,
    }
    if resumed:
        existing = json.loads(path.read_text(encoding="utf-8"))
        keys = ("validation_copy", "mode", "interval", "overlap_days", "input_file_name", "task_count", "task_plan_sha256")
        mismatches = [key for key in keys if existing.get(key) != expected.get(key)]
        if mismatches:
            raise CompactUpdateError("Resume settings do not match original plan: " + ", ".join(mismatches))
        return existing
    path.write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return expected


def table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    names = ("runs", "symbol_state", "bars", "bar_revisions", "events", "event_revisions", "symbol_runs", "sources")
    return {name: int(connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]) for name in names}


def verify_returned_values(
    connection: sqlite3.Connection,
    run_state: CompactRunState,
    tasks_by_key: Mapping[str, Any],
    results: Sequence[Any],
) -> dict[str, int]:
    checked_bars = 0
    checked_events = 0
    bar_mismatches = 0
    event_mismatches = 0
    for result in results:
        if not result.raw_file or result.classification != "SUCCESS_HISTORY_RETURNED":
            continue
        raw_path = run_state.run_dir / result.raw_file
        if not raw_path.is_file():
            bar_mismatches += result.bars_returned
            event_mismatches += result.events_returned
            continue
        body = gzip.decompress(raw_path.read_bytes())
        task = tasks_by_key[result.task_key]
        parsed = history.parse_chart_response(body, requested_symbol=task.symbol, requested_interval=task.interval)
        symbol_row = connection.execute("SELECT symbol_id FROM symbols WHERE symbol=?", (task.symbol,)).fetchone()
        interval_row = connection.execute("SELECT interval_id FROM intervals WHERE interval=?", (task.interval,)).fetchone()
        if symbol_row is None or interval_row is None:
            bar_mismatches += len(parsed.bars)
            event_mismatches += len(parsed.events)
            continue
        symbol_id, interval_id = int(symbol_row[0]), int(interval_row[0])
        for bar in parsed.bars:
            checked_bars += 1
            row = connection.execute(
                "SELECT open, high, low, close, adjclose, volume FROM bars WHERE symbol_id=? AND interval_id=? AND timestamp_utc=?",
                (symbol_id, interval_id, bar.timestamp_utc),
            ).fetchone()
            expected = _bar_values(bar)
            if row is None or any(not _numbers_equal(row[key], expected[key]) for key in expected):
                bar_mismatches += 1
        for event in parsed.events:
            checked_events += 1
            row = connection.execute(
                """SELECT e.event_json FROM events e JOIN event_types et ON et.event_type_id=e.event_type_id
                   WHERE e.symbol_id=? AND e.interval_id=? AND et.event_type=? AND e.event_key=?""",
                (symbol_id, interval_id, event.event_type, event.event_key),
            ).fetchone()
            if row is None or row[0] != event.event_json:
                event_mismatches += 1
    return {
        "checked_bars": checked_bars,
        "bar_mismatches": bar_mismatches,
        "checked_events": checked_events,
        "event_mismatches": event_mismatches,
    }


def _clean_report_text(value: Any, *, maximum: int = 500) -> str:
    text = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())
    return text[:maximum]


def extract_error_details(run_state: CompactRunState, results: Sequence[Any]) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    for result in results:
        if result.classification in {"SUCCESS_HISTORY_RETURNED", "NO_HISTORY_DATA"}:
            continue
        code: str | None = None
        description = result.error_description
        if result.raw_file:
            raw_path = run_state.run_dir / result.raw_file
            try:
                payload = json.loads(gzip.decompress(raw_path.read_bytes()).decode("utf-8"))
                error = payload.get("chart", {}).get("error") if isinstance(payload, Mapping) else None
                if isinstance(error, Mapping):
                    if error.get("code") is not None:
                        code = _clean_report_text(error.get("code"), maximum=120)
                    if error.get("description") is not None:
                        description = _clean_report_text(error.get("description"))
            except (OSError, EOFError, UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                pass
        details.append({
            "symbol": result.symbol,
            "original_classification": result.classification,
            "classification": result.classification,
            "http_status": result.http_status,
            "error_code": code,
            "error_description": _clean_report_text(description),
            "request_start_epoch": result.request_start_epoch,
            "request_end_epoch": result.request_end_epoch,
            "attempts": result.attempts,
            "raw_file": result.raw_file,
        })
    return details


def write_error_review_csv(run_dir: Path, details: Sequence[Mapping[str, Any]]) -> None:
    fields = (
        "symbol", "original_classification", "classification", "http_status",
        "error_code", "error_description",
        "request_start_epoch", "request_end_epoch", "attempts", "raw_file",
    )
    with (run_dir / "error-classification-review.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(details)


def _parse_optional_int(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def review_existing_run(run_dir: Path) -> dict[str, Any]:
    run_dir = normalize_path(run_dir)
    if path_is_within(run_dir, REPOSITORY_ROOT):
        raise CompactUpdateError(f"History run folders must remain outside the repository: {run_dir}")
    results_path = run_dir / "symbol-results.csv"
    if not results_path.is_file():
        raise CompactUpdateError(f"Existing run is missing symbol-results.csv: {run_dir}")
    with results_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    details: list[dict[str, Any]] = []
    for row in rows:
        original = str(row.get("classification") or "")
        if original in {"SUCCESS_HISTORY_RETURNED", "NO_HISTORY_DATA"}:
            continue
        raw_file = str(row.get("raw_file") or "").strip()
        body: bytes | None = None
        if raw_file:
            raw_path = run_dir / raw_file
            try:
                body = gzip.decompress(raw_path.read_bytes())
            except (OSError, EOFError):
                body = None
        status = _parse_optional_int(row.get("http_status"))
        task = history.HistoryTask(
            task_key=str(row.get("task_key") or "review"),
            task_sequence=_parse_optional_int(row.get("task_sequence")) or 0,
            symbol=str(row.get("symbol") or ""),
            interval=str(row.get("interval") or "1d"),
            mode=str(row.get("mode") or "sync"),
            full_range=str(row.get("full_range") or "").casefold() in {"1", "true", "yes"},
            request_start_epoch=_parse_optional_int(row.get("request_start_epoch")),
            request_end_epoch=_parse_optional_int(row.get("request_end_epoch")) or 0,
            prior_latest_epoch=None,
        )
        http = history.HistoryHttpResult(
            body=body, http_status=status, content_type="application/json",
            final_url_redacted="review-existing-run", requested_at_utc="",
            response_received_at_utc="", elapsed_ms=_parse_optional_int(row.get("elapsed_ms")) or 0,
            attempts=[], error_message=str(row.get("error_description") or "") or None,
            session_generation=None,
        )
        parsed = classify_http_result(http, task)
        code: str | None = parsed.error_code
        description = parsed.error_description
        if body is not None:
            try:
                payload = json.loads(body.decode("utf-8"))
                error = payload.get("chart", {}).get("error") if isinstance(payload, Mapping) else None
                if isinstance(error, Mapping):
                    if error.get("code") is not None:
                        code = _clean_report_text(error.get("code"), maximum=120)
                    if error.get("description") is not None:
                        description = _clean_report_text(error.get("description"))
            except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                pass
        details.append({
            "symbol": task.symbol,
            "original_classification": original,
            "classification": parsed.classification,
            "http_status": status,
            "error_code": _clean_report_text(code, maximum=120),
            "error_description": _clean_report_text(description),
            "request_start_epoch": task.request_start_epoch,
            "request_end_epoch": task.request_end_epoch,
            "attempts": _parse_optional_int(row.get("attempts")) or 0,
            "raw_file": raw_file or None,
        })
    write_error_review_csv(run_dir, details)
    groups = Counter(
        (
            str(item.get("classification") or ""),
            str(item.get("http_status") if item.get("http_status") is not None else ""),
            str(item.get("error_code") or ""),
            str(item.get("error_description") or ""),
        )
        for item in details
    )
    report = [
        "Yahoo Long-History Existing-Run Error Review",
        f"Utility version: {UTILITY_VERSION}",
        f"Run folder name: {run_dir.name}",
        "Network access: False",
        "Database opened or changed: False",
        f"Responses reviewed: {len(details)}",
        "",
        "Candidate.7 classifications:",
    ]
    counts = Counter(str(item["classification"]) for item in details)
    report.extend(f"- {key}: {value}" for key, value in sorted(counts.items()))
    report.extend(["", "Grouped details:"])
    if groups:
        for (classification, status, code, description), count in sorted(groups.items()):
            symbols = sorted(
                str(item["symbol"]) for item in details
                if str(item.get("classification") or "") == classification
                and str(item.get("http_status") if item.get("http_status") is not None else "") == status
                and str(item.get("error_code") or "") == code
                and str(item.get("error_description") or "") == description
            )
            report.append(
                f"- {classification}: {count}; HTTP={status or 'none'}; code={code or 'none'}; "
                f"symbols={','.join(symbols)}; description={description or 'none'}"
            )
    else:
        report.append("- none")
    report.extend([
        "",
        "Outputs",
        "- error-classification-review.csv",
        "- error-classification-review.txt",
    ])
    (run_dir / "error-classification-review.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    return {
        "responses_reviewed": len(details),
        "classifications": dict(sorted(counts.items())),
        "csv_path": run_dir / "error-classification-review.csv",
        "report_path": run_dir / "error-classification-review.txt",
    }


def should_emit_symbol_progress(
    completed_count: int, total: int, result: Any, *, progress_every: int, verbose: bool
) -> bool:
    if verbose or completed_count in {1, total} or completed_count % progress_every == 0:
        return True
    return result.classification not in {"SUCCESS_HISTORY_RETURNED", "NO_HISTORY_DATA"}


def write_results_csv(run_dir: Path, results: Sequence[Any]) -> None:
    fields = tuple(asdict(results[0]).keys()) if results else (
        "task_key", "task_sequence", "symbol", "classification",
    )
    with (run_dir / "symbol-results.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for result in results:
            writer.writerow(asdict(result))


def submit_bounded(
    executor: ThreadPoolExecutor,
    task_iter: Iterable[Any],
    pending: dict[Future[Any], Any],
    *,
    maximum_pending: int,
    request_function: Callable[[Any], Any],
) -> None:
    iterator = task_iter  # caller passes an iterator
    while len(pending) < maximum_pending:
        try:
            task = next(iterator)  # type: ignore[arg-type]
        except StopIteration:
            return
        pending[executor.submit(request_function, task)] = task


def run_compact_update(
    tasks: Sequence[Any],
    *,
    input_file: Path,
    source_database: Path,
    archive_root: Path,
    database_resolution: str,
    mode: str,
    interval: str,
    overlap_days: int,
    concurrency: int,
    timeout_seconds: float,
    maximum_attempts: int,
    backoff_seconds: Sequence[float],
    user_agent: str,
    in_place: bool,
    progress_every: int = 25,
    verbose_progress: bool = False,
    resume_run: Path | None = None,
    session: Any | None = None,
    opener: Callable[..., Any] = urlopen,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = utc_now,
    progress: Callable[[str], None] = print,
    request_override: Callable[[Any], Any] | None = None,
) -> tuple[Path, dict[str, Any]]:
    if concurrency < 1:
        raise CompactUpdateError("concurrency must be at least 1")
    if progress_every < 1:
        raise CompactUpdateError("progress_every must be at least 1")
    started_at = now()
    started_clock = clock()
    source_before = fingerprint_files(source_database)
    run_state = create_run_state(
        source_database, archive_root, started_at=started_at, in_place=in_place, resume_run=resume_run
    )
    resumed = resume_run is not None
    plan = write_or_validate_plan(
        run_state, tasks=tasks, input_file=input_file, mode=mode, interval=interval,
        overlap_days=overlap_days, started_at=started_at, resumed=resumed,
    )
    if resumed:
        started_at = datetime.fromisoformat(str(plan["started_at_utc"]).replace("Z", "+00:00"))
    completed_keys = load_completed_task_keys(run_state.checkpoint_path)
    remaining = [task for task in tasks if task.task_key not in completed_keys]

    connection = connect_writable(run_state.database_path)
    lookups = load_lookups(connection)
    run_id = run_state.run_dir.name
    run_key = ensure_run(connection, lookups, run_id)
    interval_id = ensure_interval(connection, lookups, interval)
    before_counts = table_counts(connection)
    connection.execute(
        """
        INSERT INTO runs(
            run_key, mode, interval_id, overlap_days, started_at_utc, completed_at_utc,
            status, input_file_name, requested_symbols, completed_symbols,
            run_folder_name, utility_version
        ) VALUES(?,?,?,?,?,NULL,'RUNNING',?,?,?,?,?)
        ON CONFLICT(run_key) DO UPDATE SET status='RUNNING', requested_symbols=excluded.requested_symbols,
            completed_symbols=excluded.completed_symbols, utility_version=excluded.utility_version
        """,
        (
            run_key, mode, interval_id, overlap_days, format_utc(started_at), input_file.name,
            len(tasks), len(completed_keys), run_state.run_dir.name, UTILITY_VERSION,
        ),
    )
    connection.commit()

    request_session = session or fast.YahooAnonymousSession(user_agent=user_agent, timeout_seconds=timeout_seconds)
    gate = fast.SharedBackoffGate(clock=clock, sleep=sleep)
    results: list[Any] = []
    if completed_keys:
        placeholders = ",".join("?" for _ in completed_keys)
        rows = connection.execute(
            f"""SELECT sr.*, s.symbol, i.interval, src.source_file, src.source_sha256
                FROM symbol_runs sr JOIN symbols s ON s.symbol_id=sr.symbol_id
                JOIN intervals i ON i.interval_id=sr.interval_id
                LEFT JOIN sources src ON src.source_id=sr.raw_source_id
                WHERE sr.run_key=? AND sr.task_key IN ({placeholders})""",
            (run_key, *sorted(completed_keys)),
        ).fetchall()
        for row in rows:
            results.append(history.SymbolResult(
                task_key=row["task_key"], task_sequence=row["task_sequence"], symbol=row["symbol"],
                interval=row["interval"], mode=row["mode"], full_range=bool(row["full_range"]),
                request_start_epoch=row["request_start_epoch"], request_end_epoch=row["request_end_epoch"],
                classification=row["classification"], http_status=row["http_status"], returned_symbol=None,
                bars_returned=row["bars_returned"], new_bars=row["new_bars"], revised_bars=row["revised_bars"],
                unchanged_bars=row["unchanged_bars"], missing_bars=row["missing_bars"],
                events_returned=row["events_returned"], new_events=row["new_events"],
                revised_events=row["revised_events"], unchanged_events=row["unchanged_events"],
                full_refresh_required=bool(row["full_refresh_required"]), full_refresh_reason=row["full_refresh_reason"],
                raw_file=None, raw_uncompressed_sha256=rebuild.blob_to_sha(row["source_sha256"]),
                raw_compressed_sha256=None, raw_uncompressed_bytes=0, raw_compressed_bytes=0,
                metadata_file="", elapsed_ms=row["elapsed_ms"], attempts=row["attempts"],
                error_description=row["error_description"],
            ))

    def request_function(task: Any) -> Any:
        if request_override is not None:
            return request_override(task)
        return history.request_history_with_retry(
            task, session=request_session, timeout_seconds=timeout_seconds,
            maximum_attempts=maximum_attempts, backoff_seconds=backoff_seconds,
            user_agent=user_agent, gate=gate, opener=opener, sleep=sleep, clock=clock, now=now,
        )

    progress(
        f"Starting compact history {'validation' if run_state.validation_copy else 'in-place update'}: "
        f"{len(remaining)} task(s), concurrency {concurrency}"
    )
    iterator = iter(remaining)
    pending: dict[Future[Any], Any] = {}
    completed_count = len(completed_keys)
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        submit_bounded(
            executor, iterator, pending, maximum_pending=max(concurrency * 2, 1),
            request_function=request_function,
        )
        while pending:
            done, _ = wait(set(pending), return_when=FIRST_COMPLETED)
            for future in done:
                task = pending.pop(future)
                http = future.result()
                result = process_symbol_result(
                    connection, lookups, run_id=run_id, run_state=run_state, task=task, http=http,
                )
                results.append(result)
                append_checkpoint(run_state, result)
                completed_count += 1
                connection.execute("UPDATE runs SET completed_symbols=? WHERE run_key=?", (completed_count, run_key))
                connection.commit()
                if should_emit_symbol_progress(
                    completed_count, len(tasks), result,
                    progress_every=progress_every, verbose=verbose_progress,
                ):
                    progress(
                        f"[{completed_count:05d}/{len(tasks):05d}] {task.symbol:<18} "
                        f"{result.classification} new={result.new_bars} revised={result.revised_bars}"
                    )
            submit_bounded(
                executor, iterator, pending, maximum_pending=max(concurrency * 2, 1),
                request_function=request_function,
            )

    results.sort(key=lambda result: result.task_sequence)
    tasks_by_key = {task.task_key: task for task in tasks}
    value_verification = verify_returned_values(connection, run_state, tasks_by_key, results)
    completed_at = now()
    connection.execute(
        "UPDATE runs SET completed_at_utc=?, status='COMPLETED', completed_symbols=? WHERE run_key=?",
        (format_utc(completed_at), len(results), run_key),
    )
    connection.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('last_compact_update_run_id',?)", (run_id,))
    if not run_state.validation_copy:
        connection.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('build_status','ACTIVE_COMPACT')")
    connection.commit()
    after_counts = table_counts(connection)
    quick_rows = [str(row[0]) for row in connection.execute("PRAGMA quick_check")]
    quick_check = "ok" if quick_rows == ["ok"] else "; ".join(quick_rows)
    foreign_key_rows = list(connection.execute("PRAGMA foreign_key_check"))
    foreign_key_ok = not foreign_key_rows
    state_mismatch_count = int(connection.execute(
        """SELECT COUNT(*) FROM symbol_state st
           WHERE st.last_bar_timestamp IS NOT (
             SELECT MAX(b.timestamp_utc) FROM bars b
             WHERE b.symbol_id=st.symbol_id AND b.interval_id=st.interval_id
           )"""
    ).fetchone()[0])
    connection.close()

    source_after = fingerprint_files(source_database)
    source_unchanged = True if not in_place else None
    if not in_place:
        source_unchanged = main_database_unchanged(source_before, source_after)
        if not source_unchanged:
            raise CompactUpdateError("The verified source compact database changed during validation.")
    accounting_ok = all(
        result.new_bars + result.revised_bars + result.unchanged_bars == result.bars_returned
        and result.new_events + result.revised_events + result.unchanged_events == result.events_returned
        for result in results if result.classification == "SUCCESS_HISTORY_RETURNED"
    )
    verification_ok = (
        quick_check == "ok" and foreign_key_ok and state_mismatch_count == 0
        and value_verification["bar_mismatches"] == 0
        and value_verification["event_mismatches"] == 0
        and accounting_ok
    )
    elapsed = max(0.0, clock() - started_clock)
    totals = Counter()
    for result in results:
        for key in (
            "bars_returned", "new_bars", "revised_bars", "unchanged_bars", "missing_bars",
            "events_returned", "new_events", "revised_events", "unchanged_events",
        ):
            totals[key] += int(getattr(result, key))
    classifications = Counter(result.classification for result in results)
    error_details = extract_error_details(run_state, results)
    error_groups = Counter(
        (
            str(item.get("classification") or ""),
            str(item.get("http_status") if item.get("http_status") is not None else ""),
            str(item.get("error_code") or ""),
            str(item.get("error_description") or ""),
        )
        for item in error_details
    )
    manifest = {
        "utility_version": UTILITY_VERSION,
        "started_at_utc": format_utc(started_at),
        "completed_at_utc": format_utc(completed_at),
        "elapsed_seconds": round(elapsed, 3),
        "database_resolution": database_resolution,
        "source_compact_database_file": source_database.name,
        "source_compact_database_unchanged": source_unchanged,
        "working_database_file": run_state.database_path.name,
        "validation_copy": run_state.validation_copy,
        "in_place_update": in_place,
        "network_access": request_override is None,
        "mode": mode,
        "interval": interval,
        "overlap_days": overlap_days,
        "symbols_requested": len(tasks),
        "symbols_completed": len(results),
        "totals": dict(totals),
        "classifications": dict(sorted(classifications.items())),
        "error_detail_count": len(error_details),
        "error_group_count": len(error_groups),
        "progress_every": progress_every,
        "verbose_progress": verbose_progress,
        "before_counts": before_counts,
        "after_counts": after_counts,
        "quick_check": quick_check,
        "foreign_key_check_ok": foreign_key_ok,
        "symbol_state_latest_mismatches": state_mismatch_count,
        "returned_value_verification": value_verification,
        "result_accounting_ok": accounting_ok,
        "verification_ok": verification_ok,
        "active_compact_database_promoted": False,
        "legacy_history_database_touched": False,
        "absolute_local_path_persisted": False,
    }
    write_results_csv(run_state.run_dir, results)
    write_error_review_csv(run_state.run_dir, error_details)
    (run_state.run_dir / "compact-update-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    report = [
        "Yahoo Long-History Compact Incremental Update",
        f"Utility version: {UTILITY_VERSION}",
        f"Started UTC: {manifest['started_at_utc']}",
        f"Completed UTC: {manifest['completed_at_utc']}",
        f"Mode: {mode}",
        f"Validation copy: {run_state.validation_copy}",
        f"In-place update: {in_place}",
        "Source compact database unchanged: " + (
            "not applicable (updated in place)" if in_place else str(source_unchanged)
        ),
        f"Symbols completed: {len(results):,} of {len(tasks):,}",
        f"Bars returned: {totals['bars_returned']:,}",
        f"New bars: {totals['new_bars']:,}",
        f"Revised bars: {totals['revised_bars']:,}",
        f"Unchanged bars: {totals['unchanged_bars']:,}",
        f"Missing bars: {totals['missing_bars']:,}",
        f"Events returned: {totals['events_returned']:,}",
        f"New events: {totals['new_events']:,}",
        f"Revised events: {totals['revised_events']:,}",
        f"Quick check: {quick_check}",
        f"Foreign-key check: {'ok' if foreign_key_ok else 'FAILED'}",
        f"Symbol-state/latest mismatches: {state_mismatch_count:,}",
        f"Returned bars checked: {value_verification['checked_bars']:,}",
        f"Returned bar mismatches: {value_verification['bar_mismatches']:,}",
        f"Returned events checked: {value_verification['checked_events']:,}",
        f"Returned event mismatches: {value_verification['event_mismatches']:,}",
        f"Result accounting: {'ok' if accounting_ok else 'FAILED'}",
        f"Overall verification: {'PASS' if verification_ok else 'FAIL'}",
        "",
        "Classifications:",
    ]
    report.extend(f"- {key}: {value}" for key, value in sorted(classifications.items()))
    report.extend(["", "Detailed non-success responses:"])
    if error_groups:
        for (classification, status, code, description), count in sorted(error_groups.items()):
            symbols = sorted(
                item["symbol"] for item in error_details
                if str(item.get("classification") or "") == classification
                and str(item.get("http_status") if item.get("http_status") is not None else "") == status
                and str(item.get("error_code") or "") == code
                and str(item.get("error_description") or "") == description
            )
            report.append(
                f"- {classification}: {count}; HTTP={status or 'none'}; code={code or 'none'}; "
                f"symbols={','.join(symbols)}; description={description or 'none'}"
            )
    else:
        report.append("- none")
    report.append("- Complete details: error-classification-review.csv")
    report.extend(["", "Safety conclusion", "- The legacy history.sqlite database was not opened or changed."])
    if run_state.validation_copy:
        report.extend([
            "- Validation mode updated only history_compact_validation.sqlite.",
            "- The verified source history_compact.sqlite remained unchanged during validation.",
            "- No database promotion, replacement, rename, or deletion was performed.",
        ])
    else:
        report.extend([
            "- In-place mode updated the verified history_compact.sqlite database after explicit acknowledgment.",
            "- Source-unchanged comparison is not applicable because history_compact.sqlite was the update target.",
            "- No database replacement, rename, or deletion was performed.",
        ])
    (run_state.run_dir / "compact-update-report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    return run_state.run_dir, manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Incrementally synchronize a compact Yahoo long-history database."
    )
    parser.add_argument("--mode", choices=sorted(history.VALID_MODES), default="sync")
    parser.add_argument("--input", type=Path, default=history.DEFAULT_INPUT_FILE)
    parser.add_argument("--history-exclusions", type=Path, default=history.DEFAULT_HISTORY_EXCLUSION_FILE)
    parser.add_argument(
        "--include-history-excluded", action="store_true",
        help="Diagnostic override: include browser-confirmed no-history symbols in Long-history requests.",
    )
    parser.add_argument("--interval", choices=sorted(history.VALID_INTERVALS), default="1d")
    parser.add_argument("--overlap-days", type=int, default=30)
    parser.add_argument("--through-date")
    parser.add_argument("--smoke", action="store_true", help="Use the first five unique symbols.")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--database", type=Path, help="Explicit history_compact.sqlite path.")
    parser.add_argument("--rebuild-dir", type=Path, help="Folder containing history_compact.sqlite.")
    parser.add_argument("--resume-run", type=Path)
    parser.add_argument(
        "--review-run", type=Path,
        help="Reclassify non-success responses from an existing run without network or database access.",
    )
    parser.add_argument("--in-place", action="store_true", help="Update the verified compact database directly.")
    parser.add_argument(
        "--acknowledge-in-place-update", action="store_true",
        help="Required with --in-place. Confirms that the verified compact database will be changed.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--backoff-seconds", default="2,10")
    parser.add_argument("--user-agent", default=history.DEFAULT_USER_AGENT)
    parser.add_argument(
        "--progress-every", type=int, default=25,
        help="Print routine progress every N completed symbols (default: 25).",
    )
    parser.add_argument(
        "--verbose-progress", action="store_true",
        help="Print one progress line for every completed symbol.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.review_run is not None:
            if args.in_place or args.resume_run is not None:
                raise CompactUpdateError("--review-run cannot be combined with --in-place or --resume-run.")
            summary = review_existing_run(args.review_run)
            print(f"Responses reviewed: {summary['responses_reviewed']}")
            for key, value in summary["classifications"].items():
                print(f"{key}: {value}")
            print(f"CSV: {summary['csv_path']}")
            print(f"Report: {summary['report_path']}")
            return 0
        if args.in_place and not args.acknowledge_in_place_update:
            raise CompactUpdateError(
                "--in-place requires --acknowledge-in-place-update. Run validation-copy mode first."
            )
        if args.limit is not None and args.limit < 1:
            raise CompactUpdateError("--limit must be at least 1")
        source_database, database_resolution, archive_root = resolve_compact_database(
            args.database, args.rebuild_dir
        )
        all_symbols = history.unique_symbols_from_input(args.input)
        exclusion_map = history.load_history_exclusions(args.history_exclusions)
        symbols, excluded_symbols = history.partition_history_symbols(
            all_symbols, exclusion_map, include_excluded=args.include_history_excluded
        )
        if args.include_history_excluded:
            excluded_symbols = []
        else:
            print(
                f"Long-history exclusions: {len(excluded_symbols)} request(s) skipped; "
                "Fast-mode capture remains enabled."
            )
        if args.smoke:
            symbols = symbols[:5]
        if args.limit is not None:
            symbols = symbols[: args.limit]
        request_end_epoch = history.parse_through_date(args.through_date)
        planning_database = source_database
        tasks = build_tasks(
            symbols, mode=args.mode, interval=args.interval, overlap_days=args.overlap_days,
            request_end_epoch=request_end_epoch, database_path=planning_database,
        )
        if args.dry_run:
            print(json.dumps({
                "utility_version": UTILITY_VERSION,
                "history_exclusions": history.history_exclusion_manifest(
                    excluded_symbols, exclusion_file=args.history_exclusions,
                    override_used=args.include_history_excluded,
                ),
                "source_compact_database_file": source_database.name,
                "database_resolution": database_resolution,
                "validation_copy": not args.in_place,
                "mode": args.mode,
                "interval": args.interval,
                "overlap_days": args.overlap_days,
                "planned_tasks": len(tasks),
                "full_range_tasks": sum(task.full_range for task in tasks),
                "incremental_tasks": sum(not task.full_range for task in tasks),
                "network_requests_sent": 0,
                "first_tasks": [asdict(task) for task in tasks[:10]],
            }, indent=2))
            return 0
        print(f"Verified compact database: {source_database} ({database_resolution})")
        print("Update target: " + ("verified compact database IN PLACE" if args.in_place else "separate validation copy"))
        run_dir, manifest = run_compact_update(
            tasks, input_file=args.input, source_database=source_database, archive_root=archive_root,
            database_resolution=database_resolution, mode=args.mode, interval=args.interval,
            overlap_days=args.overlap_days, concurrency=args.concurrency, timeout_seconds=args.timeout,
            maximum_attempts=args.max_attempts, backoff_seconds=history.parse_backoff(args.backoff_seconds),
            user_agent=args.user_agent, in_place=args.in_place,
            progress_every=args.progress_every, verbose_progress=args.verbose_progress,
            resume_run=args.resume_run,
        )
        manifest = history.write_history_exclusion_outputs(
            run_dir, excluded_symbols, exclusion_file=args.history_exclusions,
            override_used=args.include_history_excluded,
            manifest_file="compact-update-manifest.json", report_file="compact-update-report.txt",
        )
        print(f"Run folder: {run_dir}")
        print(f"Working database: {manifest['working_database_file']}")
        source_status = manifest["source_compact_database_unchanged"]
        if args.in_place:
            print("Source compact database unchanged: not applicable (updated in place)")
        else:
            print(f"Source compact database unchanged: {source_status}")
        print(f"Overall verification: {'PASS' if manifest['verification_ok'] else 'FAIL'}")
        print(f"Primary report: compact-update-report.txt")
        return 0 if manifest["verification_ok"] else 3
    except (CompactUpdateError, HistoryInputError, YahooSessionError, sqlite3.Error, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
