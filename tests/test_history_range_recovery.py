from __future__ import annotations

import importlib.util
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "capture-utility" / "history_range_recovery.py"
SPEC = importlib.util.spec_from_file_location("history_range_recovery_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
recovery = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = recovery
SPEC.loader.exec_module(recovery)


def chart_body(symbol: str, timestamps: list[int], closes: list[float]) -> bytes:
    payload = {
        "chart": {
            "result": [{
                "meta": {"symbol": symbol, "dataGranularity": "1d"},
                "timestamp": timestamps,
                "indicators": {
                    "quote": [{
                        "open": [value - 0.5 for value in closes],
                        "high": [value + 0.5 for value in closes],
                        "low": [value - 1.0 for value in closes],
                        "close": closes,
                        "volume": [int(value * 100) for value in closes],
                    }],
                    "adjclose": [{"adjclose": closes}],
                },
                "events": {},
            }],
            "error": None,
        }
    }
    return json.dumps(payload, separators=(",", ":")).encode()


def http_result(body: bytes) -> object:
    return recovery.history.HistoryHttpResult(
        body=body,
        http_status=200,
        content_type="application/json",
        final_url_redacted="https://query2.finance.yahoo.com/redacted",
        requested_at_utc="2026-08-06T00:00:00Z",
        response_received_at_utc="2026-08-06T00:00:01Z",
        elapsed_ms=10,
        attempts=[{"attempt": 1}],
        error_message=None,
        session_generation=1,
    )


def create_empty_compact_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    recovery.compact.rebuild.initialize_compact_database(connection)
    connection.execute("UPDATE meta SET value='VERIFIED_COMPLETE' WHERE key='build_status'")
    connection.execute("INSERT INTO archive_meta(key,value) VALUES('database_schema_version','1')")
    connection.commit()
    connection.close()


def capture(symbol: str, index: int, bars: list[object]) -> object:
    return recovery.WindowCapture(
        symbol=symbol,
        window_index=index,
        task_key=f"task-{index}",
        start_epoch=index * 100,
        end_epoch=(index + 1) * 100,
        classification="SUCCESS_HISTORY_RETURNED",
        http_status=200,
        returned_symbol=symbol,
        bars=bars,
        events=[],
        raw_file=f"raw/{index}.json.gz",
        raw_sha256="0" * 64,
        elapsed_ms=1,
        attempts=1,
        error_code=None,
        error_description=None,
    )


def bar(timestamp: int, close: float) -> object:
    return recovery.history.BarRecord(
        timestamp_utc=timestamp,
        datetime_utc=recovery.history.epoch_to_utc_text(timestamp),
        open=close - 0.5,
        high=close + 0.5,
        low=close - 1.0,
        close=close,
        adjclose=close,
        volume=1000,
    )


def test_build_windows_uses_two_sub_100_year_windows() -> None:
    windows = recovery.build_windows(
        start_epoch=recovery.history.BASELINE_START_EPOCH,
        end_epoch=recovery.date_to_epoch(recovery.date(2026, 8, 7)),
        window_years=90,
        overlap_days=31,
    )
    assert len(windows) == 2
    assert windows[0].start_date == "1900-01-01"
    assert windows[0].end_date_exclusive == "1990-01-01"
    assert windows[1].start_date == "1989-12-01"
    assert windows[1].end_date_exclusive == "2026-08-07"


def test_build_requests_plans_24_requests_for_12_symbols() -> None:
    symbols = [f"S{index}" for index in range(12)]
    windows = [
        recovery.RecoveryWindow(1, 0, 100, "1970-01-01", "1970-01-02"),
        recovery.RecoveryWindow(2, 50, 200, "1970-01-01", "1970-01-03"),
    ]
    requests = recovery.build_requests(symbols, windows)
    assert len(requests) == 24
    assert all(item.task.full_range is False for item in requests)
    assert requests[1].task.request_start_epoch == 50


def test_merge_deduplicates_identical_overlap() -> None:
    first = capture("AAA", 1, [bar(100, 10.0), bar(200, 11.0)])
    second = capture("AAA", 2, [bar(200, 11.0), bar(300, 12.0)])
    outcome = recovery.merge_window_captures("AAA", [first, second])
    assert outcome.classification == "SUCCESS_HISTORY_RETURNED"
    assert [item.timestamp_utc for item in outcome.bars] == [100, 200, 300]
    assert outcome.duplicate_bars == 1
    assert outcome.bar_conflicts == 0


def test_merge_rejects_conflicting_overlap() -> None:
    first = capture("AAA", 1, [bar(200, 11.0)])
    second = capture("AAA", 2, [bar(200, 99.0)])
    outcome = recovery.merge_window_captures("AAA", [first, second])
    assert outcome.classification == "RECOVERY_OVERLAP_CONFLICT"
    assert outcome.bar_conflicts == 1
    assert outcome.bars == []


def test_validation_copy_recovers_merged_history_and_preserves_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(recovery.compact, "REPOSITORY_ROOT", repo)
    archive = tmp_path / "archive"
    rebuild_dir = archive / "compact-rebuilds" / "verified"
    rebuild_dir.mkdir(parents=True)
    source = rebuild_dir / recovery.compact.COMPACT_DATABASE_FILENAME
    create_empty_compact_database(source)
    symbols_file = tmp_path / "symbols.csv"
    symbols_file.write_text("Symbol\nAAA\n", encoding="utf-8")
    windows = [
        recovery.RecoveryWindow(1, 100, 250, "1970-01-01", "1970-01-02"),
        recovery.RecoveryWindow(2, 200, 400, "1970-01-01", "1970-01-03"),
    ]

    def fake_request(task: object) -> object:
        if task.task_key.endswith("w01"):
            return http_result(chart_body("AAA", [100, 200], [10.0, 11.0]))
        return http_result(chart_body("AAA", [200, 300], [11.0, 12.0]))

    before = source.read_bytes()
    run_dir, manifest = recovery.run_recovery(
        symbols=["AAA"],
        windows=windows,
        source_database=source,
        archive_root=archive,
        database_resolution="test",
        symbols_file=symbols_file,
        in_place=False,
        overlap_days=31,
        concurrency=1,
        timeout_seconds=1,
        maximum_attempts=1,
        backoff_seconds=(),
        user_agent="test",
        request_override=fake_request,
        now=lambda: datetime(2026, 8, 6, tzinfo=timezone.utc),
        progress=lambda _message: None,
    )
    assert source.read_bytes() == before
    assert manifest["verification_ok"] is True
    assert manifest["recovery_complete"] is True
    assert manifest["totals"]["bars_returned"] == 3
    assert manifest["totals"]["duplicate_bars"] == 1
    validation = run_dir / recovery.compact.VALIDATION_DATABASE_FILENAME
    connection = sqlite3.connect(validation)
    assert connection.execute("SELECT COUNT(*) FROM bars").fetchone()[0] == 3
    assert connection.execute("SELECT COUNT(*) FROM symbol_runs").fetchone()[0] == 1
    connection.close()
    assert (run_dir / "range-recovery-report.txt").is_file()
    assert (run_dir / "window-results.csv").is_file()


def test_in_place_requires_explicit_acknowledgment() -> None:
    args = recovery.build_parser().parse_args(["--in-place", "--dry-run"])
    assert args.in_place is True
    assert args.acknowledge_in_place_update is False


def test_merge_accepts_no_history_window_when_other_window_has_data() -> None:
    empty = capture("AAA", 1, [])
    empty.classification = "NO_CHART_HISTORY_AVAILABLE"
    recent = capture("AAA", 2, [bar(300, 12.0)])
    outcome = recovery.merge_window_captures("AAA", [empty, recent])
    assert outcome.classification == "SUCCESS_HISTORY_RETURNED"
    assert len(outcome.bars) == 1
    assert outcome.successful_windows == 2


def test_review_existing_run_reports_exact_single_bar_date(tmp_path: Path) -> None:
    import gzip

    run_dir = tmp_path / "run"
    merged = run_dir / "merged"
    merged.mkdir(parents=True)
    payload = {
        "symbol": "AAA",
        "bars": [{
            "timestamp_utc": 1785974400,
            "datetime_utc": "2026-08-06T00:00:00Z",
            "open": 1.0,
            "high": 1.1,
            "low": 0.9,
            "close": 1.0,
            "adjclose": 1.0,
            "volume": 0,
        }],
    }
    with gzip.open(merged / "AAA.merged.json.gz", "wt", encoding="utf-8") as handle:
        json.dump(payload, handle)
    report_path, csv_path = recovery.review_existing_run(run_dir)
    report = report_path.read_text(encoding="utf-8")
    detail = csv_path.read_text(encoding="utf-8")
    assert "Database opened or changed: False" in report
    assert "SINGLE_BAR_ONLY: 1" in report
    assert "2026-08-06T00:00:00Z" in report
    assert "AAA,1785974400,2026-08-06T00:00:00Z" in detail
