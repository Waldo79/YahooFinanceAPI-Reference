#!/usr/bin/env python3
"""Build and verify a full compact copy of the Yahoo long-history database.

Version 0.1.0-candidate.5. The authoritative ``history.sqlite`` is opened with
SQLite URI ``mode=ro`` and ``PRAGMA query_only=ON``. A separate compact database
is created outside the repository. The build is checkpointed by symbol and can
be resumed. Every logical table is verified by ordered row count and SHA-256.
No Yahoo request, source mutation, replacement, or deletion is performed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

UTILITY_VERSION = "0.1.0-candidate.5"
SCHEMA_VERSION = "1"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LOCAL_CONFIG_FILE = REPOSITORY_ROOT / "config" / "local" / "history_capture_local.json"
OUTPUT_ROOT_ENVIRONMENT_VARIABLE = "YAHOO_HISTORY_CAPTURE_ROOT"
DATABASE_FILENAME = "history.sqlite"
COMPACT_DATABASE_FILENAME = "history_compact.sqlite"
_DEFAULT_ARCHIVE_PARENT = (
    REPOSITORY_ROOT.parent.parent
    if REPOSITORY_ROOT.parent.name.casefold() == "code"
    else REPOSITORY_ROOT.parent
)
DEFAULT_EXTERNAL_ROOT = _DEFAULT_ARCHIVE_PARENT / "Captures" / "long-history"
BAR_BATCH_SIZE = 20_000
EVENT_BATCH_SIZE = 5_000


class RebuildError(RuntimeError):
    """Raised when the compact rebuild cannot safely continue."""


class RebuildPaused(RebuildError):
    """Testing-only controlled pause after committed symbol checkpoints."""


@dataclass(frozen=True)
class FileFingerprint:
    name: str
    exists: bool
    bytes: int
    modified_time_ns: int


@dataclass(frozen=True)
class VerificationResult:
    dataset: str
    source_rows: int
    compact_rows: int
    source_sha256: str
    compact_sha256: str
    matches_source: bool


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def filename_utc(value: datetime) -> str:
    return format_utc(value).replace(":", "-")


def epoch_to_utc_text(value: int) -> str:
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    try:
        return (epoch + timedelta(seconds=int(value))).isoformat().replace("+00:00", "Z")
    except (OverflowError, ValueError) as exc:
        raise RebuildError(f"History timestamp is outside the supported range: {value}") from exc


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
        raise RebuildError(f"Cannot read local history config {resolved}: {exc}") from exc
    value = payload.get("output_root") if isinstance(payload, Mapping) else None
    if not isinstance(value, str) or not value.strip():
        raise RebuildError(f"Local history config is missing a non-empty output_root: {resolved}")
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


def default_output_directory(database_path: Path, started_at: datetime) -> Path:
    return database_path.parent / "compact-rebuilds" / f"{filename_utc(started_at)}_full-compact-rebuild"


def validate_source_database(path: Path) -> Path:
    resolved = normalize_path(path)
    if path_is_within(resolved, REPOSITORY_ROOT):
        raise RebuildError(
            "The authoritative long-history database must remain outside the synchronized repository: "
            f"{resolved}"
        )
    if not resolved.is_file():
        raise RebuildError(f"SQLite database does not exist: {resolved}")
    return resolved


def validate_new_output_directory(path: Path) -> Path:
    resolved = normalize_path(path)
    if path_is_within(resolved, REPOSITORY_ROOT):
        raise RebuildError(
            "Compact rebuild files must remain outside the synchronized repository: "
            f"{resolved}"
        )
    resolved.mkdir(parents=True, exist_ok=False)
    probe = resolved / ".write-test"
    probe.write_text("compact-rebuild-write-test\n", encoding="utf-8")
    probe.unlink()
    return resolved


def validate_resume_directory(path: Path) -> Path:
    resolved = normalize_path(path)
    if path_is_within(resolved, REPOSITORY_ROOT):
        raise RebuildError(
            "Compact rebuild files must remain outside the synchronized repository: "
            f"{resolved}"
        )
    if not resolved.is_dir():
        raise RebuildError(f"Resume directory does not exist: {resolved}")
    if not (resolved / COMPACT_DATABASE_FILENAME).is_file():
        raise RebuildError(f"Resume directory has no {COMPACT_DATABASE_FILENAME}: {resolved}")
    return resolved


def fingerprint_files(database_path: Path) -> list[FileFingerprint]:
    paths = [database_path, Path(str(database_path) + "-wal"), Path(str(database_path) + "-shm")]
    results: list[FileFingerprint] = []
    for path in paths:
        try:
            stat = path.stat()
            results.append(FileFingerprint(path.name, True, stat.st_size, stat.st_mtime_ns))
        except FileNotFoundError:
            results.append(FileFingerprint(path.name, False, 0, 0))
    return results


def main_database_unchanged(before: Sequence[FileFingerprint], after: Sequence[FileFingerprint]) -> bool:
    before_map = {item.name: item for item in before}
    after_map = {item.name: item for item in after}
    return before_map.get(before[0].name) == after_map.get(after[0].name)


def connect_read_only(database_path: Path) -> sqlite3.Connection:
    resolved = validate_source_database(database_path)
    try:
        connection = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise RebuildError(f"Cannot open source SQLite database in read-only mode: {exc}") from exc
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA temp_store = MEMORY")
    if int(connection.execute("PRAGMA query_only").fetchone()[0]) != 1:
        connection.close()
        raise RebuildError("SQLite did not accept PRAGMA query_only=ON.")
    return connection


def connect_output(database_path: Path, *, new: bool) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    if new:
        connection.execute("PRAGMA page_size = 4096")
    connection.execute("PRAGMA journal_mode = DELETE")
    connection.execute("PRAGMA synchronous = OFF")
    connection.execute("PRAGMA temp_store = MEMORY")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA cache_size = -131072")
    return connection


def require_source_schema(connection: sqlite3.Connection) -> None:
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    required = {
        "archive_meta", "runs", "symbol_state", "bars", "bar_revisions",
        "events", "event_revisions", "symbol_runs",
    }
    missing = sorted(required - tables)
    if missing:
        raise RebuildError(f"Source database is missing required tables: {', '.join(missing)}")


def source_symbols(connection: sqlite3.Connection) -> list[str]:
    query = """
        SELECT symbol FROM (
            SELECT symbol FROM symbol_state
            UNION SELECT symbol FROM bars
            UNION SELECT symbol FROM events
            UNION SELECT symbol FROM bar_revisions
            UNION SELECT symbol FROM event_revisions
            UNION SELECT symbol FROM symbol_runs
        ) WHERE symbol <> '' ORDER BY symbol COLLATE BINARY
    """
    symbols = [str(row[0]) for row in connection.execute(query)]
    if not symbols:
        raise RebuildError("Source database contains no symbols.")
    return symbols


def initialize_compact_database(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
        CREATE TABLE rebuild_progress (
            symbol_id INTEGER PRIMARY KEY,
            bars_copied INTEGER NOT NULL,
            events_copied INTEGER NOT NULL,
            completed_at_utc TEXT NOT NULL
        ) WITHOUT ROWID;
        CREATE TABLE archive_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
        CREATE TABLE symbols (symbol_id INTEGER PRIMARY KEY, symbol TEXT NOT NULL UNIQUE);
        CREATE TABLE intervals (interval_id INTEGER PRIMARY KEY, interval TEXT NOT NULL UNIQUE);
        CREATE TABLE run_ids (run_key INTEGER PRIMARY KEY, run_id TEXT NOT NULL UNIQUE);
        CREATE TABLE sources (
            source_id INTEGER PRIMARY KEY,
            source_file TEXT NOT NULL,
            source_sha256 BLOB NOT NULL,
            UNIQUE(source_file, source_sha256)
        );
        CREATE TABLE event_types (event_type_id INTEGER PRIMARY KEY, event_type TEXT NOT NULL UNIQUE);
        CREATE TABLE runs (
            run_key INTEGER PRIMARY KEY,
            mode TEXT NOT NULL,
            interval_id INTEGER NOT NULL,
            overlap_days INTEGER NOT NULL,
            started_at_utc TEXT NOT NULL,
            completed_at_utc TEXT,
            status TEXT NOT NULL,
            input_file_name TEXT NOT NULL,
            requested_symbols INTEGER NOT NULL,
            completed_symbols INTEGER NOT NULL,
            run_folder_name TEXT NOT NULL,
            utility_version TEXT NOT NULL,
            FOREIGN KEY(run_key) REFERENCES run_ids(run_key),
            FOREIGN KEY(interval_id) REFERENCES intervals(interval_id)
        ) WITHOUT ROWID;
        CREATE TABLE symbol_state (
            symbol_id INTEGER NOT NULL,
            interval_id INTEGER NOT NULL,
            last_bar_timestamp INTEGER,
            last_checked_at_utc TEXT,
            last_success_run_key INTEGER,
            baseline_run_key INTEGER,
            full_refresh_required INTEGER NOT NULL,
            full_refresh_reason TEXT NOT NULL,
            PRIMARY KEY(symbol_id, interval_id),
            FOREIGN KEY(symbol_id) REFERENCES symbols(symbol_id),
            FOREIGN KEY(interval_id) REFERENCES intervals(interval_id),
            FOREIGN KEY(last_success_run_key) REFERENCES run_ids(run_key),
            FOREIGN KEY(baseline_run_key) REFERENCES run_ids(run_key)
        ) WITHOUT ROWID;
        CREATE TABLE bars (
            symbol_id INTEGER NOT NULL,
            interval_id INTEGER NOT NULL,
            timestamp_utc INTEGER NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            adjclose REAL,
            volume INTEGER,
            first_seen_run_key INTEGER NOT NULL,
            last_seen_run_key INTEGER NOT NULL,
            source_id INTEGER NOT NULL,
            PRIMARY KEY(symbol_id, interval_id, timestamp_utc),
            FOREIGN KEY(symbol_id) REFERENCES symbols(symbol_id),
            FOREIGN KEY(interval_id) REFERENCES intervals(interval_id),
            FOREIGN KEY(first_seen_run_key) REFERENCES run_ids(run_key),
            FOREIGN KEY(last_seen_run_key) REFERENCES run_ids(run_key),
            FOREIGN KEY(source_id) REFERENCES sources(source_id)
        ) WITHOUT ROWID;
        CREATE TABLE bar_revisions (
            revision_id INTEGER PRIMARY KEY,
            run_key INTEGER NOT NULL,
            symbol_id INTEGER NOT NULL,
            interval_id INTEGER NOT NULL,
            timestamp_utc INTEGER NOT NULL,
            detected_at_utc TEXT NOT NULL,
            action TEXT NOT NULL,
            changed_fields_json TEXT NOT NULL,
            old_values_json TEXT,
            new_values_json TEXT,
            FOREIGN KEY(run_key) REFERENCES run_ids(run_key),
            FOREIGN KEY(symbol_id) REFERENCES symbols(symbol_id),
            FOREIGN KEY(interval_id) REFERENCES intervals(interval_id)
        );
        CREATE TABLE events (
            symbol_id INTEGER NOT NULL,
            interval_id INTEGER NOT NULL,
            event_type_id INTEGER NOT NULL,
            event_timestamp_utc INTEGER NOT NULL,
            event_key TEXT NOT NULL,
            event_json TEXT NOT NULL,
            first_seen_run_key INTEGER NOT NULL,
            last_seen_run_key INTEGER NOT NULL,
            source_id INTEGER NOT NULL,
            PRIMARY KEY(symbol_id, interval_id, event_type_id, event_key),
            FOREIGN KEY(symbol_id) REFERENCES symbols(symbol_id),
            FOREIGN KEY(interval_id) REFERENCES intervals(interval_id),
            FOREIGN KEY(event_type_id) REFERENCES event_types(event_type_id),
            FOREIGN KEY(first_seen_run_key) REFERENCES run_ids(run_key),
            FOREIGN KEY(last_seen_run_key) REFERENCES run_ids(run_key),
            FOREIGN KEY(source_id) REFERENCES sources(source_id)
        ) WITHOUT ROWID;
        CREATE TABLE event_revisions (
            revision_id INTEGER PRIMARY KEY,
            run_key INTEGER NOT NULL,
            symbol_id INTEGER NOT NULL,
            interval_id INTEGER NOT NULL,
            event_type_id INTEGER NOT NULL,
            event_timestamp_utc INTEGER NOT NULL,
            detected_at_utc TEXT NOT NULL,
            action TEXT NOT NULL,
            old_event_json TEXT,
            new_event_json TEXT,
            FOREIGN KEY(run_key) REFERENCES run_ids(run_key),
            FOREIGN KEY(symbol_id) REFERENCES symbols(symbol_id),
            FOREIGN KEY(interval_id) REFERENCES intervals(interval_id),
            FOREIGN KEY(event_type_id) REFERENCES event_types(event_type_id)
        );
        CREATE TABLE symbol_runs (
            run_key INTEGER NOT NULL,
            task_key TEXT NOT NULL,
            task_sequence INTEGER NOT NULL,
            symbol_id INTEGER NOT NULL,
            interval_id INTEGER NOT NULL,
            mode TEXT NOT NULL,
            full_range INTEGER NOT NULL,
            request_start_epoch INTEGER,
            request_end_epoch INTEGER NOT NULL,
            classification TEXT NOT NULL,
            http_status INTEGER,
            bars_returned INTEGER NOT NULL,
            new_bars INTEGER NOT NULL,
            revised_bars INTEGER NOT NULL,
            unchanged_bars INTEGER NOT NULL,
            missing_bars INTEGER NOT NULL,
            events_returned INTEGER NOT NULL,
            new_events INTEGER NOT NULL,
            revised_events INTEGER NOT NULL,
            unchanged_events INTEGER NOT NULL,
            full_refresh_required INTEGER NOT NULL,
            full_refresh_reason TEXT NOT NULL,
            raw_source_id INTEGER,
            raw_file_fallback TEXT,
            raw_sha256_fallback TEXT,
            elapsed_ms INTEGER NOT NULL,
            attempts INTEGER NOT NULL,
            error_description TEXT,
            PRIMARY KEY(run_key, task_key),
            FOREIGN KEY(run_key) REFERENCES run_ids(run_key),
            FOREIGN KEY(symbol_id) REFERENCES symbols(symbol_id),
            FOREIGN KEY(interval_id) REFERENCES intervals(interval_id),
            FOREIGN KEY(raw_source_id) REFERENCES sources(source_id)
        ) WITHOUT ROWID;
        """
    )
    connection.executemany(
        "INSERT INTO meta(key, value) VALUES(?, ?)",
        [
            ("schema_name", "compact_long_history"),
            ("schema_version", SCHEMA_VERSION),
            ("utility_version", UTILITY_VERSION),
            ("build_status", "BUILDING"),
        ],
    )
    connection.commit()


def sha_to_blob(value: str) -> bytes:
    text = str(value)
    try:
        return bytes.fromhex(text)
    except ValueError:
        return text.encode("utf-8")


def blob_to_sha(value: bytes | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if len(value) == 32:
        return value.hex()
    return value.decode("utf-8")


@dataclass
class Lookups:
    symbols: dict[str, int]
    intervals: dict[str, int]
    runs: dict[str, int]
    sources: dict[tuple[str, str], int]
    event_types: dict[str, int]


def distinct_values(connection: sqlite3.Connection, query: str) -> list[str]:
    return [str(row[0]) for row in connection.execute(query) if row[0] is not None and str(row[0]) != ""]


def preload_lookups(source: sqlite3.Connection, target: sqlite3.Connection) -> Lookups:
    symbols = source_symbols(source)
    intervals = distinct_values(
        source,
        """
        SELECT interval FROM (
            SELECT interval FROM runs UNION SELECT interval FROM symbol_state
            UNION SELECT interval FROM bars UNION SELECT interval FROM bar_revisions
            UNION SELECT interval FROM events UNION SELECT interval FROM event_revisions
            UNION SELECT interval FROM symbol_runs
        ) WHERE interval <> '' ORDER BY interval COLLATE BINARY
        """,
    )
    run_ids = distinct_values(
        source,
        """
        SELECT run_id FROM (
            SELECT run_id FROM runs UNION SELECT last_success_run_id FROM symbol_state
            UNION SELECT baseline_run_id FROM symbol_state UNION SELECT first_seen_run_id FROM bars
            UNION SELECT last_seen_run_id FROM bars UNION SELECT run_id FROM bar_revisions
            UNION SELECT first_seen_run_id FROM events UNION SELECT last_seen_run_id FROM events
            UNION SELECT run_id FROM event_revisions UNION SELECT run_id FROM symbol_runs
        ) WHERE run_id IS NOT NULL AND run_id <> '' ORDER BY run_id COLLATE BINARY
        """,
    )
    event_types = distinct_values(
        source,
        """
        SELECT event_type FROM (
            SELECT event_type FROM events UNION SELECT event_type FROM event_revisions
        ) WHERE event_type <> '' ORDER BY event_type COLLATE BINARY
        """,
    )
    target.executemany("INSERT INTO symbols(symbol) VALUES(?)", ((v,) for v in symbols))
    target.executemany("INSERT INTO intervals(interval) VALUES(?)", ((v,) for v in intervals))
    target.executemany("INSERT INTO run_ids(run_id) VALUES(?)", ((v,) for v in run_ids))
    target.executemany("INSERT INTO event_types(event_type) VALUES(?)", ((v,) for v in event_types))
    source_pairs: set[tuple[str, str]] = set()
    for table, file_column, sha_column in (
        ("bars", "source_file", "source_sha256"),
        ("events", "source_file", "source_sha256"),
        ("symbol_runs", "raw_file", "raw_sha256"),
    ):
        for row in source.execute(
            f"SELECT DISTINCT {file_column}, {sha_column} FROM {table} "
            f"WHERE {file_column} IS NOT NULL AND {sha_column} IS NOT NULL"
        ):
            source_pairs.add((str(row[0]), str(row[1])))
    target.executemany(
        "INSERT INTO sources(source_file, source_sha256) VALUES(?, ?)",
        ((file_name, sha_to_blob(sha)) for file_name, sha in sorted(source_pairs)),
    )
    target.commit()
    return Lookups(
        symbols={str(r[1]): int(r[0]) for r in target.execute("SELECT symbol_id, symbol FROM symbols")},
        intervals={str(r[1]): int(r[0]) for r in target.execute("SELECT interval_id, interval FROM intervals")},
        runs={str(r[1]): int(r[0]) for r in target.execute("SELECT run_key, run_id FROM run_ids")},
        sources={(str(r[1]), str(blob_to_sha(r[2]))): int(r[0]) for r in target.execute("SELECT source_id, source_file, source_sha256 FROM sources")},
        event_types={str(r[1]): int(r[0]) for r in target.execute("SELECT event_type_id, event_type FROM event_types")},
    )


def load_lookups(target: sqlite3.Connection) -> Lookups:
    return Lookups(
        symbols={str(r[1]): int(r[0]) for r in target.execute("SELECT symbol_id, symbol FROM symbols")},
        intervals={str(r[1]): int(r[0]) for r in target.execute("SELECT interval_id, interval FROM intervals")},
        runs={str(r[1]): int(r[0]) for r in target.execute("SELECT run_key, run_id FROM run_ids")},
        sources={(str(r[1]), str(blob_to_sha(r[2]))): int(r[0]) for r in target.execute("SELECT source_id, source_file, source_sha256 FROM sources")},
        event_types={str(r[1]): int(r[0]) for r in target.execute("SELECT event_type_id, event_type FROM event_types")},
    )


def lookup_required(mapping: Mapping[str, int], value: Any, label: str) -> int:
    key = str(value)
    try:
        return mapping[key]
    except KeyError as exc:
        raise RebuildError(f"Missing compact lookup for {label}: {key}") from exc


def copy_symbol_rows(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    lookups: Lookups,
    symbol: str,
) -> tuple[int, int]:
    symbol_id = lookup_required(lookups.symbols, symbol, "symbol")
    bar_sql = """
        INSERT INTO bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    event_sql = """
        INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    bar_count = 0
    batch: list[tuple[Any, ...]] = []
    for row in source.execute(
        """
        SELECT interval, timestamp_utc, open, high, low, close, adjclose, volume,
               first_seen_run_id, last_seen_run_id, source_file, source_sha256
        FROM bars WHERE symbol=? ORDER BY interval COLLATE BINARY, timestamp_utc
        """,
        (symbol,),
    ):
        batch.append(
            (
                symbol_id,
                lookup_required(lookups.intervals, row[0], "interval"),
                row[1], row[2], row[3], row[4], row[5], row[6], row[7],
                lookup_required(lookups.runs, row[8], "first_seen_run_id"),
                lookup_required(lookups.runs, row[9], "last_seen_run_id"),
                lookups.sources[(str(row[10]), str(row[11]))],
            )
        )
        if len(batch) >= BAR_BATCH_SIZE:
            target.executemany(bar_sql, batch)
            bar_count += len(batch)
            batch.clear()
    if batch:
        target.executemany(bar_sql, batch)
        bar_count += len(batch)
    event_count = 0
    batch = []
    for row in source.execute(
        """
        SELECT interval, event_type, event_timestamp_utc, event_key, event_json,
               first_seen_run_id, last_seen_run_id, source_file, source_sha256
        FROM events WHERE symbol=?
        ORDER BY interval COLLATE BINARY, event_type COLLATE BINARY, event_key COLLATE BINARY
        """,
        (symbol,),
    ):
        batch.append(
            (
                symbol_id,
                lookup_required(lookups.intervals, row[0], "interval"),
                lookup_required(lookups.event_types, row[1], "event_type"),
                row[2], row[3], row[4],
                lookup_required(lookups.runs, row[5], "first_seen_run_id"),
                lookup_required(lookups.runs, row[6], "last_seen_run_id"),
                lookups.sources[(str(row[7]), str(row[8]))],
            )
        )
        if len(batch) >= EVENT_BATCH_SIZE:
            target.executemany(event_sql, batch)
            event_count += len(batch)
            batch.clear()
    if batch:
        target.executemany(event_sql, batch)
        event_count += len(batch)
    target.execute(
        "INSERT INTO rebuild_progress(symbol_id, bars_copied, events_copied, completed_at_utc) VALUES(?, ?, ?, ?)",
        (symbol_id, bar_count, event_count, format_utc(utc_now())),
    )
    target.commit()
    return bar_count, event_count


def copy_small_tables(source: sqlite3.Connection, target: sqlite3.Connection, lookups: Lookups) -> None:
    target.execute("DELETE FROM archive_meta")
    target.executemany("INSERT INTO archive_meta(key, value) VALUES(?, ?)", source.execute("SELECT key, value FROM archive_meta"))
    target.execute("DELETE FROM runs")
    run_rows = []
    for row in source.execute(
        """
        SELECT run_id, mode, interval, overlap_days, started_at_utc, completed_at_utc,
               status, input_file_name, requested_symbols, completed_symbols,
               run_folder_name, utility_version FROM runs ORDER BY run_id COLLATE BINARY
        """
    ):
        run_rows.append((
            lookup_required(lookups.runs, row[0], "run_id"), row[1],
            lookup_required(lookups.intervals, row[2], "interval"),
            *tuple(row[3:]),
        ))
    target.executemany("INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", run_rows)
    target.execute("DELETE FROM symbol_state")
    state_rows = []
    for row in source.execute(
        """
        SELECT symbol, interval, last_bar_timestamp, last_checked_at_utc,
               last_success_run_id, baseline_run_id, full_refresh_required,
               full_refresh_reason FROM symbol_state
        ORDER BY symbol COLLATE BINARY, interval COLLATE BINARY
        """
    ):
        state_rows.append((
            lookup_required(lookups.symbols, row[0], "symbol"),
            lookup_required(lookups.intervals, row[1], "interval"),
            row[2], row[3],
            lookups.runs.get(str(row[4])) if row[4] is not None else None,
            lookups.runs.get(str(row[5])) if row[5] is not None else None,
            row[6], row[7],
        ))
    target.executemany("INSERT INTO symbol_state VALUES (?, ?, ?, ?, ?, ?, ?, ?)", state_rows)
    target.execute("DELETE FROM bar_revisions")
    revision_rows = []
    for row in source.execute(
        """
        SELECT revision_id, run_id, symbol, interval, timestamp_utc, detected_at_utc,
               action, changed_fields_json, old_values_json, new_values_json
        FROM bar_revisions ORDER BY revision_id
        """
    ):
        revision_rows.append((
            row[0], lookup_required(lookups.runs, row[1], "run_id"),
            lookup_required(lookups.symbols, row[2], "symbol"),
            lookup_required(lookups.intervals, row[3], "interval"), *tuple(row[4:]),
        ))
    target.executemany("INSERT INTO bar_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", revision_rows)
    target.execute("DELETE FROM event_revisions")
    revision_rows = []
    for row in source.execute(
        """
        SELECT revision_id, run_id, symbol, interval, event_type, event_timestamp_utc,
               detected_at_utc, action, old_event_json, new_event_json
        FROM event_revisions ORDER BY revision_id
        """
    ):
        revision_rows.append((
            row[0], lookup_required(lookups.runs, row[1], "run_id"),
            lookup_required(lookups.symbols, row[2], "symbol"),
            lookup_required(lookups.intervals, row[3], "interval"),
            lookup_required(lookups.event_types, row[4], "event_type"), *tuple(row[5:]),
        ))
    target.executemany("INSERT INTO event_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", revision_rows)
    target.execute("DELETE FROM symbol_runs")
    symbol_run_rows = []
    for row in source.execute(
        """
        SELECT run_id, task_key, task_sequence, symbol, interval, mode, full_range,
               request_start_epoch, request_end_epoch, classification, http_status,
               bars_returned, new_bars, revised_bars, unchanged_bars, missing_bars,
               events_returned, new_events, revised_events, unchanged_events,
               full_refresh_required, full_refresh_reason, raw_file, raw_sha256,
               elapsed_ms, attempts, error_description
        FROM symbol_runs ORDER BY run_id COLLATE BINARY, task_key COLLATE BINARY
        """
    ):
        raw_source_id = None
        raw_file_fallback = None
        raw_sha_fallback = None
        if row[22] is not None and row[23] is not None:
            raw_source_id = lookups.sources.get((str(row[22]), str(row[23])))
            if raw_source_id is None:
                raw_file_fallback, raw_sha_fallback = row[22], row[23]
        else:
            raw_file_fallback, raw_sha_fallback = row[22], row[23]
        symbol_run_rows.append((
            lookup_required(lookups.runs, row[0], "run_id"), row[1], row[2],
            lookup_required(lookups.symbols, row[3], "symbol"),
            lookup_required(lookups.intervals, row[4], "interval"),
            *tuple(row[5:22]), raw_source_id, raw_file_fallback, raw_sha_fallback,
            row[24], row[25], row[26],
        ))
    target.executemany(
        "INSERT INTO symbol_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        symbol_run_rows,
    )
    target.commit()


def create_indexes(target: sqlite3.Connection) -> None:
    target.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_bars_interval_timestamp_symbol
            ON bars(interval_id, timestamp_utc, symbol_id);
        CREATE INDEX IF NOT EXISTS idx_events_symbol_timestamp
            ON events(symbol_id, interval_id, event_timestamp_utc);
        CREATE INDEX IF NOT EXISTS idx_bar_revisions_run_symbol
            ON bar_revisions(run_key, symbol_id);
        CREATE INDEX IF NOT EXISTS idx_event_revisions_run_symbol
            ON event_revisions(run_key, symbol_id);
        """
    )
    target.commit()


def canonical_bytes(values: Sequence[Any]) -> bytes:
    return (json.dumps(list(values), ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def hash_rows(rows: Iterable[Sequence[Any]]) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0
    for row in rows:
        digest.update(canonical_bytes(tuple(row)))
        count += 1
    return count, digest.hexdigest()


def source_rows(connection: sqlite3.Connection, dataset: str) -> Iterator[tuple[Any, ...]]:
    queries = {
        "archive_meta": "SELECT key, value FROM archive_meta ORDER BY key COLLATE BINARY",
        "runs": """SELECT run_id, mode, interval, overlap_days, started_at_utc, completed_at_utc,
                    status, input_file_name, requested_symbols, completed_symbols,
                    run_folder_name, utility_version FROM runs ORDER BY run_id COLLATE BINARY""",
        "symbol_state": """SELECT symbol, interval, last_bar_timestamp, last_checked_at_utc,
                    last_success_run_id, baseline_run_id, full_refresh_required, full_refresh_reason
                    FROM symbol_state ORDER BY symbol COLLATE BINARY, interval COLLATE BINARY""",
        "bars": """SELECT symbol, interval, timestamp_utc, datetime_utc, open, high, low, close,
                    adjclose, volume, first_seen_run_id, last_seen_run_id, source_file, source_sha256
                    FROM bars ORDER BY symbol COLLATE BINARY, interval COLLATE BINARY, timestamp_utc""",
        "bar_revisions": """SELECT revision_id, run_id, symbol, interval, timestamp_utc, detected_at_utc,
                    action, changed_fields_json, old_values_json, new_values_json
                    FROM bar_revisions ORDER BY revision_id""",
        "events": """SELECT symbol, interval, event_type, event_timestamp_utc, event_key, event_json,
                    first_seen_run_id, last_seen_run_id, source_file, source_sha256
                    FROM events ORDER BY symbol COLLATE BINARY, interval COLLATE BINARY,
                    event_type COLLATE BINARY, event_key COLLATE BINARY""",
        "event_revisions": """SELECT revision_id, run_id, symbol, interval, event_type,
                    event_timestamp_utc, detected_at_utc, action, old_event_json, new_event_json
                    FROM event_revisions ORDER BY revision_id""",
        "symbol_runs": """SELECT run_id, task_key, task_sequence, symbol, interval, mode, full_range,
                    request_start_epoch, request_end_epoch, classification, http_status,
                    bars_returned, new_bars, revised_bars, unchanged_bars, missing_bars,
                    events_returned, new_events, revised_events, unchanged_events,
                    full_refresh_required, full_refresh_reason, raw_file, raw_sha256,
                    elapsed_ms, attempts, error_description
                    FROM symbol_runs ORDER BY run_id COLLATE BINARY, task_key COLLATE BINARY""",
    }
    for row in connection.execute(queries[dataset]):
        yield tuple(row)


def compact_rows(connection: sqlite3.Connection, dataset: str) -> Iterator[tuple[Any, ...]]:
    if dataset == "archive_meta":
        for row in connection.execute("SELECT key, value FROM archive_meta ORDER BY key COLLATE BINARY"):
            yield tuple(row)
    elif dataset == "runs":
        query = """
            SELECT rid.run_id, r.mode, i.interval, r.overlap_days, r.started_at_utc,
                   r.completed_at_utc, r.status, r.input_file_name, r.requested_symbols,
                   r.completed_symbols, r.run_folder_name, r.utility_version
            FROM runs r JOIN run_ids rid ON rid.run_key=r.run_key
            JOIN intervals i ON i.interval_id=r.interval_id
            ORDER BY rid.run_id COLLATE BINARY
        """
        for row in connection.execute(query): yield tuple(row)
    elif dataset == "symbol_state":
        query = """
            SELECT s.symbol, i.interval, st.last_bar_timestamp, st.last_checked_at_utc,
                   lsr.run_id, br.run_id, st.full_refresh_required, st.full_refresh_reason
            FROM symbol_state st JOIN symbols s ON s.symbol_id=st.symbol_id
            JOIN intervals i ON i.interval_id=st.interval_id
            LEFT JOIN run_ids lsr ON lsr.run_key=st.last_success_run_key
            LEFT JOIN run_ids br ON br.run_key=st.baseline_run_key
            ORDER BY s.symbol COLLATE BINARY, i.interval COLLATE BINARY
        """
        for row in connection.execute(query): yield tuple(row)
    elif dataset == "bars":
        query = """
            SELECT s.symbol, i.interval, b.timestamp_utc, b.open, b.high, b.low, b.close,
                   b.adjclose, b.volume, fr.run_id, lr.run_id, src.source_file, src.source_sha256
            FROM bars b JOIN symbols s ON s.symbol_id=b.symbol_id
            JOIN intervals i ON i.interval_id=b.interval_id
            JOIN run_ids fr ON fr.run_key=b.first_seen_run_key
            JOIN run_ids lr ON lr.run_key=b.last_seen_run_key
            JOIN sources src ON src.source_id=b.source_id
            ORDER BY s.symbol COLLATE BINARY, i.interval COLLATE BINARY, b.timestamp_utc
        """
        for row in connection.execute(query):
            yield (row[0], row[1], row[2], epoch_to_utc_text(int(row[2])), *tuple(row[3:12]), blob_to_sha(row[12]))
    elif dataset == "bar_revisions":
        query = """
            SELECT br.revision_id, rid.run_id, s.symbol, i.interval, br.timestamp_utc,
                   br.detected_at_utc, br.action, br.changed_fields_json,
                   br.old_values_json, br.new_values_json
            FROM bar_revisions br JOIN run_ids rid ON rid.run_key=br.run_key
            JOIN symbols s ON s.symbol_id=br.symbol_id
            JOIN intervals i ON i.interval_id=br.interval_id ORDER BY br.revision_id
        """
        for row in connection.execute(query): yield tuple(row)
    elif dataset == "events":
        query = """
            SELECT s.symbol, i.interval, et.event_type, e.event_timestamp_utc, e.event_key,
                   e.event_json, fr.run_id, lr.run_id, src.source_file, src.source_sha256
            FROM events e JOIN symbols s ON s.symbol_id=e.symbol_id
            JOIN intervals i ON i.interval_id=e.interval_id
            JOIN event_types et ON et.event_type_id=e.event_type_id
            JOIN run_ids fr ON fr.run_key=e.first_seen_run_key
            JOIN run_ids lr ON lr.run_key=e.last_seen_run_key
            JOIN sources src ON src.source_id=e.source_id
            ORDER BY s.symbol COLLATE BINARY, i.interval COLLATE BINARY,
                     et.event_type COLLATE BINARY, e.event_key COLLATE BINARY
        """
        for row in connection.execute(query):
            values = list(row)
            values[-1] = blob_to_sha(values[-1])
            yield tuple(values)
    elif dataset == "event_revisions":
        query = """
            SELECT er.revision_id, rid.run_id, s.symbol, i.interval, et.event_type,
                   er.event_timestamp_utc, er.detected_at_utc, er.action,
                   er.old_event_json, er.new_event_json
            FROM event_revisions er JOIN run_ids rid ON rid.run_key=er.run_key
            JOIN symbols s ON s.symbol_id=er.symbol_id
            JOIN intervals i ON i.interval_id=er.interval_id
            JOIN event_types et ON et.event_type_id=er.event_type_id
            ORDER BY er.revision_id
        """
        for row in connection.execute(query): yield tuple(row)
    elif dataset == "symbol_runs":
        query = """
            SELECT rid.run_id, sr.task_key, sr.task_sequence, s.symbol, i.interval, sr.mode,
                   sr.full_range, sr.request_start_epoch, sr.request_end_epoch, sr.classification,
                   sr.http_status, sr.bars_returned, sr.new_bars, sr.revised_bars,
                   sr.unchanged_bars, sr.missing_bars, sr.events_returned, sr.new_events,
                   sr.revised_events, sr.unchanged_events, sr.full_refresh_required,
                   sr.full_refresh_reason, src.source_file, src.source_sha256,
                   sr.raw_file_fallback, sr.raw_sha256_fallback, sr.elapsed_ms, sr.attempts,
                   sr.error_description
            FROM symbol_runs sr JOIN run_ids rid ON rid.run_key=sr.run_key
            JOIN symbols s ON s.symbol_id=sr.symbol_id
            JOIN intervals i ON i.interval_id=sr.interval_id
            LEFT JOIN sources src ON src.source_id=sr.raw_source_id
            ORDER BY rid.run_id COLLATE BINARY, sr.task_key COLLATE BINARY
        """
        for row in connection.execute(query):
            raw_file = row[22] if row[22] is not None else row[24]
            raw_sha = blob_to_sha(row[23]) if row[23] is not None else row[25]
            yield (*tuple(row[:22]), raw_file, raw_sha, *tuple(row[26:29]))
    else:
        raise RebuildError(f"Unknown verification dataset: {dataset}")


def verify_dataset(dataset: str, source: sqlite3.Connection, compact: sqlite3.Connection) -> VerificationResult:
    source_count, source_hash = hash_rows(source_rows(source, dataset))
    compact_count, compact_hash = hash_rows(compact_rows(compact, dataset))
    return VerificationResult(
        dataset=dataset,
        source_rows=source_count,
        compact_rows=compact_count,
        source_sha256=source_hash,
        compact_sha256=compact_hash,
        matches_source=(source_count == compact_count and source_hash == compact_hash),
    )


def write_csv(path: Path, fieldnames: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def human_bytes(value: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    amount = float(value)
    for unit in units:
        if abs(amount) < 1024.0 or unit == units[-1]:
            return f"{amount:.2f} {unit}"
        amount /= 1024.0
    return f"{value} B"


def log_line(output_dir: Path, message: str) -> None:
    print(message, flush=True)
    with (output_dir / "rebuild_progress.log").open("a", encoding="utf-8") as handle:
        handle.write(message + "\n")


def source_identity(database_path: Path) -> dict[str, Any]:
    stat = database_path.stat()
    return {"file_name": database_path.name, "bytes": stat.st_size, "modified_time_ns": stat.st_mtime_ns}


def verify_resume_identity(target: sqlite3.Connection, identity: Mapping[str, Any]) -> None:
    stored = {str(r[0]): str(r[1]) for r in target.execute("SELECT key, value FROM meta")}
    expected = {
        "source_file_name": str(identity["file_name"]),
        "source_bytes": str(identity["bytes"]),
        "source_modified_time_ns": str(identity["modified_time_ns"]),
    }
    for key, value in expected.items():
        if stored.get(key) != value:
            raise RebuildError(f"Resume source identity mismatch for {key}: expected {value}, found {stored.get(key)}")


def run_rebuild(args: argparse.Namespace) -> tuple[Path, dict[str, Any]]:
    started_at = utc_now()
    database_path, database_resolution = resolve_database_path(args.database)
    database_path = validate_source_database(database_path)
    source_before = fingerprint_files(database_path)
    identity = source_identity(database_path)
    if args.resume_dir:
        output_dir = validate_resume_directory(Path(args.resume_dir))
        is_new = False
    else:
        requested = normalize_path(Path(args.output_dir)) if args.output_dir else default_output_directory(database_path, started_at)
        output_dir = validate_new_output_directory(requested)
        is_new = True
    compact_path = output_dir / COMPACT_DATABASE_FILENAME
    source = connect_read_only(database_path)
    target = connect_output(compact_path, new=is_new)
    try:
        require_source_schema(source)
        symbols = source_symbols(source)
        if is_new:
            initialize_compact_database(target)
            lookups = preload_lookups(source, target)
            target.executemany(
                "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
                [
                    ("source_file_name", str(identity["file_name"])),
                    ("source_bytes", str(identity["bytes"])),
                    ("source_modified_time_ns", str(identity["modified_time_ns"])),
                    ("started_at_utc", format_utc(started_at)),
                    ("total_symbols", str(len(symbols))),
                ],
            )
            target.commit()
        else:
            verify_resume_identity(target, identity)
            lookups = load_lookups(target)
        completed_ids = {int(r[0]) for r in target.execute("SELECT symbol_id FROM rebuild_progress")}
        completed_before = len(completed_ids)
        log_line(output_dir, f"Source database: {database_path.name} ({database_resolution})")
        log_line(output_dir, f"Compact rebuild: {'new' if is_new else 'resume'}; source read-only; no network")
        log_line(output_dir, f"Symbols total: {len(symbols):,}; already completed: {completed_before:,}")
        copied_this_run = 0
        bars_this_run = 0
        events_this_run = 0
        phase_start = time.monotonic()
        for sequence, symbol in enumerate(symbols, start=1):
            symbol_id = lookups.symbols[symbol]
            if symbol_id in completed_ids:
                continue
            bars, events = copy_symbol_rows(source, target, lookups, symbol)
            copied_this_run += 1
            bars_this_run += bars
            events_this_run += events
            if copied_this_run == 1 or copied_this_run % args.progress_every == 0 or sequence == len(symbols):
                elapsed = time.monotonic() - phase_start
                log_line(
                    output_dir,
                    f"[{sequence:,}/{len(symbols):,}] {symbol}: bars={bars:,}, events={events:,}; "
                    f"this run symbols={copied_this_run:,}, elapsed={elapsed:.1f}s",
                )
            if args.stop_after_symbols and copied_this_run >= args.stop_after_symbols:
                raise RebuildPaused(
                    f"Controlled pause after {copied_this_run} newly completed symbols. Resume with --resume-dir."
                )
        copy_small_tables(source, target, lookups)
        create_indexes(target)
        target.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('build_status', 'VERIFYING')")
        target.commit()
        datasets = (
            "archive_meta", "runs", "symbol_state", "bars", "bar_revisions",
            "events", "event_revisions", "symbol_runs",
        )
        verifications: list[VerificationResult] = []
        for dataset in datasets:
            log_line(output_dir, f"Verifying {dataset}...")
            result = verify_dataset(dataset, source, target)
            verifications.append(result)
            log_line(
                output_dir,
                f"  {dataset}: source={result.source_rows:,}, compact={result.compact_rows:,}, match={result.matches_source}",
            )
        quick_check_rows = [str(r[0]) for r in target.execute("PRAGMA quick_check")]
        quick_check = "ok" if quick_check_rows == ["ok"] else "; ".join(quick_check_rows)
        foreign_key_rows = list(target.execute("PRAGMA foreign_key_check"))
        foreign_key_ok = not foreign_key_rows
        all_match = all(item.matches_source for item in verifications)
        if not all_match or quick_check != "ok" or not foreign_key_ok:
            target.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('build_status', 'VERIFICATION_FAILED')")
            target.commit()
            raise RebuildError(
                f"Full verification failed: all_tables_match={all_match}, quick_check={quick_check}, "
                f"foreign_key_ok={foreign_key_ok}"
            )
        completed_at = utc_now()
        target.executemany(
            "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
            [
                ("build_status", "VERIFIED_COMPLETE"),
                ("completed_at_utc", format_utc(completed_at)),
                ("source_database_unchanged", "True"),
            ],
        )
        target.commit()
    finally:
        source.close()
        target.close()
    source_after = fingerprint_files(database_path)
    unchanged = main_database_unchanged(source_before, source_after)
    if not unchanged:
        raise RebuildError("The authoritative history.sqlite changed during the compact rebuild.")
    compact_bytes = compact_path.stat().st_size
    source_bytes = database_path.stat().st_size
    reduction = (1.0 - compact_bytes / source_bytes) * 100.0 if source_bytes else 0.0
    manifest: dict[str, Any] = {
        "utility_version": UTILITY_VERSION,
        "started_at_utc": format_utc(started_at),
        "completed_at_utc": format_utc(utc_now()),
        "source_database_file": database_path.name,
        "source_database_resolution": database_resolution,
        "source_database_bytes": source_bytes,
        "source_connection": "URI mode=ro; PRAGMA query_only=ON",
        "source_database_unchanged": unchanged,
        "network_access": False,
        "compact_database_file": compact_path.name,
        "compact_database_bytes": compact_bytes,
        "size_reduction_percent": round(reduction, 3),
        "symbols_total": len(symbols),
        "symbols_completed": int(sqlite3.connect(compact_path).execute("SELECT COUNT(*) FROM rebuild_progress").fetchone()[0]),
        "quick_check": quick_check,
        "foreign_key_check_ok": foreign_key_ok,
        "verification": [asdict(item) for item in verifications],
        "active_archive_switched": False,
        "source_path_persisted": False,
        "output_files": [
            COMPACT_DATABASE_FILENAME, "rebuild_report.txt", "rebuild_manifest.json",
            "verification.csv", "table_counts.csv", "rebuild_progress.log",
        ],
    }
    write_csv(
        output_dir / "verification.csv",
        tuple(asdict(verifications[0]).keys()),
        (asdict(item) for item in verifications),
    )
    write_csv(
        output_dir / "table_counts.csv",
        ("dataset", "source_rows", "compact_rows", "matches_source"),
        ({
            "dataset": item.dataset,
            "source_rows": item.source_rows,
            "compact_rows": item.compact_rows,
            "matches_source": item.matches_source,
        } for item in verifications),
    )
    (output_dir / "rebuild_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    report = [
        "Yahoo Long-History Full Compact Rebuild",
        f"Utility version: {UTILITY_VERSION}",
        f"Started UTC: {manifest['started_at_utc']}",
        f"Completed UTC: {manifest['completed_at_utc']}",
        f"Source connection: {manifest['source_connection']}",
        "Network access: none",
        f"Source database: {database_path.name}",
        f"Source database size: {human_bytes(source_bytes)}",
        f"Source database unchanged: {unchanged}",
        f"Compact database: {compact_path.name}",
        f"Compact database size: {human_bytes(compact_bytes)}",
        f"Size reduction: {reduction:.2f}%",
        f"Symbols completed: {manifest['symbols_completed']:,} of {len(symbols):,}",
        f"Quick check: {quick_check}",
        f"Foreign-key check: {'ok' if foreign_key_ok else 'FAILED'}",
        "",
        "Full logical verification",
    ]
    for item in verifications:
        report.append(
            f"- {item.dataset}: source={item.source_rows:,}; compact={item.compact_rows:,}; "
            f"ordered_sha256_match={item.matches_source}"
        )
    report.extend([
        "",
        "Safety conclusion",
        "- The authoritative history.sqlite was opened read-only and remained unchanged.",
        "- No Yahoo request, source optimization, replacement, migration switch, or deletion occurred.",
        "- history_compact.sqlite is a verified parallel archive, not yet the active incremental-update database.",
        "- Keep history.sqlite until the capture engine is separately adapted and validated for the compact schema.",
    ])
    (output_dir / "rebuild_report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    return output_dir, manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a full verified compact copy of history.sqlite without changing the source."
    )
    parser.add_argument("--database", help="Explicit source history.sqlite path.")
    parser.add_argument("--output-dir", help="New external output directory; must not already exist.")
    parser.add_argument("--resume-dir", help="Existing interrupted compact-rebuild directory to resume.")
    parser.add_argument(
        "--progress-every", type=int, default=25,
        help="Print progress after this many newly completed symbols (default 25).",
    )
    parser.add_argument("--stop-after-symbols", type=int, default=0, help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.output_dir and args.resume_dir:
        print("ERROR: --output-dir and --resume-dir cannot be used together.", file=sys.stderr)
        return 1
    if args.progress_every <= 0:
        print("ERROR: --progress-every must be positive.", file=sys.stderr)
        return 1
    try:
        output_dir, manifest = run_rebuild(args)
    except RebuildPaused as exc:
        print(f"PAUSED: {exc}", file=sys.stderr)
        return 2
    except (RebuildError, FileExistsError, OSError, sqlite3.Error, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Compact database: {manifest['compact_database_file']}")
    print(f"Source database unchanged: {manifest['source_database_unchanged']}")
    print(f"All logical tables verified: {all(v['matches_source'] for v in manifest['verification'])}")
    print(f"Compact size: {human_bytes(manifest['compact_database_bytes'])}")
    print(f"Size reduction: {manifest['size_reduction_percent']:.2f}%")
    print(f"Rebuild folder: {output_dir}")
    print("Primary report: rebuild_report.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
