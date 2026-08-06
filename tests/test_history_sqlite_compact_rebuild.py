from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "capture-utility" / "history_sqlite_compact_rebuild.py"
SPEC = importlib.util.spec_from_file_location("history_sqlite_compact_rebuild_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
rebuild = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = rebuild
SPEC.loader.exec_module(rebuild)


def sha(symbol: str) -> str:
    return hashlib.sha256(symbol.encode()).hexdigest()


def create_source(path: Path, symbols: int = 6, bars_each: int = 40) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE archive_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE runs (run_id TEXT PRIMARY KEY, mode TEXT NOT NULL, interval TEXT NOT NULL,
          overlap_days INTEGER NOT NULL, started_at_utc TEXT NOT NULL, completed_at_utc TEXT,
          status TEXT NOT NULL, input_file_name TEXT NOT NULL, requested_symbols INTEGER NOT NULL,
          completed_symbols INTEGER NOT NULL DEFAULT 0, run_folder_name TEXT NOT NULL,
          utility_version TEXT NOT NULL);
        CREATE TABLE symbol_state (symbol TEXT NOT NULL, interval TEXT NOT NULL,
          last_bar_timestamp INTEGER, last_checked_at_utc TEXT, last_success_run_id TEXT,
          baseline_run_id TEXT, full_refresh_required INTEGER NOT NULL DEFAULT 0,
          full_refresh_reason TEXT NOT NULL DEFAULT '', PRIMARY KEY(symbol, interval));
        CREATE TABLE bars (symbol TEXT NOT NULL, interval TEXT NOT NULL, timestamp_utc INTEGER NOT NULL,
          datetime_utc TEXT NOT NULL, open REAL, high REAL, low REAL, close REAL, adjclose REAL,
          volume INTEGER, first_seen_run_id TEXT NOT NULL, last_seen_run_id TEXT NOT NULL,
          source_file TEXT NOT NULL, source_sha256 TEXT NOT NULL,
          PRIMARY KEY(symbol, interval, timestamp_utc));
        CREATE INDEX idx_bars_interval_timestamp ON bars(interval, timestamp_utc);
        CREATE TABLE bar_revisions (revision_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
          symbol TEXT NOT NULL, interval TEXT NOT NULL, timestamp_utc INTEGER NOT NULL,
          detected_at_utc TEXT NOT NULL, action TEXT NOT NULL, changed_fields_json TEXT NOT NULL,
          old_values_json TEXT, new_values_json TEXT);
        CREATE TABLE events (symbol TEXT NOT NULL, interval TEXT NOT NULL, event_type TEXT NOT NULL,
          event_timestamp_utc INTEGER NOT NULL, event_key TEXT NOT NULL, event_json TEXT NOT NULL,
          first_seen_run_id TEXT NOT NULL, last_seen_run_id TEXT NOT NULL, source_file TEXT NOT NULL,
          source_sha256 TEXT NOT NULL, PRIMARY KEY(symbol, interval, event_type, event_key));
        CREATE INDEX idx_events_symbol_timestamp ON events(symbol, interval, event_timestamp_utc);
        CREATE TABLE event_revisions (revision_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
          symbol TEXT NOT NULL, interval TEXT NOT NULL, event_type TEXT NOT NULL,
          event_timestamp_utc INTEGER NOT NULL, detected_at_utc TEXT NOT NULL, action TEXT NOT NULL,
          old_event_json TEXT, new_event_json TEXT);
        CREATE TABLE symbol_runs (run_id TEXT NOT NULL, task_key TEXT NOT NULL, task_sequence INTEGER NOT NULL,
          symbol TEXT NOT NULL, interval TEXT NOT NULL, mode TEXT NOT NULL, full_range INTEGER NOT NULL,
          request_start_epoch INTEGER, request_end_epoch INTEGER NOT NULL, classification TEXT NOT NULL,
          http_status INTEGER, bars_returned INTEGER NOT NULL, new_bars INTEGER NOT NULL,
          revised_bars INTEGER NOT NULL, unchanged_bars INTEGER NOT NULL, missing_bars INTEGER NOT NULL,
          events_returned INTEGER NOT NULL, new_events INTEGER NOT NULL, revised_events INTEGER NOT NULL,
          unchanged_events INTEGER NOT NULL, full_refresh_required INTEGER NOT NULL,
          full_refresh_reason TEXT NOT NULL, raw_file TEXT, raw_sha256 TEXT, elapsed_ms INTEGER NOT NULL,
          attempts INTEGER NOT NULL, error_description TEXT, PRIMARY KEY(run_id, task_key));
        """
    )
    run_id = "run-1"
    con.execute("INSERT INTO archive_meta VALUES('database_schema_version','1')")
    con.execute("INSERT INTO runs VALUES(?, 'baseline','1d',30,'2026-08-05T00:00:00Z','2026-08-05T01:00:00Z','COMPLETE','symbols.csv',?,?, 'run-folder','0.1.0')", (run_id, symbols, symbols))
    epoch = 1_600_000_000
    for idx in range(symbols):
        symbol = f"SYM{idx:03d}"
        file_name = f"runs/run-folder/raw/{symbol}.json.gz"
        digest = sha(symbol)
        last = epoch + (bars_each - 1) * 86400
        con.execute("INSERT INTO symbol_state VALUES(?, '1d', ?, '2026-08-05T01:00:00Z', ?, ?, 0, '')", (symbol, last, run_id, run_id))
        rows = []
        for j in range(bars_each):
            ts = epoch + j * 86400
            rows.append((symbol, '1d', ts, rebuild.epoch_to_utc_text(ts), 1.0+idx, 2.0+idx, .5+idx, 1.5+idx, 1.4+idx, 1000+j, run_id, run_id, file_name, digest))
        con.executemany("INSERT INTO bars VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        event_ts = epoch + 10 * 86400
        event_json = json.dumps({"amount": idx + .25, "date": event_ts}, sort_keys=True, separators=(",", ":"))
        con.execute("INSERT INTO events VALUES(?, '1d','DIVIDEND',?,?,?,?,?,?,?)", (symbol, event_ts, f"{event_ts}:{symbol}", event_json, run_id, run_id, file_name, digest))
        con.execute("INSERT INTO symbol_runs VALUES(?,?,?,?, '1d','baseline',1,?,?, 'SUCCESS_HISTORY_RETURNED',200,?,?,0,0,0,1,1,0,0,0,'',?,?,10,1,NULL)", (run_id, f"task-{symbol}", idx+1, symbol, epoch, last+86400, bars_each, bars_each, file_name, digest))
    con.execute("INSERT INTO bar_revisions(run_id,symbol,interval,timestamp_utc,detected_at_utc,action,changed_fields_json,old_values_json,new_values_json) VALUES(?,?,?,?,?,?,?,?,?)", (run_id,'SYM000','1d',epoch,'2026-08-05T01:00:00Z','REVISED','[\"close\"]','{\"close\":1}','{\"close\":2}'))
    con.execute("INSERT INTO event_revisions(run_id,symbol,interval,event_type,event_timestamp_utc,detected_at_utc,action,old_event_json,new_event_json) VALUES(?,?,?,?,?,?,?,?,?)", (run_id,'SYM000','1d','DIVIDEND',epoch,'2026-08-05T01:00:00Z','REVISED','{}','{\"amount\":1}'))
    con.commit(); con.close()


def test_source_is_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "history.sqlite"; create_source(db)
    monkeypatch.setattr(rebuild, "REPOSITORY_ROOT", tmp_path / "repo")
    con = rebuild.connect_read_only(db)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("DELETE FROM bars")
    con.close()


def test_full_rebuild_verifies_all_tables_and_reduces_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = tmp_path / "archive"; archive.mkdir(); db = archive / "history.sqlite"
    create_source(db, symbols=10, bars_each=250)
    repo = tmp_path / "repo"; repo.mkdir(); monkeypatch.setattr(rebuild, "REPOSITORY_ROOT", repo)
    output = tmp_path / "compact-output"
    args = rebuild.build_parser().parse_args(["--database", str(db), "--output-dir", str(output), "--progress-every", "100"])
    out, manifest = rebuild.run_rebuild(args)
    assert out == output
    assert manifest["source_database_unchanged"] is True
    assert manifest["symbols_completed"] == 10
    assert manifest["quick_check"] == "ok"
    assert manifest["foreign_key_check_ok"] is True
    assert all(item["matches_source"] for item in manifest["verification"])
    assert manifest["compact_database_bytes"] < manifest["source_database_bytes"]
    assert manifest["size_reduction_percent"] > 20
    assert (output / "rebuild_report.txt").is_file()


def test_checkpoint_resume_completes_remaining_symbols(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = tmp_path / "archive"; archive.mkdir(); db = archive / "history.sqlite"
    create_source(db, symbols=7, bars_each=30)
    repo = tmp_path / "repo"; repo.mkdir(); monkeypatch.setattr(rebuild, "REPOSITORY_ROOT", repo)
    output = tmp_path / "compact-output"
    pause_args = rebuild.build_parser().parse_args(["--database", str(db), "--output-dir", str(output), "--stop-after-symbols", "3"])
    with pytest.raises(rebuild.RebuildPaused):
        rebuild.run_rebuild(pause_args)
    con = sqlite3.connect(output / rebuild.COMPACT_DATABASE_FILENAME)
    assert con.execute("SELECT COUNT(*) FROM rebuild_progress").fetchone()[0] == 3
    con.close()
    resume_args = rebuild.build_parser().parse_args(["--database", str(db), "--resume-dir", str(output), "--progress-every", "100"])
    _, manifest = rebuild.run_rebuild(resume_args)
    assert manifest["symbols_completed"] == 7
    assert all(item["matches_source"] for item in manifest["verification"])


def test_output_inside_repository_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"; repo.mkdir(); monkeypatch.setattr(rebuild, "REPOSITORY_ROOT", repo)
    with pytest.raises(rebuild.RebuildError, match="outside"):
        rebuild.validate_new_output_directory(repo / "data")


def test_resume_rejects_changed_source_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = tmp_path / "archive"; archive.mkdir(); db = archive / "history.sqlite"
    create_source(db, symbols=3, bars_each=10)
    repo = tmp_path / "repo"; repo.mkdir(); monkeypatch.setattr(rebuild, "REPOSITORY_ROOT", repo)
    output = tmp_path / "compact-output"
    pause_args = rebuild.build_parser().parse_args(["--database", str(db), "--output-dir", str(output), "--stop-after-symbols", "1"])
    with pytest.raises(rebuild.RebuildPaused): rebuild.run_rebuild(pause_args)
    con = sqlite3.connect(db); con.execute("INSERT INTO archive_meta VALUES('changed','yes')"); con.commit(); con.close()
    resume_args = rebuild.build_parser().parse_args(["--database", str(db), "--resume-dir", str(output)])
    with pytest.raises(rebuild.RebuildError, match="identity mismatch"):
        rebuild.run_rebuild(resume_args)
