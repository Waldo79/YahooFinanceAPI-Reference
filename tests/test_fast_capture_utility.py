from __future__ import annotations

import importlib.util
import json
import sys
import threading
from datetime import datetime, timedelta, timezone
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "capture-utility" / "yahoo_fast_capture.py"
SPEC = importlib.util.spec_from_file_location("yahoo_fast_capture", MODULE_PATH)
assert SPEC and SPEC.loader
fast = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = fast
SPEC.loader.exec_module(fast)


class FakeResponse:
    def __init__(self, body: bytes, *, status: int = 200, url: str = "https://example.test", content_type: str = "application/json"):
        self._body = body
        self.status = status
        self.url = url
        self.headers = Message()
        self.headers["Content-Type"] = content_type

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
            current = self.value
            self.value += timedelta(milliseconds=10)
            return current


class SafeClock:
    def __init__(self):
        self._lock = threading.Lock()
        self.value = 0.0

    def __call__(self):
        with self._lock:
            current = self.value
            self.value += 0.01
            return current


def row(sequence: int, symbol: str, *, duplicate: bool = False, occurrence: int = 1):
    return fast.InputRow(
        request_sequence=sequence,
        symbol=symbol,
        duplicate_control=duplicate,
        request_occurrence=occurrence,
    )


def http(body: bytes, status: int = 200):
    return fast.HttpResult(
        body=body,
        http_status=status,
        content_type="application/json",
        final_url_redacted="https://example.test?crumb=REDACTED",
        requested_at_utc="2026-08-05T01:00:00.000Z",
        response_received_at_utc="2026-08-05T01:00:00.010Z",
        elapsed_ms=10,
        attempts=[],
        error_message=None,
        session_generation=1,
    )


def test_load_input_rows_has_no_30_symbol_limit_and_preserves_duplicates(tmp_path: Path):
    path = tmp_path / "symbols.csv"
    lines = ["request_sequence,symbol,request_occurrence,duplicate_control"]
    for i in range(1, 101):
        lines.append(f"{i},SYM{i},1,NO")
    lines.append("101,SYM1,2,YES")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    rows = fast.load_input_rows(path)
    assert len(rows) == 101
    assert len({item.symbol for item in rows}) == 100
    assert rows[-1].duplicate_control is True
    assert rows[-1].request_occurrence == 2


def test_load_input_rows_rejects_url_and_bad_character(tmp_path: Path):
    path = tmp_path / "symbols.csv"
    path.write_text("symbol\nhttps://finance.yahoo.com/quote/AAPL\n", encoding="utf-8")
    with pytest.raises(fast.FastCaptureInputError, match="full URLs"):
        fast.load_input_rows(path)
    path.write_text("symbol\nBAD SYMBOL\n", encoding="utf-8")
    with pytest.raises(fast.FastCaptureInputError, match="unsupported characters"):
        fast.load_input_rows(path)


def test_smoke_selection_includes_both_occurrences_of_duplicate_controls():
    rows = [row(i, f"SYM{i}") for i in range(1, 41)]
    rows.append(row(41, "SYM2", duplicate=True, occurrence=2))
    rows.append(row(42, "SYM3", duplicate=True, occurrence=2))
    selected = fast.select_smoke_rows(rows, target_count=12)
    assert len(selected) == 12
    assert sum(item.symbol == "SYM2" for item in selected) == 2
    assert sum(item.symbol == "SYM3" for item in selected) == 2


def test_build_quote_tasks_batches_and_keeps_duplicate_rows():
    rows = [row(1, "AAPL"), row(2, "MSFT"), row(3, "AAPL", duplicate=True, occurrence=2)]
    settings = fast.EndpointSettings(concurrency=2, quote_batch_size=2)
    tasks = fast.build_tasks(rows, ("quote",), settings)
    assert len(tasks) == 2
    assert [r.symbol for r in tasks[0].rows] == ["AAPL", "MSFT"]
    assert [r.symbol for r in tasks[1].rows] == ["AAPL"]


def test_url_redaction_removes_crumb_but_preserves_symbols():
    redacted = fast.redact_url("https://x.test/q?symbols=AAPL%2CMSFT&crumb=secret%2Fvalue&region=US")
    assert "secret" not in redacted
    assert "crumb=REDACTED" in redacted
    assert "symbols=AAPL%2CMSFT" in redacted


def test_quote_analysis_records_partial_result_without_stopping():
    task = fast.CaptureTask("quote-batch-1", "quote", 1, (row(1, "AAPL"), row(2, "BAD")))
    body = b'{"quoteResponse":{"result":[{"symbol":"AAPL"}],"error":null}}'
    classification, returned, request_results, description = fast.analyze_task(task, http(body))
    assert classification == "PARTIAL_SYMBOL_RESULT"
    assert returned == ("AAPL",)
    assert request_results[0].classification == "SUCCESS_RESULT_RETURNED"
    assert request_results[1].classification == "REQUESTED_SYMBOL_MISSING_FROM_RESULT"
    assert description is None


def test_quote_duplicate_occurrences_share_returned_result():
    task = fast.CaptureTask(
        "quote-batch-1",
        "quote",
        1,
        (row(1, "AAPL"), row(2, "AAPL", duplicate=True, occurrence=2)),
    )
    body = b'{"quoteResponse":{"result":[{"symbol":"AAPL"}],"error":null}}'
    _, _, results, _ = fast.analyze_task(task, http(body))
    assert all(item.classification == "SUCCESS_RESULT_RETURNED" for item in results)
    assert results[0].response_result_reused_for_duplicate_occurrence is False
    assert results[1].response_result_reused_for_duplicate_occurrence is True


def test_quote_summary_no_fundamentals_is_not_transport_failure():
    task = fast.CaptureTask("quoteSummary-1", "quoteSummary", 1, (row(1, "FUND"),))
    body = b'{"quoteSummary":{"result":null,"error":{"code":"Not Found","description":"No fundamentals data found for symbol: FUND"}}}'
    classification, _, results, _ = fast.analyze_task(task, http(body))
    assert classification == "NO_FUNDAMENTALS_AVAILABLE"
    assert results[0].classification == "NO_FUNDAMENTALS_AVAILABLE"


def test_chart_not_found_is_symbol_level_terminal_state():
    task = fast.CaptureTask("chart-1", "chart", 1, (row(1, "BAD"),))
    body = b'{"chart":{"result":null,"error":{"code":"Not Found","description":"No data found, symbol may be delisted"}}}'
    classification, _, results, _ = fast.analyze_task(task, http(body))
    assert classification == "SYMBOL_NOT_AVAILABLE"
    assert fast.status_is_review(results[0].classification) is False


def test_options_empty_result_is_not_optionable_and_run_can_continue():
    task = fast.CaptureTask("options-1", "options", 1, (row(1, "VTSAX"),))
    body = b'{"optionChain":{"result":[],"error":null}}'
    classification, _, results, _ = fast.analyze_task(task, http(body))
    assert classification == "NOT_OPTIONABLE_OR_NO_CHAIN"
    assert fast.status_is_review(results[0].classification) is False


def test_options_quote_only_result_is_not_optionable_and_run_can_continue():
    task = fast.CaptureTask("options-quote-only", "options", 1, (row(1, "VTSAX"),))
    body = b'{"optionChain":{"result":[{"quote":{"symbol":"VTSAX"},"expirationDates":[],"options":[]}],"error":null}}'
    classification, returned, results, _ = fast.analyze_task(task, http(body))
    assert classification == "NOT_OPTIONABLE_OR_NO_CHAIN"
    assert returned == ("VTSAX",)
    assert results[0].classification == "NOT_OPTIONABLE_OR_NO_CHAIN"
    assert fast.status_is_review(results[0].classification) is False


def test_request_retries_429_and_redacts_url():
    calls = []
    headers = Message()
    headers["Content-Type"] = "application/json"
    success = b'{"quoteResponse":{"result":[{"symbol":"AAPL"}],"error":null}}'

    def opener(request, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise HTTPError(request.full_url, 429, "Too Many Requests", headers, None)
        return FakeResponse(success, url=request.full_url)

    sleeps = []
    task = fast.CaptureTask("quote-batch-1", "quote", 1, (row(1, "AAPL"),))
    result = fast.request_with_retry(
        task,
        session=fast.StaticSession(),
        settings=fast.EndpointSettings(concurrency=1),
        timeout_seconds=5,
        retry_policy=fast.RetryPolicy(2, (0.0,)),
        user_agent="test-agent",
        gate=fast.SharedBackoffGate(clock=SafeClock(), sleep=sleeps.append),
        opener=opener,
        sleep=sleeps.append,
        clock=SafeClock(),
        now=SafeNow(),
    )
    assert len(calls) == 2
    assert result.http_status == 200
    assert "test-crumb" not in result.final_url_redacted
    assert "crumb=REDACTED" in result.final_url_redacted


def test_apply_quote_retest_changes_batch_omission_to_individual_success():
    primary_task = fast.CaptureTask("quote-batch-1", "quote", 1, (row(1, "AAPL"), row(2, "MSFT")))
    primary_body = b'{"quoteResponse":{"result":[{"symbol":"AAPL"}],"error":null}}'
    _, _, primary_rr, _ = fast.analyze_task(primary_task, http(primary_body))
    primary = fast.TaskResult(
        task_key="quote-batch-1", endpoint="quote", task_sequence=1,
        requested_symbols=("AAPL", "MSFT"), requested_row_sequences=(1,2),
        result_classification="PARTIAL_SYMBOL_RESULT", http_status=200,
        returned_symbols=("AAPL",), request_results=primary_rr,
        raw_response_file=None, raw_response_sha256=None, raw_response_bytes=0,
        metadata_file="m", attempts=[], elapsed_ms=1, error_message=None,
        error_description=None, retest=False,
    )
    retest_task = fast.CaptureTask("quote-retest-2", "quote", 2, (row(2, "MSFT"),), retest=True)
    retest_body = b'{"quoteResponse":{"result":[{"symbol":"MSFT"}],"error":null}}'
    _, _, retest_rr, _ = fast.analyze_task(retest_task, http(retest_body))
    retest = fast.TaskResult(
        task_key="quote-retest-2", endpoint="quote", task_sequence=2,
        requested_symbols=("MSFT",), requested_row_sequences=(2,),
        result_classification="SUCCESS_RESULT_RETURNED", http_status=200,
        returned_symbols=("MSFT",), request_results=retest_rr,
        raw_response_file=None, raw_response_sha256=None, raw_response_bytes=0,
        metadata_file="m2", attempts=[], elapsed_ms=1, error_message=None,
        error_description=None, retest=True,
    )
    fast.apply_quote_retests([primary], [retest])
    assert primary.request_results[1].classification == "BATCH_OMISSION_INDIVIDUAL_SUCCESS"
    assert primary.request_results[1].individual_retest_classification == "SUCCESS_RESULT_RETURNED"


def test_checkpoint_round_trip(tmp_path: Path):
    run_state = fast.RunState(tmp_path, tmp_path / "checkpoint.jsonl")
    result = fast.TaskResult(
        task_key="t1", endpoint="quote", task_sequence=1,
        requested_symbols=("AAPL",), requested_row_sequences=(1,),
        result_classification="SUCCESS_RESULT_RETURNED", http_status=200,
        returned_symbols=("AAPL",), request_results=[fast.RequestResult(
            request_sequence=1, symbol="AAPL", endpoint="quote", task_key="t1",
            request_occurrence=1, duplicate_control=False,
            classification="SUCCESS_RESULT_RETURNED", http_status=200,
        )], raw_response_file="raw/a", raw_response_sha256="abc", raw_response_bytes=3,
        metadata_file="meta/a", attempts=[], elapsed_ms=10, error_message=None,
        error_description=None,
    )
    fast.append_checkpoint(run_state, result)
    loaded = fast.load_checkpoint(run_state.checkpoint_path)
    assert loaded["t1"].returned_symbols == ("AAPL",)
    assert loaded["t1"].request_results[0].classification == "SUCCESS_RESULT_RETURNED"


def test_offline_smoke_run_writes_manifest_raw_metadata_and_summary(tmp_path: Path):
    rows = [row(1, "AAPL"), row(2, "MSFT")]

    def opener(request, timeout):
        url = request.full_url
        if "/v7/finance/quote?" in url:
            symbols_text = url.split("symbols=", 1)[1].split("&", 1)[0]
            if "%2C" in symbols_text:
                symbols = ["AAPL", "MSFT"]
            else:
                symbols = ["AAPL"] if "AAPL" in url else ["MSFT"]
            payload = {"quoteResponse": {"result": [{"symbol": symbol} for symbol in symbols], "error": None}}
        elif "/quoteSummary/" in url:
            symbol = "AAPL" if "AAPL" in url else "MSFT"
            payload = {"quoteSummary": {"result": [{"quoteType": {"symbol": symbol}}], "error": None}}
        elif "/v8/finance/chart/" in url:
            symbol = "AAPL" if "AAPL" in url else "MSFT"
            payload = {"chart": {"result": [{"meta": {"symbol": symbol}}], "error": None}}
        elif "/v7/finance/options/" in url:
            symbol = "AAPL" if "AAPL" in url else "MSFT"
            payload = {"optionChain": {"result": [{"quote": {"symbol": symbol}}], "error": None}}
        else:
            raise AssertionError(url)
        return FakeResponse(json.dumps(payload).encode(), url=url)

    settings = {
        "quote": fast.EndpointSettings(concurrency=2, quote_batch_size=100),
        "quoteSummary": fast.EndpointSettings(concurrency=2),
        "chart": fast.EndpointSettings(concurrency=2),
        "options": fast.EndpointSettings(concurrency=2),
    }
    run_dir, manifest = fast.run_capture(
        rows,
        input_file=tmp_path / "input.csv",
        outdir=tmp_path / "captures",
        settings_by_endpoint=settings,
        timeout_seconds=5,
        retry_policy=fast.RetryPolicy(1, ()),
        session=fast.StaticSession(),
        opener=opener,
        sleep=lambda seconds: None,
        clock=SafeClock(),
        now=SafeNow(),
        progress=lambda message: None,
    )
    assert manifest["summary"]["task_count"] == 7
    assert manifest["summary"]["request_result_count"] == 8
    assert manifest["summary"]["review_request_results"] == 0
    assert (run_dir / "run-manifest.json").exists()
    summary_text = (run_dir / "run-summary.txt").read_text(encoding="utf-8")
    assert "Tasks                 : 7" in summary_text
    assert "Per-symbol results    : 8" in summary_text
    assert "Task classifications:" in summary_text
    assert "Per-symbol endpoint classifications:" in summary_text
    assert (run_dir / "summary" / "request-results.csv").exists()
    assert list((run_dir / "raw").rglob("*.raw.json"))
    saved = (run_dir / "run-manifest.json").read_text(encoding="utf-8")
    assert "test-crumb" not in saved
    assert manifest["privacy"]["crumb_persisted"] is False


def test_dry_run_full_universe_task_count(tmp_path: Path):
    rows = [row(i, f"S{i}") for i in range(1, 1538)]
    for offset, symbol in enumerate(["S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9", "S10"], start=1538):
        rows.append(row(offset, symbol, duplicate=True, occurrence=2))
    quote_settings = fast.EndpointSettings(concurrency=4, quote_batch_size=100)
    tasks = []
    for endpoint, settings in [
        ("quote", quote_settings),
        ("quoteSummary", fast.EndpointSettings(concurrency=10)),
        ("chart", fast.EndpointSettings(concurrency=10)),
        ("options", fast.EndpointSettings(concurrency=5)),
    ]:
        tasks.extend(fast.build_tasks(rows, (endpoint,), settings))
    assert len(rows) == 1547
    assert len([task for task in tasks if task.endpoint == "quote"]) == 16
    assert len(tasks) == 16 + 1547 * 3


def test_persist_retest_updates_rewrites_primary_metadata_and_checkpoint(tmp_path: Path):
    run_state = fast.RunState(tmp_path, tmp_path / "checkpoint.jsonl")
    metadata_path = tmp_path / "metadata" / "quote" / "batch.metadata.json"
    metadata_path.parent.mkdir(parents=True)
    metadata_path.write_text('{"request_url_redacted":"https://x.test?crumb=REDACTED"}\n', encoding="utf-8")
    rr = fast.RequestResult(
        request_sequence=1,
        symbol="MSFT",
        endpoint="quote",
        task_key="quote-batch-1",
        request_occurrence=1,
        duplicate_control=False,
        classification="BATCH_OMISSION_INDIVIDUAL_SUCCESS",
        http_status=200,
        individual_retest_classification="SUCCESS_RESULT_RETURNED",
    )
    result = fast.TaskResult(
        task_key="quote-batch-1",
        endpoint="quote",
        task_sequence=1,
        requested_symbols=("MSFT",),
        requested_row_sequences=(1,),
        result_classification="PARTIAL_SYMBOL_RESULT",
        http_status=200,
        returned_symbols=(),
        request_results=[rr],
        raw_response_file=None,
        raw_response_sha256=None,
        raw_response_bytes=0,
        metadata_file="metadata/quote/batch.metadata.json",
        attempts=[],
        elapsed_ms=10,
        error_message=None,
        error_description=None,
    )
    fast.persist_retest_updates(run_state, [result])
    saved = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert saved["retest_reconciled"] is True
    assert saved["request_url_redacted"].endswith("REDACTED")
    loaded = fast.load_checkpoint(run_state.checkpoint_path)
    assert loaded["quote-batch-1"].request_results[0].classification == "BATCH_OMISSION_INDIVIDUAL_SUCCESS"
