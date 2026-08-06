from __future__ import annotations

import gzip
import importlib.util
import json
import sqlite3
import sys
import threading
from datetime import datetime, timedelta, timezone
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "capture-utility" / "yahoo_history_capture.py"
SPEC = importlib.util.spec_from_file_location("yahoo_history_capture", MODULE_PATH)
assert SPEC and SPEC.loader
history = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = history
SPEC.loader.exec_module(history)


class FakeResponse:
    def __init__(self, body: bytes, *, status: int = 200, url: str = "https://example.test"):
        self._body = body
        self.status = status
        self.url = url
        self.headers = Message()
        self.headers["Content-Type"] = "application/json"

    def read(self) -> bytes:
        return self._body

    def getcode(self) -> int:
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class SafeNow:
    def __init__(self):
        self._lock = threading.Lock()
        self.value = datetime(2026, 8, 5, 1, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        with self._lock:
            value = self.value
            self.value += timedelta(milliseconds=10)
            return value


class SafeClock:
    def __init__(self):
        self._lock = threading.Lock()
        self.value = 0.0

    def __call__(self):
        with self._lock:
            value = self.value
            self.value += 0.01
            return value


def chart_body(
    *,
    symbol: str = "AAPL",
    timestamps: list[int] | None = None,
    closes: list[float | None] | None = None,
    adjcloses: list[float | None] | None = None,
    dividends: dict[str, dict] | None = None,
    splits: dict[str, dict] | None = None,
) -> bytes:
    timestamps = timestamps or [100, 200]
    closes = closes or [10.0, 11.0]
    adjcloses = adjcloses or closes
    result = {
        "meta": {"symbol": symbol, "currency": "USD", "dataGranularity": "1d"},
        "timestamp": timestamps,
        "indicators": {
            "quote": [{
                "open": [value - 0.5 if value is not None else None for value in closes],
                "high": [value + 0.5 if value is not None else None for value in closes],
                "low": [value - 1.0 if value is not None else None for value in closes],
                "close": closes,
                "volume": [1000 + index for index in range(len(timestamps))],
            }],
            "adjclose": [{"adjclose": adjcloses}],
        },
    }
    events = {}
    if dividends:
        events["dividends"] = dividends
    if splits:
        events["splits"] = splits
    if events:
        result["events"] = events
    payload = {"chart": {"result": [result], "error": None}}
    return json.dumps(payload, separators=(",", ":")).encode()


def task(
    symbol: str = "AAPL",
    *,
    mode: str = "baseline",
    full_range: bool = True,
    start: int | None = None,
    end: int = 1000,
):
    return history.HistoryTask(
        task_key="history-000001-AAPL",
        task_sequence=1,
        symbol=symbol,
        interval="1d",
        mode=mode,
        full_range=full_range,
        request_start_epoch=start,
        request_end_epoch=end,
        prior_latest_epoch=None,
    )


def test_external_storage_rejects_repository_path(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(history, "REPOSITORY_ROOT", tmp_path / "repo")
    inside = tmp_path / "repo" / "captures"
    with pytest.raises(history.HistoryInputError, match="outside"):
        history.validate_external_root(inside)
    assert history.validate_external_root(tmp_path / "archive") == (tmp_path / "archive").resolve()


def test_local_config_resolution_order(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(history, "REPOSITORY_ROOT", tmp_path / "repo")
    config = tmp_path / "repo" / "config" / "local" / "history_capture_local.json"
    configured = tmp_path / "configured"
    history.write_local_output_config(config, configured)
    resolved, source = history.resolve_output_root(None, config_path=config, environment={})
    assert resolved == configured.resolve()
    assert source == "local_config"
    env_root = tmp_path / "environment"
    resolved, source = history.resolve_output_root(
        None,
        config_path=config,
        environment={history.OUTPUT_ROOT_ENVIRONMENT_VARIABLE: str(env_root)},
    )
    assert resolved == env_root.resolve()
    assert source == "environment"


def test_unique_symbols_deduplicates_preserving_first_order(tmp_path: Path):
    path = tmp_path / "symbols.csv"
    path.write_text("symbol\nAAPL\nMSFT\nAAPL\n", encoding="utf-8")
    assert history.unique_symbols_from_input(path) == ["AAPL", "MSFT"]


def test_baseline_url_uses_explicit_bounds_and_redacts_crumb():
    snapshot = history.fast.StaticSession(crumb="secret-value").snapshot()
    url = history.build_chart_url(task(), snapshot)
    assert f"period1={history.BASELINE_START_EPOCH}" in url
    assert "period2=1000" in url
    assert "range=max" not in url
    assert "secret-value" in url
    redacted = history.redact_url(url)
    assert "secret-value" not in redacted
    assert "crumb=REDACTED" in redacted


def test_pre_1970_epoch_conversion_is_windows_safe():
    assert history.epoch_to_utc_text(-2208988800) == "1900-01-01T00:00:00Z"


def test_parse_rejects_silent_interval_downgrade():
    payload = json.loads(chart_body().decode("utf-8"))
    payload["chart"]["result"][0]["meta"]["dataGranularity"] = "1mo"
    parsed = history.parse_chart_response(
        json.dumps(payload).encode("utf-8"),
        requested_symbol="AAPL",
        requested_interval="1d",
    )
    assert parsed.classification == "UNEXPECTED_DATA_GRANULARITY"
    assert parsed.bars == []
    assert "1mo" in (parsed.error_description or "")


def test_incremental_url_uses_period_bounds():
    snapshot = history.fast.StaticSession().snapshot()
    incremental = task(mode="sync", full_range=False, start=500, end=1000)
    url = history.build_chart_url(incremental, snapshot)
    assert "period1=500" in url
    assert "period2=1000" in url
    assert "range=max" not in url


def test_parse_chart_response_extracts_bars_and_events():
    body = chart_body(
        dividends={"150": {"date": 150, "amount": 0.25}},
        splits={"175": {"date": 175, "numerator": 2, "denominator": 1}},
    )
    parsed = history.parse_chart_response(body, requested_symbol="AAPL")
    assert parsed.classification == "SUCCESS_HISTORY_RETURNED"
    assert parsed.returned_symbol == "AAPL"
    assert len(parsed.bars) == 2
    assert parsed.bars[1].close == 11.0
    assert {event.event_type for event in parsed.events} == {"DIVIDEND", "SPLIT"}


def test_parse_not_found_is_terminal_symbol_state():
    body = b'{"chart":{"result":null,"error":{"code":"Not Found","description":"No data found, symbol may be delisted"}}}'
    parsed = history.parse_chart_response(body, requested_symbol="BAD")
    assert parsed.classification == "SYMBOL_NOT_AVAILABLE"


def test_request_retries_429_and_redacts_url():
    calls = []
    headers = Message()
    headers["Content-Type"] = "application/json"
    success = chart_body()

    def opener(request, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise HTTPError(request.full_url, 429, "Too Many Requests", headers, None)
        return FakeResponse(success, url=request.full_url)

    sleeps = []
    result = history.request_history_with_retry(
        task(),
        session=history.fast.StaticSession(crumb="secret"),
        timeout_seconds=5,
        maximum_attempts=2,
        backoff_seconds=(0.0,),
        user_agent="test-agent",
        gate=history.fast.SharedBackoffGate(clock=SafeClock(), sleep=sleeps.append),
        opener=opener,
        sleep=sleeps.append,
        clock=SafeClock(),
        now=SafeNow(),
    )
    assert len(calls) == 2
    assert result.http_status == 200
    assert "secret" not in result.final_url_redacted
    assert "crumb=REDACTED" in result.final_url_redacted


def test_database_baseline_inserts_bars_and_event(tmp_path: Path):
    db = tmp_path / "history.sqlite"
    connection = history.connect_database(db)
    history.initialize_database(connection)
    parsed = history.parse_chart_response(
        chart_body(dividends={"150": {"date": 150, "amount": 0.25}}),
        requested_symbol="AAPL",
    )
    with connection:
        stats = history.apply_parsed_history(
            connection,
            run_id="run1",
            task=task(),
            parsed=parsed,
            source_file="raw.json.gz",
            source_sha256="a" * 64,
            detected_at_utc="2026-08-05T00:00:00Z",
        )
    assert stats.new_bars == 2
    assert stats.new_events == 1
    assert connection.execute("SELECT COUNT(*) FROM bars").fetchone()[0] == 2
    assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    connection.close()


def test_incremental_update_records_new_revised_and_unchanged_bars(tmp_path: Path):
    connection = history.connect_database(tmp_path / "history.sqlite")
    history.initialize_database(connection)
    baseline = history.parse_chart_response(chart_body(), requested_symbol="AAPL")
    with connection:
        history.apply_parsed_history(
            connection,
            run_id="run1",
            task=task(),
            parsed=baseline,
            source_file="one.gz",
            source_sha256="a" * 64,
            detected_at_utc="2026-08-05T00:00:00Z",
        )
    changed = history.parse_chart_response(
        chart_body(timestamps=[100, 200, 300], closes=[10.0, 12.0, 13.0]),
        requested_symbol="AAPL",
    )
    with connection:
        stats = history.apply_parsed_history(
            connection,
            run_id="run2",
            task=task(mode="sync", full_range=False, start=50),
            parsed=changed,
            source_file="two.gz",
            source_sha256="b" * 64,
            detected_at_utc="2026-08-06T00:00:00Z",
        )
    assert stats.new_bars == 1
    assert stats.revised_bars == 1
    assert stats.unchanged_bars == 1
    revision = connection.execute("SELECT * FROM bar_revisions WHERE run_id='run2'").fetchone()
    assert json.loads(revision["changed_fields_json"]) == ["open", "high", "low", "close", "adjclose"]
    connection.close()


def test_new_corporate_action_flags_symbol_for_full_refresh(tmp_path: Path):
    connection = history.connect_database(tmp_path / "history.sqlite")
    history.initialize_database(connection)
    baseline = history.parse_chart_response(chart_body(), requested_symbol="AAPL")
    with connection:
        history.apply_parsed_history(
            connection,
            run_id="run1",
            task=task(),
            parsed=baseline,
            source_file="one.gz",
            source_sha256="a" * 64,
            detected_at_utc="2026-08-05T00:00:00Z",
        )
    updated = history.parse_chart_response(
        chart_body(dividends={"150": {"date": 150, "amount": 0.25}}),
        requested_symbol="AAPL",
    )
    with connection:
        stats = history.apply_parsed_history(
            connection,
            run_id="run2",
            task=task(mode="sync", full_range=False, start=50),
            parsed=updated,
            source_file="two.gz",
            source_sha256="b" * 64,
            detected_at_utc="2026-08-06T00:00:00Z",
        )
    assert stats.full_refresh_required is True
    assert "CORPORATE_ACTION_CHANGE" in stats.full_refresh_reason
    connection.close()


def test_missing_bar_is_logged_but_not_deleted(tmp_path: Path):
    connection = history.connect_database(tmp_path / "history.sqlite")
    history.initialize_database(connection)
    baseline = history.parse_chart_response(chart_body(), requested_symbol="AAPL")
    with connection:
        history.apply_parsed_history(
            connection,
            run_id="run1",
            task=task(),
            parsed=baseline,
            source_file="one.gz",
            source_sha256="a" * 64,
            detected_at_utc="2026-08-05T00:00:00Z",
        )
    refresh = history.parse_chart_response(
        chart_body(timestamps=[100], closes=[10.0]), requested_symbol="AAPL"
    )
    with connection:
        stats = history.apply_parsed_history(
            connection,
            run_id="run2",
            task=task(mode="refresh-flagged", full_range=True),
            parsed=refresh,
            source_file="two.gz",
            source_sha256="b" * 64,
            detected_at_utc="2026-08-06T00:00:00Z",
        )
    assert stats.missing_bars == 1
    assert connection.execute("SELECT COUNT(*) FROM bars").fetchone()[0] == 2
    action = connection.execute("SELECT action FROM bar_revisions WHERE run_id='run2'").fetchone()[0]
    assert action == "MISSING_FROM_REFRESH"
    connection.close()


def test_build_sync_tasks_uses_overlap_and_baseline_fallback(tmp_path: Path):
    db = tmp_path / "history.sqlite"
    connection = history.connect_database(db)
    history.initialize_database(connection)
    connection.execute(
        "INSERT INTO symbol_state(symbol, interval, last_bar_timestamp) VALUES('AAPL','1d',1000000)"
    )
    connection.commit()
    connection.close()
    tasks = history.build_tasks(
        ["AAPL", "MSFT"],
        mode="sync",
        interval="1d",
        overlap_days=30,
        request_end_epoch=2000000,
        database_path=db,
    )
    assert tasks[0].full_range is False
    assert tasks[0].request_start_epoch == max(0, 1000000 - 30 * 86400)
    assert tasks[1].full_range is True
    assert tasks[1].mode == "baseline-fallback"


def test_refresh_flagged_selects_only_flagged_symbols(tmp_path: Path):
    db = tmp_path / "history.sqlite"
    connection = history.connect_database(db)
    history.initialize_database(connection)
    connection.execute(
        "INSERT INTO symbol_state(symbol, interval, full_refresh_required) VALUES('AAPL','1d',1)"
    )
    connection.execute(
        "INSERT INTO symbol_state(symbol, interval, full_refresh_required) VALUES('MSFT','1d',0)"
    )
    connection.commit()
    connection.close()
    tasks = history.build_tasks(
        ["AAPL", "MSFT", "PDI"],
        mode="refresh-flagged",
        interval="1d",
        overlap_days=30,
        request_end_epoch=2000000,
        database_path=db,
    )
    assert [item.symbol for item in tasks] == ["AAPL"]
    assert tasks[0].full_range is True


def test_raw_response_is_gzip_compressed_and_exact(tmp_path: Path):
    output_root = tmp_path / "archive"
    run_state = history.create_run_state(output_root, started_at=datetime(2026, 8, 5, tzinfo=timezone.utc))
    body = chart_body()
    http = history.HistoryHttpResult(
        body=body,
        http_status=200,
        content_type="application/json",
        final_url_redacted="https://example.test?crumb=REDACTED",
        requested_at_utc="2026-08-05T00:00:00Z",
        response_received_at_utc="2026-08-05T00:00:01Z",
        elapsed_ms=1000,
        attempts=[],
        error_message=None,
        session_generation=1,
    )
    raw_file, raw_sha, compressed_sha, raw_size, compressed_size, metadata_file = history.write_raw_and_metadata(
        run_state, task(), http
    )
    assert raw_file and compressed_sha and metadata_file
    compressed = (run_state.run_dir / raw_file).read_bytes()
    assert gzip.decompress(compressed) == body
    assert raw_sha == history.hashlib.sha256(body).hexdigest()
    assert raw_size == len(body)
    assert compressed_size == len(compressed)


def test_checkpoint_round_trip(tmp_path: Path):
    run_state = history.create_run_state(tmp_path / "archive")
    result = history.SymbolResult(
        task_key="history-1", task_sequence=1, symbol="AAPL", interval="1d", mode="baseline",
        full_range=True, request_start_epoch=None, request_end_epoch=1,
        classification="SUCCESS_HISTORY_RETURNED", http_status=200, returned_symbol="AAPL",
        bars_returned=1, new_bars=1, revised_bars=0, unchanged_bars=0, missing_bars=0,
        events_returned=0, new_events=0, revised_events=0, unchanged_events=0,
        full_refresh_required=False, full_refresh_reason="", raw_file="x", raw_uncompressed_sha256="a",
        raw_compressed_sha256="b", raw_uncompressed_bytes=1, raw_compressed_bytes=1,
        metadata_file="m", elapsed_ms=1, attempts=1,
    )
    history.append_checkpoint(run_state, result)
    assert history.load_completed_task_keys(run_state.checkpoint_path) == {"history-1"}


def test_manifest_does_not_persist_absolute_output_path(tmp_path: Path):
    output_root = tmp_path / "private" / "archive"
    run_state = history.create_run_state(output_root)
    connection = history.connect_database(run_state.database_path)
    history.initialize_database(connection)
    manifest = history.write_manifest_and_summary(
        connection,
        run_id=run_state.run_dir.name,
        run_state=run_state,
        results=[],
        input_file=Path("symbols.csv"),
        mode="baseline",
        interval="1d",
        overlap_days=30,
        started_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
        completed_at=datetime(2026, 8, 5, 0, 0, 1, tzinfo=timezone.utc),
        elapsed_seconds=1.0,
        output_root_source="test",
        session_summary=history.fast.StaticSession().public_summary(),
        resumed=False,
    )
    text = json.dumps(manifest)
    assert str(output_root) not in text
    assert manifest["storage"]["absolute_local_path_persisted"] is False
    connection.close()


def test_through_date_is_inclusive_calendar_date():
    epoch = history.parse_through_date("2026-08-05")
    assert datetime.fromtimestamp(epoch, tz=timezone.utc) == datetime(2026, 8, 6, tzinfo=timezone.utc)


def test_parse_backoff_rejects_negative_value():
    with pytest.raises(history.HistoryInputError, match="negative"):
        history.parse_backoff("1,-2")


def test_run_plan_resume_requires_same_task_plan(tmp_path: Path):
    run_state = history.create_run_state(tmp_path / "archive")
    tasks = [task()]
    history.write_or_validate_run_plan(
        run_state,
        tasks=tasks,
        input_file=Path("symbols.csv"),
        mode="baseline",
        interval="1d",
        overlap_days=30,
        started_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
        resumed=False,
    )
    loaded = history.write_or_validate_run_plan(
        run_state,
        tasks=tasks,
        input_file=Path("symbols.csv"),
        mode="baseline",
        interval="1d",
        overlap_days=30,
        started_at=datetime(2026, 8, 6, tzinfo=timezone.utc),
        resumed=True,
    )
    assert loaded["task_plan_sha256"] == history.task_plan_sha256(tasks)
    with pytest.raises(history.HistoryInputError, match="overlap_days"):
        history.write_or_validate_run_plan(
            run_state,
            tasks=tasks,
            input_file=Path("symbols.csv"),
            mode="baseline",
            interval="1d",
            overlap_days=31,
            started_at=datetime(2026, 8, 6, tzinfo=timezone.utc),
            resumed=True,
        )


def write_exclusions(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "symbol,policy,keep_fast_mode,category,browser_evidence,api_evidence,evidence_date,reason,notes\n"
        "AAA,EXCLUDE_LONG_HISTORY_REQUESTS,true,test,BROWSER_NO_DOWNLOADABLE_HISTORY,REQUEST_RANGE_NOT_SUPPORTED|CURRENT_SESSION_BAR_ONLY,2026-08-05,No downloadable history,Keep Fast mode\n"
        "BBB,EXCLUDE_LONG_HISTORY_REQUESTS,true,test,BROWSER_NO_DOWNLOADABLE_HISTORY,REQUEST_RANGE_NOT_SUPPORTED|CURRENT_SESSION_BAR_ONLY,2026-08-05,No downloadable history,Keep Fast mode\n",
        encoding="utf-8",
    )


def test_history_exclusions_skip_requests_but_keep_fast_mode(tmp_path: Path):
    exclusions_path = tmp_path / "exclusions.csv"
    write_exclusions(exclusions_path)
    exclusions = history.load_history_exclusions(exclusions_path)
    included, skipped = history.partition_history_symbols(["AAA", "CCC", "BBB"], exclusions)
    assert included == ["CCC"]
    assert [item.symbol for item in skipped] == ["AAA", "BBB"]
    assert all(item.keep_fast_mode for item in skipped)
    included_override, skipped_override = history.partition_history_symbols(
        ["AAA", "CCC"], exclusions, include_excluded=True
    )
    assert included_override == ["AAA", "CCC"]
    assert skipped_override == []


def test_history_exclusion_outputs_are_non_destructive(tmp_path: Path):
    exclusions_path = tmp_path / "exclusions.csv"
    write_exclusions(exclusions_path)
    skipped = list(history.load_history_exclusions(exclusions_path).values())
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run-manifest.json").write_text('{"utility_version":"test"}\n', encoding="utf-8")
    (run_dir / "run-summary.txt").write_text("summary\n", encoding="utf-8")
    manifest = history.write_history_exclusion_outputs(
        run_dir, skipped, exclusion_file=exclusions_path, override_used=False,
        manifest_file="run-manifest.json", report_file="run-summary.txt",
    )
    assert manifest["history_exclusions"]["requests_skipped"] == 2
    assert manifest["history_exclusions"]["existing_database_rows_deleted"] is False
    assert manifest["history_exclusions"]["fast_mode_unchanged"] is True
    assert (run_dir / "excluded-history-symbols.csv").is_file()
    report = (run_dir / "run-summary.txt").read_text(encoding="utf-8")
    assert "Existing database rows deleted: False" in report


def test_baseline_dry_run_filters_excluded_symbols(tmp_path: Path, capsys):
    input_path = tmp_path / "symbols.csv"
    input_path.write_text("symbol\nAAA\nCCC\nBBB\n", encoding="utf-8")
    exclusions_path = tmp_path / "exclusions.csv"
    write_exclusions(exclusions_path)
    output_root = tmp_path / "archive"
    status = history.main([
        "--mode", "baseline", "--dry-run", "--input", str(input_path),
        "--history-exclusions", str(exclusions_path), "--output-root", str(output_root),
    ])
    assert status == 0
    output = capsys.readouterr().out
    assert '"planned_tasks": 1' in output
    assert '"requests_skipped": 2' in output
    assert '"CCC"' in output
