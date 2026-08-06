from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "capture-utility" / "history_compact_incremental.py"
SPEC = importlib.util.spec_from_file_location("history_compact_incremental_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
compact = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = compact
SPEC.loader.exec_module(compact)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def make_chart_body(symbol: str, timestamps: list[int], closes: list[float], *, dividend: bool = True) -> bytes:
    events = {}
    if dividend:
        ts = timestamps[-1]
        events = {"dividends": {str(ts): {"amount": 0.25, "date": ts}}}
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
                        "volume": [1000 + i for i in range(len(closes))],
                    }],
                    "adjclose": [{"adjclose": closes}],
                },
                "events": events,
            }],
            "error": None,
        }
    }
    return json.dumps(payload, separators=(",", ":")).encode()


def create_compact_database(path: Path, symbols: tuple[str, ...] = ("AAA", "BBB")) -> None:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    compact.rebuild.initialize_compact_database(con)
    con.execute("UPDATE meta SET value='VERIFIED_COMPLETE' WHERE key='build_status'")
    con.execute("INSERT INTO archive_meta VALUES('database_schema_version','1')")
    con.execute("INSERT INTO intervals(interval) VALUES('1d')")
    interval_id = con.execute("SELECT interval_id FROM intervals WHERE interval='1d'").fetchone()[0]
    con.execute("INSERT INTO run_ids(run_id) VALUES('baseline-run')")
    run_key = con.execute("SELECT run_key FROM run_ids WHERE run_id='baseline-run'").fetchone()[0]
    con.execute(
        "INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (run_key, "baseline", interval_id, 30, "2026-08-05T00:00:00Z", "2026-08-05T01:00:00Z", "COMPLETED", "symbols.csv", len(symbols), len(symbols), "baseline-run", "0.1.0"),
    )
    epoch = 1_700_000_000
    for index, symbol in enumerate(symbols):
        con.execute("INSERT INTO symbols(symbol) VALUES(?)", (symbol,))
        symbol_id = con.execute("SELECT symbol_id FROM symbols WHERE symbol=?", (symbol,)).fetchone()[0]
        source_file = f"runs/baseline/raw/{symbol}.json.gz"
        source_hash = digest(symbol)
        con.execute("INSERT INTO sources(source_file,source_sha256) VALUES(?,?)", (source_file, bytes.fromhex(source_hash)))
        source_id = con.execute("SELECT source_id FROM sources WHERE source_file=?", (source_file,)).fetchone()[0]
        timestamps = [epoch, epoch + 86400]
        for offset, ts in enumerate(timestamps):
            value = 10.0 + index + offset
            con.execute(
                "INSERT INTO bars VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (symbol_id, interval_id, ts, value - .5, value + .5, value - 1, value, value, 1000 + offset, run_key, run_key, source_id),
            )
        con.execute(
            "INSERT INTO symbol_state VALUES(?,?,?,?,?,?,?,?)",
            (symbol_id, interval_id, timestamps[-1], "2026-08-05T01:00:00Z", run_key, run_key, 0, ""),
        )
    con.commit()
    con.close()


def test_schema_validation_requires_verified_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "history_compact.sqlite"
    create_compact_database(db)
    monkeypatch.setattr(compact, "REPOSITORY_ROOT", tmp_path / "repo")
    con = compact.connect_read_only(db)
    assert compact.read_compact_meta(con)["build_status"] == "VERIFIED_COMPLETE"
    con.close()
    writable = sqlite3.connect(db)
    writable.execute("UPDATE meta SET value='BUILDING' WHERE key='build_status'")
    writable.commit(); writable.close()
    with pytest.raises(compact.CompactUpdateError, match="not verified"):
        compact.connect_read_only(db)


def test_build_tasks_uses_compact_state_and_overlap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "history_compact.sqlite"
    create_compact_database(db)
    monkeypatch.setattr(compact, "REPOSITORY_ROOT", tmp_path / "repo")
    tasks = compact.build_tasks(
        ["AAA", "NEW"], mode="sync", interval="1d", overlap_days=30,
        request_end_epoch=1_800_000_000, database_path=db,
    )
    assert tasks[0].full_range is False
    assert tasks[0].request_start_epoch == 1_700_086_400 - 30 * 86400
    assert tasks[1].full_range is True
    assert tasks[1].mode == "baseline-fallback"


def test_apply_compact_records_new_revised_unchanged_and_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "history_compact.sqlite"
    create_compact_database(db, ("AAA",))
    monkeypatch.setattr(compact, "REPOSITORY_ROOT", tmp_path / "repo")
    con = compact.connect_writable(db)
    lookups = compact.load_lookups(con)
    old_ts = 1_700_000_000
    second_ts = old_ts + 86400
    new_ts = second_ts + 86400
    parsed = compact.history.ParsedHistory(
        "SUCCESS_HISTORY_RETURNED", "AAA",
        [
            compact.history.BarRecord(old_ts, compact.history.epoch_to_utc_text(old_ts), 9.5, 10.5, 9.0, 10.0, 10.0, 1000),
            compact.history.BarRecord(second_ts, compact.history.epoch_to_utc_text(second_ts), 10.5, 12.0, 10.0, 11.5, 11.5, 1001),
            compact.history.BarRecord(new_ts, compact.history.epoch_to_utc_text(new_ts), 11.5, 12.5, 11.0, 12.0, 12.0, 1002),
        ],
        [compact.history.EventRecord("DIVIDEND", new_ts, "event-key", '{"amount":0.25,"date":1700172800}')],
        {"symbol": "AAA"},
    )
    task = compact.history.HistoryTask("task", 1, "AAA", "1d", "sync", False, old_ts, new_ts + 86400, second_ts)
    with con:
        stats = compact.apply_parsed_history_compact(
            con, lookups, run_id="sync-run", task=task, parsed=parsed,
            source_file="incremental/raw/AAA.json.gz", source_sha256=digest("new"),
            detected_at_utc="2026-08-06T00:00:00Z",
        )
    assert stats.new_bars == 1
    assert stats.revised_bars == 1
    assert stats.unchanged_bars == 1
    assert stats.new_events == 1
    assert con.execute("SELECT COUNT(*) FROM bar_revisions").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    con.close()


def test_http_422_error_body_is_classified() -> None:
    task = compact.history.HistoryTask("task", 1, "BAD", "1d", "sync", False, 1, 2, 1)
    body = json.dumps({"chart": {"result": None, "error": {"code": "Not Found", "description": "No data found, symbol may be delisted"}}}).encode()
    http = compact.history.HistoryHttpResult(body, 422, "application/json", "redacted", "a", "b", 1, [{}], None, 1)
    parsed = compact.classify_http_result(http, task)
    assert parsed.classification == "NO_CHART_HISTORY_AVAILABLE"
    assert parsed.error_description


def test_validation_copy_run_preserves_source_and_verifies_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = tmp_path / "archive"
    rebuild_dir = archive / "compact-rebuilds" / "verified"
    rebuild_dir.mkdir(parents=True)
    source = rebuild_dir / compact.COMPACT_DATABASE_FILENAME
    create_compact_database(source, ("AAA",))
    repo = tmp_path / "repo"; repo.mkdir()
    monkeypatch.setattr(compact, "REPOSITORY_ROOT", repo)
    epoch = 1_700_000_000
    task = compact.build_tasks(
        ["AAA"], mode="sync", interval="1d", overlap_days=30,
        request_end_epoch=epoch + 3 * 86400, database_path=source,
    )[0]
    body = make_chart_body("AAA", [epoch, epoch + 86400, epoch + 2 * 86400], [10.0, 11.5, 12.0])

    def fake_request(_task):
        return compact.history.HistoryHttpResult(
            body, 200, "application/json", "redacted", "2026-08-06T00:00:00Z",
            "2026-08-06T00:00:01Z", 10, [{"attempt": 1}], None, 1,
        )

    before = source.read_bytes()
    run_dir, manifest = compact.run_compact_update(
        [task], input_file=tmp_path / "symbols.csv", source_database=source,
        archive_root=archive, database_resolution="test", mode="sync", interval="1d",
        overlap_days=30, concurrency=1, timeout_seconds=1, maximum_attempts=1,
        backoff_seconds=(), user_agent="test", in_place=False, request_override=fake_request,
        now=lambda: datetime(2026, 8, 6, tzinfo=timezone.utc), progress=lambda _: None,
    )
    assert source.read_bytes() == before
    assert manifest["source_compact_database_unchanged"] is True
    assert manifest["verification_ok"] is True
    assert manifest["returned_value_verification"]["bar_mismatches"] == 0
    assert (run_dir / compact.VALIDATION_DATABASE_FILENAME).is_file()
    assert manifest["totals"]["new_bars"] == 1


def test_in_place_requires_explicit_acknowledgment() -> None:
    args = compact.build_parser().parse_args(["--in-place", "--dry-run"])
    assert args.in_place is True
    assert args.acknowledge_in_place_update is False


def test_output_inside_repository_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    db = repo / compact.COMPACT_DATABASE_FILENAME
    create_compact_database(db)
    monkeypatch.setattr(compact, "REPOSITORY_ROOT", repo)
    with pytest.raises(compact.CompactUpdateError, match="outside"):
        compact.connect_read_only(db)


def test_http_422_data_does_not_exist_is_no_history() -> None:
    task = compact.history.HistoryTask("task", 1, "BAD", "1d", "sync", False, 1, 2, 1)
    body = json.dumps({
        "chart": {
            "result": None,
            "error": {
                "code": "Unprocessable Entity",
                "description": "Data doesn't exist for startDate = 1, endDate = 2",
            },
        }
    }).encode()
    http = compact.history.HistoryHttpResult(
        body, 422, "application/json", "redacted", "a", "b", 1, [{}], None, 1
    )
    parsed = compact.classify_http_result(http, task)
    assert parsed.classification == "NO_CHART_HISTORY_AVAILABLE"
    assert parsed.error_code == "Unprocessable Entity"


def test_periodic_progress_and_exception_visibility() -> None:
    from types import SimpleNamespace

    success = SimpleNamespace(classification="SUCCESS_HISTORY_RETURNED")
    error = SimpleNamespace(classification="YAHOO_ERROR_OBJECT")
    assert compact.should_emit_symbol_progress(1, 100, success, progress_every=25, verbose=False)
    assert compact.should_emit_symbol_progress(25, 100, success, progress_every=25, verbose=False)
    assert not compact.should_emit_symbol_progress(26, 100, success, progress_every=25, verbose=False)
    assert compact.should_emit_symbol_progress(26, 100, error, progress_every=25, verbose=False)
    assert compact.should_emit_symbol_progress(26, 100, success, progress_every=25, verbose=True)


def test_in_place_report_has_mode_specific_safety_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = tmp_path / "archive"
    rebuild_dir = archive / "compact-rebuilds" / "verified"
    rebuild_dir.mkdir(parents=True)
    source = rebuild_dir / compact.COMPACT_DATABASE_FILENAME
    create_compact_database(source, ("AAA",))
    repo = tmp_path / "repo"; repo.mkdir()
    monkeypatch.setattr(compact, "REPOSITORY_ROOT", repo)
    epoch = 1_700_000_000
    task = compact.build_tasks(
        ["AAA"], mode="sync", interval="1d", overlap_days=30,
        request_end_epoch=epoch + 3 * 86400, database_path=source,
    )[0]
    body = make_chart_body("AAA", [epoch, epoch + 86400], [10.0, 11.0], dividend=False)

    def fake_request(_task):
        return compact.history.HistoryHttpResult(
            body, 200, "application/json", "redacted", "2026-08-06T00:00:00Z",
            "2026-08-06T00:00:01Z", 10, [{"attempt": 1}], None, 1,
        )

    run_dir, manifest = compact.run_compact_update(
        [task], input_file=tmp_path / "symbols.csv", source_database=source,
        archive_root=archive, database_resolution="test", mode="sync", interval="1d",
        overlap_days=30, concurrency=1, timeout_seconds=1, maximum_attempts=1,
        backoff_seconds=(), user_agent="test", in_place=True, request_override=fake_request,
        now=lambda: datetime(2026, 8, 6, tzinfo=timezone.utc), progress=lambda _: None,
    )
    report = (run_dir / "compact-update-report.txt").read_text(encoding="utf-8")
    assert manifest["verification_ok"] is True
    assert "Source compact database unchanged: not applicable (updated in place)" in report
    assert "In-place mode updated the verified history_compact.sqlite database" in report
    assert "Validation mode updated only" not in report


def test_error_review_csv_preserves_sanitized_yahoo_error_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "archive"
    rebuild_dir = archive / "compact-rebuilds" / "verified"
    rebuild_dir.mkdir(parents=True)
    source = rebuild_dir / compact.COMPACT_DATABASE_FILENAME
    create_compact_database(source, ("BAD",))
    repo = tmp_path / "repo"; repo.mkdir()
    monkeypatch.setattr(compact, "REPOSITORY_ROOT", repo)
    task = compact.build_tasks(
        ["BAD"], mode="sync", interval="1d", overlap_days=30,
        request_end_epoch=1_800_000_000, database_path=source,
    )[0]
    description = "Data doesn't exist for startDate = 1, endDate = 2"
    body = json.dumps({
        "chart": {"result": None, "error": {"code": "Unprocessable Entity", "description": description}}
    }).encode()

    def fake_request(_task):
        return compact.history.HistoryHttpResult(
            body, 422, "application/json", "redacted", "2026-08-06T00:00:00Z",
            "2026-08-06T00:00:01Z", 10, [{"attempt": 1}], None, 1,
        )

    run_dir, manifest = compact.run_compact_update(
        [task], input_file=tmp_path / "symbols.csv", source_database=source,
        archive_root=archive, database_resolution="test", mode="sync", interval="1d",
        overlap_days=30, concurrency=1, timeout_seconds=1, maximum_attempts=1,
        backoff_seconds=(), user_agent="test", in_place=False, request_override=fake_request,
        now=lambda: datetime(2026, 8, 6, tzinfo=timezone.utc), progress=lambda _: None,
    )
    rows = list(csv.DictReader((run_dir / "error-classification-review.csv").open(encoding="utf-8")))
    assert manifest["verification_ok"] is True
    assert manifest["classifications"] == {"NO_CHART_HISTORY_AVAILABLE": 1}
    assert rows[0]["symbol"] == "BAD"
    assert rows[0]["error_code"] == "Unprocessable Entity"
    assert rows[0]["error_description"] == description


def test_review_existing_run_reclassifies_without_database_or_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / "existing-run"
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True)
    repo = tmp_path / "repo"; repo.mkdir()
    monkeypatch.setattr(compact, "REPOSITORY_ROOT", repo)
    description = "Data doesn't exist for startDate = 1, endDate = 2"
    body = json.dumps({
        "chart": {"result": None, "error": {"code": "Unprocessable Entity", "description": description}}
    }).encode()
    raw_rel = "raw/BAD.json.gz"
    import gzip
    (run_dir / raw_rel).write_bytes(gzip.compress(body))
    fields = [
        "task_key", "task_sequence", "symbol", "interval", "mode", "full_range",
        "request_start_epoch", "request_end_epoch", "classification", "http_status",
        "raw_file", "attempts", "elapsed_ms", "error_description",
    ]
    with (run_dir / "symbol-results.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow({
            "task_key": "task", "task_sequence": 1, "symbol": "BAD", "interval": "1d",
            "mode": "sync", "full_range": "False", "request_start_epoch": 1,
            "request_end_epoch": 2, "classification": "YAHOO_ERROR_OBJECT",
            "http_status": 422, "raw_file": raw_rel, "attempts": 1,
            "elapsed_ms": 10, "error_description": description,
        })
    summary = compact.review_existing_run(run_dir)
    assert summary["responses_reviewed"] == 1
    assert summary["classifications"] == {"NO_CHART_HISTORY_AVAILABLE": 1}
    review_rows = list(csv.DictReader((run_dir / "error-classification-review.csv").open(encoding="utf-8")))
    assert review_rows[0]["original_classification"] == "YAHOO_ERROR_OBJECT"
    assert review_rows[0]["classification"] == "NO_CHART_HISTORY_AVAILABLE"
    report = (run_dir / "error-classification-review.txt").read_text(encoding="utf-8")
    assert "Network access: False" in report
    assert "Database opened or changed: False" in report


def write_history_exclusions(path: Path) -> None:
    path.write_text(
        "symbol,policy,keep_fast_mode,category,browser_evidence,api_evidence,evidence_date,reason,notes\n"
        "AAA,EXCLUDE_LONG_HISTORY_REQUESTS,true,test,BROWSER_NO_DOWNLOADABLE_HISTORY,REQUEST_RANGE_NOT_SUPPORTED|CURRENT_SESSION_BAR_ONLY,2026-08-05,No downloadable history,Keep Fast mode\n",
        encoding="utf-8",
    )


def test_compact_dry_run_skips_browser_confirmed_symbol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = tmp_path / "archive"
    rebuild_dir = archive / "compact-rebuilds" / "verified"
    rebuild_dir.mkdir(parents=True)
    source = rebuild_dir / compact.COMPACT_DATABASE_FILENAME
    create_compact_database(source, ("AAA", "BBB"))
    repo = tmp_path / "repo"; repo.mkdir()
    monkeypatch.setattr(compact, "REPOSITORY_ROOT", repo)
    input_path = tmp_path / "symbols.csv"
    input_path.write_text("symbol\nAAA\nBBB\n", encoding="utf-8")
    exclusions_path = tmp_path / "exclusions.csv"
    write_history_exclusions(exclusions_path)
    status = compact.main([
        "--dry-run", "--database", str(source), "--input", str(input_path),
        "--history-exclusions", str(exclusions_path),
    ])
    assert status == 0
    output = capsys.readouterr().out
    assert '"planned_tasks": 1' in output
    assert '"requests_skipped": 1' in output
    assert '"symbol": "BBB"' in output
