from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "capture-utility" / "yahoo_capture.py"
SPEC = importlib.util.spec_from_file_location("yahoo_capture", MODULE_PATH)
assert SPEC and SPEC.loader
capture = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = capture
SPEC.loader.exec_module(capture)


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        url: str = "https://example.test/quote?symbols=MSFT",
        content_type: str = "application/json; charset=utf-8",
    ):
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


class IncrementingNow:
    def __init__(self):
        self.value = datetime(2026, 7, 11, 20, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        current = self.value
        self.value += timedelta(milliseconds=100)
        return current


class IncrementingClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        current = self.value
        self.value += 0.123
        return current


def write_master_fields(path: Path) -> None:
    path.write_text(
        "field_id,field_name,endpoint,json_path,yahoo_type\n"
        "YF00001,symbol,quote,quoteResponse.result[].symbol,string\n"
        "YF00002,regularMarketTime,quote,quoteResponse.result[].regularMarketTime,number\n",
        encoding="utf-8",
    )


def test_load_symbol_table_preserves_enabled_order_and_rejects_urls(tmp_path: Path):
    symbols = tmp_path / "symbols.csv"
    symbols.write_text(
        "symbol,enabled,project_security_type,endpoint_id,notes\n"
        "AAPL,yes,Stock,quote,first\n"
        "SPY,no,ETF,quote,disabled\n"
        "^GSPC,1,Index,quote,last\n",
        encoding="utf-8",
    )
    rows = capture.load_symbol_table(symbols)
    assert [row.symbol for row in rows] == ["AAPL", "^GSPC"]
    assert rows[1].project_security_type == "Index"

    symbols.write_text(
        "symbol,enabled,project_security_type,endpoint_id,notes\n"
        "https://query1.finance.yahoo.com/v7/finance/quote,yes,,,\n",
        encoding="utf-8",
    )
    with pytest.raises(capture.CaptureInputError, match="full URLs"):
        capture.load_symbol_table(symbols)


def test_flatten_json_preserves_indexes_null_and_empty_containers():
    data = {
        "quoteResponse": {
            "result": [
                {
                    "symbol": "MSFT",
                    "value": None,
                    "emptyArray": [],
                    "emptyObject": {},
                }
            ]
        }
    }
    fields = {field.json_path: field for field in capture.flatten_json(data)}
    assert fields["quoteResponse.result[0].symbol"].raw_value == "MSFT"
    assert fields["quoteResponse.result[0].value"].raw_type == "null"
    assert fields["quoteResponse.result[0].emptyArray"].raw_type == "array"
    assert fields["quoteResponse.result[0].emptyObject"].raw_type == "object"


def test_capture_run_preserves_raw_bytes_and_writes_manifest(tmp_path: Path):
    raw = b'{"quoteResponse":{"result":[{"regularMarketTime":1783800000,"symbol":"MSFT"}],"error":null}}\n'

    def opener(request, timeout):
        assert "symbols=MSFT" in request.full_url
        assert timeout == 10
        return FakeResponse(raw, url=request.full_url)

    master = tmp_path / "master.csv"
    write_master_fields(master)
    run_dir, manifest = capture.run_capture(
        [capture.SymbolRequest("MSFT", project_security_type="Stock")],
        outdir=tmp_path / "captures",
        input_file="test-symbols.csv",
        master_field_path=master,
        pause_between_requests_ms=0,
        timeout_seconds=10,
        retry_policy=capture.RetryPolicy(1, ()),
        user_agent="test-agent",
        auth_mode="none",
        opener=opener,
        sleep=lambda seconds: None,
        clock=IncrementingClock(),
        now=IncrementingNow(),
    )

    request = manifest["requests"][0]
    raw_path = run_dir / request["raw_response_file"]
    metadata_path = run_dir / request["metadata_file"]
    normalized_path = run_dir / request["normalized_output_file"]

    assert raw_path.read_bytes() == raw
    assert request["raw_response_sha256"] == hashlib.sha256(raw).hexdigest()
    assert request["result_classification"] == "SUCCESS_RESULT_RETURNED"
    assert request["returned_symbol"] == "MSFT"
    assert metadata_path.exists()
    assert normalized_path.exists()
    normalized = normalized_path.read_text(encoding="utf-8")
    assert "quoteResponse.result[0].symbol" in normalized
    assert "Known" in normalized
    assert "decoded_utc" in normalized
    assert manifest["summary"]["success_result_returned"] == 1
    assert json.loads((run_dir / "run-manifest.json").read_text(encoding="utf-8"))["run_completed_at_utc"]


def test_requested_symbol_missing_is_not_treated_as_null():
    raw = b'{"quoteResponse":{"result":[{"symbol":"AAPL"}],"error":null}}'
    analysis = capture.analyze_quote_response(raw, 200, "MSFT")
    assert analysis.classification == "REQUESTED_SYMBOL_MISSING_FROM_RESULT"
    assert analysis.returned_symbol == "AAPL"
    assert analysis.returned_symbols == ("AAPL",)


def test_retry_on_429_then_success():
    calls = []
    headers = Message()
    headers["Content-Type"] = "application/json"
    success = b'{"quoteResponse":{"result":[{"symbol":"MSFT"}],"error":null}}'

    def opener(request, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise HTTPError(request.full_url, 429, "Too Many Requests", headers, None)
        return FakeResponse(success, url=request.full_url)

    sleeps = []
    result = capture.request_with_retry(
        "https://example.test/quote?symbols=MSFT",
        timeout_seconds=5,
        retry_policy=capture.RetryPolicy(2, (0.5,)),
        user_agent="test-agent",
        opener=opener,
        sleep=sleeps.append,
        clock=IncrementingClock(),
        now=IncrementingNow(),
    )
    assert len(calls) == 2
    assert sleeps == [0.5]
    assert result.http_status == 200
    assert result.body == success
    assert len(result.attempts) == 2


def test_anonymous_session_adds_crumb_and_never_persists_secret(tmp_path: Path):
    secret_crumb = "crumb/one=="
    calls: list[str] = []
    raw = b'{"quoteResponse":{"result":[{"symbol":"MSFT"}],"error":null}}'

    def opener(request, timeout):
        calls.append(request.full_url)
        if request.full_url == capture.YAHOO_BASIC_COOKIE_URL:
            return FakeResponse(b"cookie", url=request.full_url, content_type="text/html")
        if request.full_url == capture.YAHOO_BASIC_CRUMB_URL:
            return FakeResponse(secret_crumb.encode(), url=request.full_url, content_type="text/plain")
        assert "symbols=MSFT" in request.full_url
        assert "crumb=crumb%2Fone%3D%3D" in request.full_url
        return FakeResponse(raw, url=request.full_url)

    master = tmp_path / "master.csv"
    write_master_fields(master)
    session = capture.YahooAnonymousSession(
        user_agent="test-agent",
        timeout_seconds=10,
        opener=opener,
    )
    run_dir, manifest = capture.run_capture(
        [capture.SymbolRequest("MSFT")],
        outdir=tmp_path / "captures",
        input_file="test.csv",
        master_field_path=master,
        pause_between_requests_ms=0,
        timeout_seconds=10,
        retry_policy=capture.RetryPolicy(1, ()),
        user_agent="test-agent",
        request_session=session,
        sleep=lambda seconds: None,
        clock=IncrementingClock(),
        now=IncrementingNow(),
    )

    request_meta = manifest["requests"][0]
    assert request_meta["result_classification"] == "SUCCESS_RESULT_RETURNED"
    assert request_meta["auth_strategy"] == "basic-query1"
    assert "crumb=REDACTED" in request_meta["request_url_redacted"]
    saved = (run_dir / "run-manifest.json").read_text(encoding="utf-8")
    metadata_saved = (run_dir / request_meta["metadata_file"]).read_text(encoding="utf-8")
    assert secret_crumb not in saved
    assert secret_crumb not in metadata_saved
    assert "crumb%2Fone" not in saved
    assert manifest["authentication"]["sensitive_values_persisted"] is False
    assert calls[:2] == [capture.YAHOO_BASIC_COOKIE_URL, capture.YAHOO_BASIC_CRUMB_URL]


def test_401_refreshes_session_once_and_retries_successfully(tmp_path: Path):
    calls: list[str] = []
    crumb_count = 0
    success = b'{"quoteResponse":{"result":[{"symbol":"MSFT"}],"error":null}}'
    headers = Message()
    headers["Content-Type"] = "application/json"

    def opener(request, timeout):
        nonlocal crumb_count
        calls.append(request.full_url)
        if request.full_url == capture.YAHOO_BASIC_COOKIE_URL:
            return FakeResponse(b"cookie", url=request.full_url, content_type="text/html")
        if request.full_url == capture.YAHOO_BASIC_CRUMB_URL:
            crumb_count += 1
            crumb = b"old-secret" if crumb_count == 1 else b"new-secret"
            return FakeResponse(crumb, url=request.full_url, content_type="text/plain")
        if "crumb=old-secret" in request.full_url:
            raise HTTPError(request.full_url, 401, "Unauthorized", headers, None)
        assert "crumb=new-secret" in request.full_url
        return FakeResponse(success, url=request.full_url)

    master = tmp_path / "master.csv"
    write_master_fields(master)
    session = capture.YahooAnonymousSession(
        user_agent="test-agent",
        timeout_seconds=10,
        opener=opener,
    )
    run_dir, manifest = capture.run_capture(
        [capture.SymbolRequest("MSFT")],
        outdir=tmp_path / "captures",
        input_file="test.csv",
        master_field_path=master,
        pause_between_requests_ms=0,
        timeout_seconds=10,
        retry_policy=capture.RetryPolicy(1, ()),
        user_agent="test-agent",
        request_session=session,
        sleep=lambda seconds: None,
        clock=IncrementingClock(),
        now=IncrementingNow(),
    )

    request_meta = manifest["requests"][0]
    assert request_meta["result_classification"] == "SUCCESS_RESULT_RETURNED"
    assert request_meta["auth_refresh_performed"] is True
    assert request_meta["attempt_count"] == 2
    assert [attempt["http_status"] for attempt in request_meta["attempts"]] == [401, 200]
    assert manifest["authentication"]["refresh_count"] == 1
    saved = (run_dir / "run-manifest.json").read_text(encoding="utf-8")
    assert "old-secret" not in saved
    assert "new-secret" not in saved


def test_anonymous_session_uses_query2_fallback_when_query1_crumb_is_invalid():
    calls: list[str] = []

    def opener(request, timeout):
        calls.append(request.full_url)
        if request.full_url in {capture.YAHOO_BASIC_COOKIE_URL, capture.YAHOO_FALLBACK_COOKIE_URL}:
            return FakeResponse(b"cookie", url=request.full_url, content_type="text/html")
        if request.full_url == capture.YAHOO_BASIC_CRUMB_URL:
            return FakeResponse(b"", url=request.full_url, content_type="text/plain")
        if request.full_url == capture.YAHOO_FALLBACK_CRUMB_URL:
            return FakeResponse(b"fallback-secret", url=request.full_url, content_type="text/plain")
        raise AssertionError(request.full_url)

    session = capture.YahooAnonymousSession(
        user_agent="test-agent",
        timeout_seconds=10,
        opener=opener,
    )
    url = session.quote_url("AAPL", capture.DEFAULT_BASE_URL)
    assert "crumb=fallback-secret" in url
    assert session.strategy == "finance-query2-fallback"
    assert calls == [
        capture.YAHOO_BASIC_COOKIE_URL,
        capture.YAHOO_BASIC_CRUMB_URL,
        capture.YAHOO_FALLBACK_COOKIE_URL,
        capture.YAHOO_FALLBACK_CRUMB_URL,
    ]


def make_valid_run(tmp_path: Path, progress=None):
    raw = b'{"quoteResponse":{"result":[{"regularMarketTime":1783800000,"symbol":"MSFT"}],"error":null}}\n'

    def opener(request, timeout):
        return FakeResponse(raw, url=request.full_url)

    master = tmp_path / "master.csv"
    write_master_fields(master)
    repository_root = tmp_path / "repo"
    symbols_file = repository_root / "tools" / "capture-utility" / "symbols.csv"
    symbols_file.parent.mkdir(parents=True)
    symbols_file.write_text("symbol,enabled\nMSFT,yes\n", encoding="utf-8")
    run_dir, manifest = capture.run_capture(
        [capture.SymbolRequest("MSFT", project_security_type="Stock")],
        outdir=tmp_path / "captures",
        input_file=str(symbols_file),
        master_field_path=master,
        pause_between_requests_ms=0,
        timeout_seconds=10,
        retry_policy=capture.RetryPolicy(1, ()),
        user_agent="test-agent",
        auth_mode="none",
        opener=opener,
        sleep=lambda seconds: None,
        clock=IncrementingClock(),
        now=IncrementingNow(),
        repository_root=repository_root,
        progress=progress,
    )
    return run_dir, manifest, master, repository_root


def test_default_output_is_repository_root_relative():
    assert capture.DEFAULT_OUTDIR == capture.REPOSITORY_ROOT / "captures" / "local"
    assert capture.DEFAULT_OUTDIR.is_absolute()


def test_default_pause_is_zero():
    args = capture.build_parser().parse_args([])
    assert capture.DEFAULT_PAUSE_MS == 0
    assert args.pause_ms == 0


def test_pause_override_is_preserved():
    args = capture.build_parser().parse_args(["--pause-ms", "25"])
    assert args.pause_ms == 25


def test_manifest_paths_are_portable_and_do_not_expose_external_directories(tmp_path: Path):
    run_dir, manifest, master, repository_root = make_valid_run(tmp_path)
    assert manifest["input_file"] == "tools/capture-utility/symbols.csv"
    assert manifest["input_file_scope"] == "repository"
    assert manifest["master_field_database"] == master.name
    assert manifest["master_field_database_scope"] == "external"
    saved = (run_dir / "run-manifest.json").read_text(encoding="utf-8")
    assert str(tmp_path) not in saved


def test_capture_progress_reports_sequence_symbol_http_and_classification(tmp_path: Path):
    progress: list[str] = []
    make_valid_run(tmp_path, progress=progress.append)
    assert progress == ["[01/01] MSFT ... HTTP 200 SUCCESS_RESULT_RETURNED"]


def test_validate_run_passes_and_writes_reports(tmp_path: Path):
    run_dir, manifest, master, repository_root = make_valid_run(tmp_path)
    report = capture.validate_run(run_dir, master_field_path=master)
    assert report["status"] == "PASS"
    assert report["counts"]["errors"] == 0
    assert report["counts"]["raw_files_checked"] == 1
    assert report["counts"]["metadata_files_checked"] == 1
    assert report["counts"]["normalized_files_checked"] == 1
    assert (run_dir / capture.VALIDATION_JSON_FILENAME).exists()
    assert (run_dir / capture.VALIDATION_TEXT_FILENAME).exists()


def test_validate_run_detects_raw_hash_and_size_changes(tmp_path: Path):
    run_dir, manifest, master, repository_root = make_valid_run(tmp_path)
    raw_path = run_dir / manifest["requests"][0]["raw_response_file"]
    raw_path.write_bytes(raw_path.read_bytes() + b"tampered")
    report = capture.validate_run(run_dir, master_field_path=master, write_reports=False)
    codes = {issue["code"] for issue in report["issues"]}
    assert report["status"] == "FAIL"
    assert "RAW_SHA256_MISMATCH" in codes
    assert "RAW_SIZE_MISMATCH" in codes


def test_validate_run_detects_unredacted_crumb_without_echoing_secret(tmp_path: Path):
    run_dir, manifest, master, repository_root = make_valid_run(tmp_path)
    metadata_rel = manifest["requests"][0]["metadata_file"]
    metadata_path = run_dir / metadata_rel
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["request_url_redacted"] = "https://example.test/quote?symbols=MSFT&crumb=private-value"
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    manifest["requests"][0] = metadata
    (run_dir / "run-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    report = capture.validate_run(run_dir, master_field_path=master, write_reports=False)
    assert report["status"] == "FAIL"
    assert any(issue["code"] == "UNREDACTED_CRUMB" for issue in report["issues"])
    assert "private-value" not in json.dumps(report)


def test_validate_run_detects_unreferenced_extra_file(tmp_path: Path):
    run_dir, manifest, master, repository_root = make_valid_run(tmp_path)
    (run_dir / "raw" / "extra.raw.json").write_text("{}", encoding="utf-8")
    report = capture.validate_run(run_dir, master_field_path=master, write_reports=False)
    assert report["status"] == "FAIL"
    assert any(issue["code"] == "UNREFERENCED_EXTRA_FILE" for issue in report["issues"])
