#!/usr/bin/env python3
"""Build a copy-based compact-schema prototype from the long-history archive.

Version 0.1.0-candidate.4. The authoritative ``history.sqlite`` is opened with
SQLite URI ``mode=ro`` and ``PRAGMA query_only=ON``. Two new subset databases
are written outside the repository: one using the current legacy row layout and
one using a normalized compact layout. Ordered hashes verify that both copies
reconstruct the selected source rows exactly.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

UTILITY_VERSION = "0.1.0-candidate.4"
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
DEFAULT_SYMBOL_LIMIT = 100
MAX_SYMBOL_LIMIT = 500


class PrototypeError(RuntimeError):
    """Raised when a safe compact-schema prototype cannot be completed."""


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
    legacy_rows: int
    compact_rows: int
    source_sha256: str
    legacy_sha256: str
    compact_sha256: str
    legacy_matches_source: bool
    compact_matches_source: bool


@dataclass(frozen=True)
class SizeResult:
    file_name: str
    bytes: int
    mebibytes: float
    percent_of_legacy: float
    reduction_percent: float


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
        raise PrototypeError(f"History timestamp is outside the supported range: {value}") from exc


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
        raise PrototypeError(f"Cannot read local history config {resolved}: {exc}") from exc
    value = payload.get("output_root") if isinstance(payload, Mapping) else None
    if not isinstance(value, str) or not value.strip():
        raise PrototypeError(f"Local history config is missing a non-empty output_root: {resolved}")
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
    return database_path.parent / "prototypes" / f"{filename_utc(started_at)}_compact-schema-prototype"


def validate_source_database(path: Path) -> Path:
    resolved = normalize_path(path)
    if path_is_within(resolved, REPOSITORY_ROOT):
        raise PrototypeError(
            "The authoritative long-history database must remain outside the synchronized repository: "
            f"{resolved}"
        )
    if not resolved.is_file():
        raise PrototypeError(f"SQLite database does not exist: {resolved}")
    return resolved


def validate_output_directory(path: Path) -> Path:
    resolved = normalize_path(path)
    if path_is_within(resolved, REPOSITORY_ROOT):
        raise PrototypeError(
            "Compact-schema prototype files must remain outside the synchronized repository: "
            f"{resolved}"
        )
    resolved.mkdir(parents=True, exist_ok=False)
    probe = resolved / ".write-test"
    probe.write_text("prototype-write-test\n", encoding="utf-8")
    probe.unlink()
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
    main_name = before[0].name
    return before_map.get(main_name) == after_map.get(main_name)


def connect_read_only(database_path: Path) -> sqlite3.Connection:
    resolved = validate_source_database(database_path)
    uri = resolved.as_uri() + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise PrototypeError(f"Cannot open source SQLite database in read-only mode: {exc}") from exc
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA temp_store = MEMORY")
    if int(connection.execute("PRAGMA query_only").fetchone()[0]) != 1:
        connection.close()
        raise PrototypeError("SQLite did not accept PRAGMA query_only=ON.")
    return connection


def connect_output(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA page_size = 4096")
    connection.execute("PRAGMA journal_mode = DELETE")
    connection.execute("PRAGMA synchronous = OFF")
    connection.execute("PRAGMA temp_store = MEMORY")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def require_source_schema(connection: sqlite3.Connection) -> None:
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    required = {"bars", "events", "symbol_state", "runs", "symbol_runs"}
    missing = sorted(required - tables)
    if missing:
        raise PrototypeError(f"Source database is missing required tables: {', '.join(missing)}")


def source_symbols(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT DISTINCT symbol FROM symbol_state WHERE symbol <> '' ORDER BY symbol COLLATE BINARY"
    ).fetchall()
    symbols = [str(row[0]) for row in rows]
    if not symbols:
        rows = connection.execute(
            "SELECT DISTINCT symbol FROM bars WHERE symbol <> '' ORDER BY symbol COLLATE BINARY"
        ).fetchall()
        symbols = [str(row[0]) for row in rows]
    if not symbols:
        raise PrototypeError("Source database contains no symbols.")
    return symbols


def spread_select(symbols: Sequence[str], limit: int) -> list[str]:
    if limit <= 0:
        raise PrototypeError("--symbol-limit must be positive.")
    if limit > MAX_SYMBOL_LIMIT:
        raise PrototypeError(f"--symbol-limit cannot exceed {MAX_SYMBOL_LIMIT} for this prototype.")
    if limit >= len(symbols):
        return list(symbols)
    if limit == 1:
        return [symbols[len(symbols) // 2]]
    indexes = [round(index * (len(symbols) - 1) / (limit - 1)) for index in range(limit)]
    selected: list[str] = []
    seen: set[str] = set()
    for index in indexes:
        symbol = symbols[index]
        if symbol not in seen:
            selected.append(symbol)
            seen.add(symbol)
    for symbol in symbols:
        if len(selected) >= limit:
            break
        if symbol not in seen:
            selected.append(symbol)
            seen.add(symbol)
    return selected


def parse_explicit_symbols(value: str | None, available: Sequence[str]) -> list[str] | None:
    if value is None:
        return None
    requested = [item.strip() for item in value.split(",") if item.strip()]
    if not requested:
        raise PrototypeError("--symbols did not contain any symbols.")
    if len(requested) > MAX_SYMBOL_LIMIT:
        raise PrototypeError(f"--symbols cannot contain more than {MAX_SYMBOL_LIMIT} entries.")
    available_set = set(available)
    missing = [symbol for symbol in requested if symbol not in available_set]
    if missing:
        raise PrototypeError(f"Symbols are not present in the source database: {', '.join(missing)}")
    deduplicated: list[str] = []
    seen: set[str] = set()
    for symbol in requested:
        if symbol not in seen:
            deduplicated.append(symbol)
            seen.add(symbol)
    return deduplicated


def placeholders(count: int) -> str:
    return ",".join("?" for _ in range(count))


def iter_source_bars(connection: sqlite3.Connection, symbols: Sequence[str]) -> Iterator[sqlite3.Row]:
    query = f"""
        SELECT symbol, interval, timestamp_utc, datetime_utc, open, high, low, close,
               adjclose, volume, first_seen_run_id, last_seen_run_id, source_file,
               source_sha256
        FROM bars
        WHERE symbol IN ({placeholders(len(symbols))})
        ORDER BY symbol COLLATE BINARY, interval COLLATE BINARY, timestamp_utc
    """
    yield from connection.execute(query, tuple(symbols))


def iter_source_events(connection: sqlite3.Connection, symbols: Sequence[str]) -> Iterator[sqlite3.Row]:
    query = f"""
        SELECT symbol, interval, event_type, event_timestamp_utc, event_key, event_json,
               first_seen_run_id, last_seen_run_id, source_file, source_sha256
        FROM events
        WHERE symbol IN ({placeholders(len(symbols))})
        ORDER BY symbol COLLATE BINARY, interval COLLATE BINARY, event_type COLLATE BINARY,
                 event_key COLLATE BINARY
    """
    yield from connection.execute(query, tuple(symbols))


def initialize_legacy_database(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE bars (
            symbol TEXT NOT NULL,
            interval TEXT NOT NULL,
            timestamp_utc INTEGER NOT NULL,
            datetime_utc TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            adjclose REAL,
            volume INTEGER,
            first_seen_run_id TEXT NOT NULL,
            last_seen_run_id TEXT NOT NULL,
            source_file TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            PRIMARY KEY (symbol, interval, timestamp_utc)
        );
        CREATE INDEX idx_bars_interval_timestamp ON bars(interval, timestamp_utc);
        CREATE TABLE events (
            symbol TEXT NOT NULL,
            interval TEXT NOT NULL,
            event_type TEXT NOT NULL,
            event_timestamp_utc INTEGER NOT NULL,
            event_key TEXT NOT NULL,
            event_json TEXT NOT NULL,
            first_seen_run_id TEXT NOT NULL,
            last_seen_run_id TEXT NOT NULL,
            source_file TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            PRIMARY KEY (symbol, interval, event_type, event_key)
        );
        CREATE INDEX idx_events_symbol_timestamp ON events(symbol, interval, event_timestamp_utc);
        """
    )


def initialize_compact_database(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        ) WITHOUT ROWID;
        CREATE TABLE symbols (
            symbol_id INTEGER PRIMARY KEY,
            symbol TEXT NOT NULL UNIQUE
        );
        CREATE TABLE intervals (
            interval_id INTEGER PRIMARY KEY,
            interval TEXT NOT NULL UNIQUE
        );
        CREATE TABLE runs (
            run_key INTEGER PRIMARY KEY,
            run_id TEXT NOT NULL UNIQUE
        );
        CREATE TABLE sources (
            source_id INTEGER PRIMARY KEY,
            source_file TEXT NOT NULL,
            source_sha256 BLOB NOT NULL,
            UNIQUE(source_file, source_sha256)
        );
        CREATE TABLE event_types (
            event_type_id INTEGER PRIMARY KEY,
            event_type TEXT NOT NULL UNIQUE
        );
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
            PRIMARY KEY (symbol_id, interval_id, timestamp_utc),
            FOREIGN KEY(symbol_id) REFERENCES symbols(symbol_id),
            FOREIGN KEY(interval_id) REFERENCES intervals(interval_id),
            FOREIGN KEY(first_seen_run_key) REFERENCES runs(run_key),
            FOREIGN KEY(last_seen_run_key) REFERENCES runs(run_key),
            FOREIGN KEY(source_id) REFERENCES sources(source_id)
        ) WITHOUT ROWID;
        CREATE INDEX idx_bars_interval_timestamp_symbol
            ON bars(interval_id, timestamp_utc, symbol_id);
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
            PRIMARY KEY (symbol_id, interval_id, event_type_id, event_key),
            FOREIGN KEY(symbol_id) REFERENCES symbols(symbol_id),
            FOREIGN KEY(interval_id) REFERENCES intervals(interval_id),
            FOREIGN KEY(event_type_id) REFERENCES event_types(event_type_id),
            FOREIGN KEY(first_seen_run_key) REFERENCES runs(run_key),
            FOREIGN KEY(last_seen_run_key) REFERENCES runs(run_key),
            FOREIGN KEY(source_id) REFERENCES sources(source_id)
        ) WITHOUT ROWID;
        CREATE INDEX idx_events_symbol_timestamp
            ON events(symbol_id, interval_id, event_timestamp_utc);
        """
    )
    connection.executemany(
        "INSERT INTO meta(key, value) VALUES(?, ?)",
        [
            ("schema_name", "compact_history_prototype"),
            ("schema_version", "1"),
            ("utility_version", UTILITY_VERSION),
        ],
    )


def sha_to_blob(value: str) -> bytes:
    text = str(value)
    try:
        decoded = bytes.fromhex(text)
    except ValueError:
        return text.encode("utf-8")
    return decoded


def blob_to_sha(value: bytes | str) -> str:
    if isinstance(value, str):
        return value
    if len(value) == 32:
        return value.hex()
    return value.decode("utf-8")


def insert_legacy_rows(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    symbols: Sequence[str],
) -> tuple[int, int]:
    bar_sql = "INSERT INTO bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    event_sql = "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    bar_count = 0
    event_count = 0
    batch: list[tuple[Any, ...]] = []
    for row in iter_source_bars(source, symbols):
        batch.append(tuple(row))
        if len(batch) >= 10_000:
            target.executemany(bar_sql, batch)
            bar_count += len(batch)
            batch.clear()
    if batch:
        target.executemany(bar_sql, batch)
        bar_count += len(batch)
    batch = []
    for row in iter_source_events(source, symbols):
        batch.append(tuple(row))
        if len(batch) >= 5_000:
            target.executemany(event_sql, batch)
            event_count += len(batch)
            batch.clear()
    if batch:
        target.executemany(event_sql, batch)
        event_count += len(batch)
    target.commit()
    return bar_count, event_count


class CompactLookups:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.symbols: dict[str, int] = {}
        self.intervals: dict[str, int] = {}
        self.runs: dict[str, int] = {}
        self.sources: dict[tuple[str, str], int] = {}
        self.event_types: dict[str, int] = {}

    def _id(self, table: str, id_column: str, value_column: str, value: str, cache: dict[str, int]) -> int:
        existing = cache.get(value)
        if existing is not None:
            return existing
        cursor = self.connection.execute(
            f"INSERT INTO {table}({value_column}) VALUES(?)",
            (value,),
        )
        result = int(cursor.lastrowid)
        cache[value] = result
        return result

    def symbol(self, value: str) -> int:
        return self._id("symbols", "symbol_id", "symbol", value, self.symbols)

    def interval(self, value: str) -> int:
        return self._id("intervals", "interval_id", "interval", value, self.intervals)

    def run(self, value: str) -> int:
        return self._id("runs", "run_key", "run_id", value, self.runs)

    def event_type(self, value: str) -> int:
        return self._id("event_types", "event_type_id", "event_type", value, self.event_types)

    def source(self, source_file: str, source_sha256: str) -> int:
        key = (source_file, source_sha256)
        existing = self.sources.get(key)
        if existing is not None:
            return existing
        cursor = self.connection.execute(
            "INSERT INTO sources(source_file, source_sha256) VALUES(?, ?)",
            (source_file, sha_to_blob(source_sha256)),
        )
        result = int(cursor.lastrowid)
        self.sources[key] = result
        return result


def insert_compact_rows(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    symbols: Sequence[str],
) -> tuple[int, int]:
    lookups = CompactLookups(target)
    bar_sql = """
        INSERT INTO bars(
            symbol_id, interval_id, timestamp_utc, open, high, low, close, adjclose,
            volume, first_seen_run_key, last_seen_run_key, source_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    event_sql = """
        INSERT INTO events(
            symbol_id, interval_id, event_type_id, event_timestamp_utc, event_key,
            event_json, first_seen_run_key, last_seen_run_key, source_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    bar_count = 0
    event_count = 0
    batch: list[tuple[Any, ...]] = []
    for row in iter_source_bars(source, symbols):
        batch.append(
            (
                lookups.symbol(str(row["symbol"])),
                lookups.interval(str(row["interval"])),
                int(row["timestamp_utc"]),
                row["open"],
                row["high"],
                row["low"],
                row["close"],
                row["adjclose"],
                row["volume"],
                lookups.run(str(row["first_seen_run_id"])),
                lookups.run(str(row["last_seen_run_id"])),
                lookups.source(str(row["source_file"]), str(row["source_sha256"])),
            )
        )
        if len(batch) >= 10_000:
            target.executemany(bar_sql, batch)
            bar_count += len(batch)
            batch.clear()
    if batch:
        target.executemany(bar_sql, batch)
        bar_count += len(batch)
    batch = []
    for row in iter_source_events(source, symbols):
        batch.append(
            (
                lookups.symbol(str(row["symbol"])),
                lookups.interval(str(row["interval"])),
                lookups.event_type(str(row["event_type"])),
                int(row["event_timestamp_utc"]),
                str(row["event_key"]),
                str(row["event_json"]),
                lookups.run(str(row["first_seen_run_id"])),
                lookups.run(str(row["last_seen_run_id"])),
                lookups.source(str(row["source_file"]), str(row["source_sha256"])),
            )
        )
        if len(batch) >= 5_000:
            target.executemany(event_sql, batch)
            event_count += len(batch)
            batch.clear()
    if batch:
        target.executemany(event_sql, batch)
        event_count += len(batch)
    target.commit()
    return bar_count, event_count


def canonical_bytes(values: Sequence[Any]) -> bytes:
    return (json.dumps(list(values), ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def hash_rows(rows: Iterable[Sequence[Any]]) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0
    for row in rows:
        digest.update(canonical_bytes(tuple(row)))
        count += 1
    return count, digest.hexdigest()


def source_bar_rows(connection: sqlite3.Connection, symbols: Sequence[str]) -> Iterator[tuple[Any, ...]]:
    for row in iter_source_bars(connection, symbols):
        yield tuple(row)


def source_event_rows(connection: sqlite3.Connection, symbols: Sequence[str]) -> Iterator[tuple[Any, ...]]:
    for row in iter_source_events(connection, symbols):
        yield tuple(row)


def legacy_bar_rows(connection: sqlite3.Connection) -> Iterator[tuple[Any, ...]]:
    query = """
        SELECT symbol, interval, timestamp_utc, datetime_utc, open, high, low, close,
               adjclose, volume, first_seen_run_id, last_seen_run_id, source_file,
               source_sha256
        FROM bars
        ORDER BY symbol COLLATE BINARY, interval COLLATE BINARY, timestamp_utc
    """
    for row in connection.execute(query):
        yield tuple(row)


def legacy_event_rows(connection: sqlite3.Connection) -> Iterator[tuple[Any, ...]]:
    query = """
        SELECT symbol, interval, event_type, event_timestamp_utc, event_key, event_json,
               first_seen_run_id, last_seen_run_id, source_file, source_sha256
        FROM events
        ORDER BY symbol COLLATE BINARY, interval COLLATE BINARY, event_type COLLATE BINARY,
                 event_key COLLATE BINARY
    """
    for row in connection.execute(query):
        yield tuple(row)


def compact_bar_rows(connection: sqlite3.Connection) -> Iterator[tuple[Any, ...]]:
    query = """
        SELECT s.symbol, i.interval, b.timestamp_utc, b.open, b.high, b.low, b.close,
               b.adjclose, b.volume, fr.run_id, lr.run_id, src.source_file,
               src.source_sha256
        FROM bars b
        JOIN symbols s ON s.symbol_id = b.symbol_id
        JOIN intervals i ON i.interval_id = b.interval_id
        JOIN runs fr ON fr.run_key = b.first_seen_run_key
        JOIN runs lr ON lr.run_key = b.last_seen_run_key
        JOIN sources src ON src.source_id = b.source_id
        ORDER BY s.symbol COLLATE BINARY, i.interval COLLATE BINARY, b.timestamp_utc
    """
    for row in connection.execute(query):
        yield (
            row[0],
            row[1],
            row[2],
            epoch_to_utc_text(int(row[2])),
            row[3],
            row[4],
            row[5],
            row[6],
            row[7],
            row[8],
            row[9],
            row[10],
            row[11],
            blob_to_sha(row[12]),
        )


def compact_event_rows(connection: sqlite3.Connection) -> Iterator[tuple[Any, ...]]:
    query = """
        SELECT s.symbol, i.interval, et.event_type, e.event_timestamp_utc,
               e.event_key, e.event_json, fr.run_id, lr.run_id, src.source_file,
               src.source_sha256
        FROM events e
        JOIN symbols s ON s.symbol_id = e.symbol_id
        JOIN intervals i ON i.interval_id = e.interval_id
        JOIN event_types et ON et.event_type_id = e.event_type_id
        JOIN runs fr ON fr.run_key = e.first_seen_run_key
        JOIN runs lr ON lr.run_key = e.last_seen_run_key
        JOIN sources src ON src.source_id = e.source_id
        ORDER BY s.symbol COLLATE BINARY, i.interval COLLATE BINARY,
                 et.event_type COLLATE BINARY, e.event_key COLLATE BINARY
    """
    for row in connection.execute(query):
        yield (
            row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7],
            row[8], blob_to_sha(row[9])
        )


def verify_dataset(
    dataset: str,
    source_rows: Iterable[Sequence[Any]],
    legacy_rows: Iterable[Sequence[Any]],
    compact_rows: Iterable[Sequence[Any]],
) -> VerificationResult:
    source_count, source_hash = hash_rows(source_rows)
    legacy_count, legacy_hash = hash_rows(legacy_rows)
    compact_count, compact_hash = hash_rows(compact_rows)
    return VerificationResult(
        dataset=dataset,
        source_rows=source_count,
        legacy_rows=legacy_count,
        compact_rows=compact_count,
        source_sha256=source_hash,
        legacy_sha256=legacy_hash,
        compact_sha256=compact_hash,
        legacy_matches_source=(source_count == legacy_count and source_hash == legacy_hash),
        compact_matches_source=(source_count == compact_count and source_hash == compact_hash),
    )


def datetime_mismatch_count(source: sqlite3.Connection, symbols: Sequence[str]) -> int:
    mismatches = 0
    for row in iter_source_bars(source, symbols):
        if str(row["datetime_utc"]) != epoch_to_utc_text(int(row["timestamp_utc"])):
            mismatches += 1
    return mismatches


def integrity_result(connection: sqlite3.Connection) -> str:
    rows = [str(row[0]) for row in connection.execute("PRAGMA quick_check")]
    return "; ".join(rows)


def write_csv(path: Path, fieldnames: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def human_bytes(value: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    amount = float(value)
    for unit in units:
        if amount < 1024.0 or unit == units[-1]:
            return f"{amount:.2f} {unit}"
        amount /= 1024.0
    return f"{value} B"


def run_prototype(args: argparse.Namespace) -> tuple[Path, dict[str, Any]]:
    if args.symbol_limit <= 0:
        raise PrototypeError("--symbol-limit must be positive.")
    started_at = utc_now()
    database_path, database_source = resolve_database_path(args.database)
    database_path = validate_source_database(database_path)
    output_dir = validate_output_directory(
        Path(args.output_dir) if args.output_dir else default_output_directory(database_path, started_at)
    )
    source_before = fingerprint_files(database_path)
    source = connect_read_only(database_path)
    legacy_path = output_dir / "legacy_subset.sqlite"
    compact_path = output_dir / "compact_subset.sqlite"
    try:
        require_source_schema(source)
        available = source_symbols(source)
        explicit = parse_explicit_symbols(args.symbols, available)
        selected = explicit if explicit is not None else spread_select(available, args.symbol_limit)
        legacy = connect_output(legacy_path)
        compact = connect_output(compact_path)
        try:
            initialize_legacy_database(legacy)
            initialize_compact_database(compact)
            legacy_bars, legacy_events = insert_legacy_rows(source, legacy, selected)
            compact_bars, compact_events = insert_compact_rows(source, compact, selected)
            legacy_integrity = integrity_result(legacy)
            compact_integrity = integrity_result(compact)
            verifications = [
                verify_dataset(
                    "bars",
                    source_bar_rows(source, selected),
                    legacy_bar_rows(legacy),
                    compact_bar_rows(compact),
                ),
                verify_dataset(
                    "events",
                    source_event_rows(source, selected),
                    legacy_event_rows(legacy),
                    compact_event_rows(compact),
                ),
            ]
            date_mismatches = datetime_mismatch_count(source, selected)
        finally:
            legacy.close()
            compact.close()
    finally:
        source.close()
    source_after = fingerprint_files(database_path)
    unchanged = main_database_unchanged(source_before, source_after)
    if not unchanged:
        raise PrototypeError("The source history.sqlite file changed during the prototype run.")
    if any(not item.legacy_matches_source or not item.compact_matches_source for item in verifications):
        raise PrototypeError("Prototype verification failed; see the generated report folder.")
    if date_mismatches:
        raise PrototypeError(
            f"Compact datetime derivation differs from {date_mismatches} source bars; the compact copy was retained for review."
        )
    legacy_bytes = legacy_path.stat().st_size
    compact_bytes = compact_path.stat().st_size
    percent_of_legacy = (compact_bytes / legacy_bytes * 100.0) if legacy_bytes else 0.0
    reduction_percent = (1.0 - compact_bytes / legacy_bytes) * 100.0 if legacy_bytes else 0.0
    sizes = [
        SizeResult("legacy_subset.sqlite", legacy_bytes, round(legacy_bytes / 1048576, 3), 100.0, 0.0),
        SizeResult(
            "compact_subset.sqlite",
            compact_bytes,
            round(compact_bytes / 1048576, 3),
            round(percent_of_legacy, 3),
            round(reduction_percent, 3),
        ),
    ]
    completed_at = utc_now()
    manifest: dict[str, Any] = {
        "utility_version": UTILITY_VERSION,
        "started_at_utc": format_utc(started_at),
        "completed_at_utc": format_utc(completed_at),
        "source_database_file": database_path.name,
        "source_database_resolution": database_source,
        "source_database_bytes": database_path.stat().st_size,
        "source_connection": "URI mode=ro; PRAGMA query_only=ON",
        "source_database_unchanged": unchanged,
        "network_access": False,
        "selection_method": "explicit" if explicit is not None else "alphabetic_spread",
        "available_symbols": len(available),
        "selected_symbols": len(selected),
        "legacy_bar_rows_inserted": legacy_bars,
        "legacy_event_rows_inserted": legacy_events,
        "compact_bar_rows_inserted": compact_bars,
        "compact_event_rows_inserted": compact_events,
        "datetime_derivation_mismatches": date_mismatches,
        "legacy_quick_check": legacy_integrity,
        "compact_quick_check": compact_integrity,
        "legacy_bytes": legacy_bytes,
        "compact_bytes": compact_bytes,
        "compact_percent_of_legacy": round(percent_of_legacy, 3),
        "size_reduction_percent": round(reduction_percent, 3),
        "verification": [asdict(item) for item in verifications],
        "output_files": [
            "prototype_report.txt",
            "prototype_manifest.json",
            "selected_symbols.csv",
            "size_comparison.csv",
            "verification.csv",
            legacy_path.name,
            compact_path.name,
        ],
        "absolute_source_path_persisted": False,
    }
    write_csv(
        output_dir / "selected_symbols.csv",
        ("selection_order", "symbol"),
        ({"selection_order": index, "symbol": symbol} for index, symbol in enumerate(selected, start=1)),
    )
    write_csv(
        output_dir / "size_comparison.csv",
        ("file_name", "bytes", "mebibytes", "percent_of_legacy", "reduction_percent"),
        (asdict(item) for item in sizes),
    )
    write_csv(
        output_dir / "verification.csv",
        tuple(asdict(verifications[0]).keys()),
        (asdict(item) for item in verifications),
    )
    (output_dir / "prototype_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report_lines = [
        "Yahoo Long-History Compact-Schema Prototype",
        f"Utility version: {UTILITY_VERSION}",
        f"Started UTC: {manifest['started_at_utc']}",
        f"Completed UTC: {manifest['completed_at_utc']}",
        f"Source connection: {manifest['source_connection']}",
        "Network access: none",
        f"Source database: {database_path.name}",
        f"Source database size: {human_bytes(database_path.stat().st_size)}",
        f"Source database unchanged: {unchanged}",
        "",
        "Prototype selection",
        f"- Available symbols: {len(available):,}",
        f"- Selected symbols: {len(selected):,}",
        f"- Selection method: {manifest['selection_method']}",
        f"- Bars copied: {legacy_bars:,}",
        f"- Events copied: {legacy_events:,}",
        "",
        "Verification",
        f"- Legacy quick_check: {legacy_integrity}",
        f"- Compact quick_check: {compact_integrity}",
        f"- Derived datetime mismatches: {date_mismatches:,}",
    ]
    for item in verifications:
        report_lines.append(
            f"- {item.dataset}: source={item.source_rows:,}; legacy={item.legacy_rows:,}; "
            f"compact={item.compact_rows:,}; legacy_match={item.legacy_matches_source}; "
            f"compact_match={item.compact_matches_source}"
        )
    report_lines.extend(
        [
            "",
            "Size comparison",
            f"- Legacy subset: {human_bytes(legacy_bytes)}",
            f"- Compact subset: {human_bytes(compact_bytes)}",
            f"- Compact as percent of legacy: {percent_of_legacy:.2f}%",
            f"- Size reduction: {reduction_percent:.2f}%",
            "",
            "Compact-schema changes",
            "- Symbol, interval, run, event-type, and source provenance text is normalized into lookup tables.",
            "- SHA-256 provenance is stored once per source as a 32-byte value instead of 64-character text on every row.",
            "- datetime_utc is derived from timestamp_utc rather than stored on every bar.",
            "- Bars and events use WITHOUT ROWID composite primary-key tables.",
            "- A date-first bars index is retained for cross-symbol time-series exports.",
            "",
            "Safety conclusion",
            "- The source database was opened read-only and remained unchanged.",
            "- The two prototype databases are independent copies and are not authoritative archives.",
            "- No migration, replacement, deletion, or Yahoo request was performed.",
        ]
    )
    (output_dir / "prototype_report.txt").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    return output_dir, manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build legacy and compact SQLite subset copies without changing history.sqlite."
    )
    parser.add_argument("--database", help="Explicit source history.sqlite path.")
    parser.add_argument("--output-dir", help="New external output directory; must not already exist.")
    parser.add_argument(
        "--symbol-limit",
        type=int,
        default=DEFAULT_SYMBOL_LIMIT,
        help=f"Alphabetically spread symbol count when --symbols is omitted (default {DEFAULT_SYMBOL_LIMIT}; max {MAX_SYMBOL_LIMIT}).",
    )
    parser.add_argument(
        "--symbols",
        help="Comma-separated explicit symbols present in the source database; overrides --symbol-limit.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        output_dir, manifest = run_prototype(args)
    except (PrototypeError, FileExistsError, OSError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Source database: {manifest['source_database_file']} ({manifest['source_database_resolution']})")
    print("Source mode: read-only; no network; no source optimization")
    print(f"Selected symbols: {manifest['selected_symbols']:,}")
    print(f"Bars copied: {manifest['legacy_bar_rows_inserted']:,}")
    print(f"Events copied: {manifest['legacy_event_rows_inserted']:,}")
    print(f"Legacy subset: {human_bytes(manifest['legacy_bytes'])}")
    print(f"Compact subset: {human_bytes(manifest['compact_bytes'])}")
    print(f"Size reduction: {manifest['size_reduction_percent']:.2f}%")
    print(f"Source database unchanged: {manifest['source_database_unchanged']}")
    print(f"Prototype folder: {output_dir}")
    print("Primary report: prototype_report.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
