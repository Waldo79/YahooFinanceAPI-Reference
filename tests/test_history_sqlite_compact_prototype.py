from __future__ import annotations

import csv
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "capture-utility" / "history_sqlite_compact_prototype.py"
SPEC = importlib.util.spec_from_file_location("history_sqlite_compact_prototype_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
prototype = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = prototype
SPEC.loader.exec_module(prototype)


def create_source_database(path: Path, *, symbol_count: int = 12, bars_per_symbol: int = 120) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE archive_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY,
            mode TEXT NOT NULL,
            interval TEXT NOT NULL,
            overlap_days INTEGER NOT NULL,
            started_at_utc TEXT NOT NULL,
            completed_at_utc TEXT,
            status TEXT NOT NULL,
            input_file_name TEXT NOT NULL,
            requested_symbols INTEGER NOT NULL,
            completed_symbols INTEGER NOT NULL DEFAULT 0,
            run_folder_name TEXT NOT NULL,
            utility_version TEXT NOT NULL
        );
        CREATE TABLE symbol_state (
            symbol TEXT NOT NULL,
            interval TEXT NOT NULL,
            last_bar_timestamp INTEGER,
            last_checked_at_utc TEXT,
            last_success_run_id TEXT,
            baseline_run_id TEXT,
            full_refresh_required INTEGER NOT NULL DEFAULT 0,
            full_refresh_reason TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (symbol, interval)
        );
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
        CREATE TABLE bar_revisions (
            revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            interval TEXT NOT NULL,
            timestamp_utc INTEGER NOT NULL,
            detected_at_utc TEXT NOT NULL,
            action TEXT NOT NULL,
            changed_fields_json TEXT NOT NULL,
            old_values_json TEXT,
            new_values_json TEXT
        );
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
        CREATE TABLE event_revisions (
            revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            interval TEXT NOT NULL,
            event_type TEXT NOT NULL,
            event_timestamp_utc INTEGER NOT NULL,
            detected_at_utc TEXT NOT NULL,
            action TEXT NOT NULL,
            old_event_json TEXT,
            new_event_json TEXT
        );
        CREATE TABLE symbol_runs (
            run_id TEXT NOT NULL,
            task_key TEXT NOT NULL,
            task_sequence INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            interval TEXT NOT NULL,
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
            raw_file TEXT,
            raw_sha256 TEXT,
            elapsed_ms INTEGER NOT NULL,
            attempts INTEGER NOT NULL,
            error_description TEXT,
            PRIMARY KEY (run_id, task_key)
        );
        """
    )
    run_id = "2026-08-05T23-31-43.065Z_history-run"
    connection.execute(
        "INSERT INTO runs VALUES (?, 'baseline', '1d', 30, '2026-08-05T23:31:43Z', '2026-08-06T00:00:00Z', 'COMPLETE', 'symbols.csv', ?, ?, 'run-folder', '0.1.0')",
        (run_id, symbol_count, symbol_count),
    )
    epoch = 1_600_000_000
    for symbol_index in range(symbol_count):
        symbol = f"SYM{symbol_index:03d}"
        source_file = f"runs/run-folder/raw/{symbol}.json.gz"
        source_sha = hashlib_for_symbol(symbol)
        last_timestamp = epoch + (bars_per_symbol - 1) * 86_400
        connection.execute(
            "INSERT INTO symbol_state VALUES (?, '1d', ?, '2026-08-06T00:00:00Z', ?, ?, 0, '')",
            (symbol, last_timestamp, run_id, run_id),
        )
        rows = []
        for bar_index in range(bars_per_symbol):
            timestamp = epoch + bar_index * 86_400
            rows.append(
                (
                    symbol,
                    "1d",
                    timestamp,
                    prototype.epoch_to_utc_text(timestamp),
                    100.0 + symbol_index,
                    101.0 + symbol_index,
                    99.0 + symbol_index,
                    100.5 + symbol_index,
                    100.4 + symbol_index,
                    1_000_000 + bar_index,
                    run_id,
                    run_id,
                    source_file,
                    source_sha,
                )
            )
        connection.executemany("INSERT INTO bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        event_timestamp = epoch + 30 * 86_400
        event_json = json.dumps({"amount": 0.25 + symbol_index / 100.0, "date": event_timestamp}, sort_keys=True, separators=(",", ":"))
        connection.execute(
            "INSERT INTO events VALUES (?, '1d', 'DIVIDEND', ?, ?, ?, ?, ?, ?, ?)",
            (
                symbol,
                event_timestamp,
                f"{event_timestamp}:event-{symbol}",
                event_json,
                run_id,
                run_id,
                source_file,
                source_sha,
            ),
        )
        connection.execute(
            "INSERT INTO symbol_runs VALUES (?, ?, ?, ?, '1d', 'baseline', 1, ?, ?, 'SUCCESS_HISTORY_RETURNED', 200, ?, ?, 0, 0, 0, 1, 1, 0, 0, 0, '', ?, ?, 10, 1, NULL)",
            (
                run_id,
                f"task-{symbol}",
                symbol_index + 1,
                symbol,
                epoch,
                last_timestamp + 86_400,
                bars_per_symbol,
                bars_per_symbol,
                source_file,
                source_sha,
            ),
        )
    connection.commit()
    connection.close()


def hashlib_for_symbol(symbol: str) -> str:
    import hashlib

    return hashlib.sha256(symbol.encode("utf-8")).hexdigest()


def test_spread_select_is_deterministic_and_includes_endpoints() -> None:
    symbols = [f"S{index:03d}" for index in range(20)]
    selected = prototype.spread_select(symbols, 5)
    assert selected == ["S000", "S005", "S010", "S014", "S019"]
    assert prototype.spread_select(symbols, 25) == symbols


def test_connect_read_only_rejects_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "history.sqlite"
    create_source_database(source)
    monkeypatch.setattr(prototype, "REPOSITORY_ROOT", tmp_path / "different-repo")
    connection = prototype.connect_read_only(source)
    assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
    with pytest.raises(sqlite3.OperationalError):
        connection.execute("DELETE FROM bars")
    connection.close()


def test_full_prototype_matches_source_and_reduces_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "archive" / "history.sqlite"
    source.parent.mkdir()
    create_source_database(source, symbol_count=16, bars_per_symbol=300)
    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.setattr(prototype, "REPOSITORY_ROOT", repository)
    output = tmp_path / "prototype-output"
    before = prototype.fingerprint_files(source)
    args = prototype.build_parser().parse_args(
        ["--database", str(source), "--output-dir", str(output), "--symbol-limit", "8"]
    )
    output_dir, manifest = prototype.run_prototype(args)
    after = prototype.fingerprint_files(source)
    assert before == after
    assert manifest["source_database_unchanged"] is True
    assert manifest["network_access"] is False
    assert manifest["selected_symbols"] == 8
    assert manifest["legacy_bar_rows_inserted"] == 8 * 300
    assert manifest["compact_bar_rows_inserted"] == 8 * 300
    assert manifest["legacy_event_rows_inserted"] == 8
    assert manifest["compact_event_rows_inserted"] == 8
    assert manifest["datetime_derivation_mismatches"] == 0
    assert all(item["legacy_matches_source"] for item in manifest["verification"])
    assert all(item["compact_matches_source"] for item in manifest["verification"])
    assert manifest["compact_bytes"] < manifest["legacy_bytes"]
    assert manifest["size_reduction_percent"] > 20.0
    assert (output_dir / "prototype_report.txt").is_file()
    assert (output_dir / "legacy_subset.sqlite").is_file()
    assert (output_dir / "compact_subset.sqlite").is_file()


def test_explicit_symbols_override_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "history.sqlite"
    create_source_database(source, symbol_count=6, bars_per_symbol=20)
    monkeypatch.setattr(prototype, "REPOSITORY_ROOT", tmp_path / "repo")
    output = tmp_path / "out"
    args = prototype.build_parser().parse_args(
        [
            "--database",
            str(source),
            "--output-dir",
            str(output),
            "--symbol-limit",
            "1",
            "--symbols",
            "SYM004,SYM001,SYM004",
        ]
    )
    _, manifest = prototype.run_prototype(args)
    assert manifest["selection_method"] == "explicit"
    assert manifest["selected_symbols"] == 2
    with (output / "selected_symbols.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["symbol"] for row in rows] == ["SYM004", "SYM001"]


def test_compact_schema_normalizes_provenance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "history.sqlite"
    create_source_database(source, symbol_count=4, bars_per_symbol=40)
    monkeypatch.setattr(prototype, "REPOSITORY_ROOT", tmp_path / "repo")
    output = tmp_path / "out"
    args = prototype.build_parser().parse_args(
        ["--database", str(source), "--output-dir", str(output), "--symbol-limit", "4"]
    )
    prototype.run_prototype(args)
    connection = sqlite3.connect(output / "compact_subset.sqlite")
    columns = [row[1] for row in connection.execute("PRAGMA table_info(bars)")]
    assert "symbol" not in columns
    assert "datetime_utc" not in columns
    assert "source_file" not in columns
    assert "source_sha256" not in columns
    assert connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 4
    assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    connection.close()


def test_source_inside_repository_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    source = repository / "history.sqlite"
    create_source_database(source)
    monkeypatch.setattr(prototype, "REPOSITORY_ROOT", repository)
    with pytest.raises(prototype.PrototypeError, match="outside"):
        prototype.validate_source_database(source)


def test_output_inside_repository_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    monkeypatch.setattr(prototype, "REPOSITORY_ROOT", repository)
    with pytest.raises(prototype.PrototypeError, match="outside"):
        prototype.validate_output_directory(repository / "prototype")


def test_missing_symbol_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "history.sqlite"
    create_source_database(source, symbol_count=3, bars_per_symbol=5)
    monkeypatch.setattr(prototype, "REPOSITORY_ROOT", tmp_path / "repo")
    args = prototype.build_parser().parse_args(
        ["--database", str(source), "--output-dir", str(tmp_path / "out"), "--symbols", "MISSING"]
    )
    with pytest.raises(prototype.PrototypeError, match="not present"):
        prototype.run_prototype(args)


def test_source_has_no_network_import_or_source_mutation_sql() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "urlopen" not in source
    assert "requests" not in source
    assert "http.client" not in source
    assert "UPDATE bars" not in source
    assert "DELETE FROM bars" not in source
    assert "VACUUM" not in source
    assert "REINDEX" not in source
