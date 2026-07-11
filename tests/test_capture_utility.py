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
    def __init__(self, body: bytes, *, status: int = 200, url: str = "https://example.test/quote?symbols=MSFT"):
        self._body = body
        self.status = status
        self.url = url
        self.headers = Message()
        self.headers["Content-Type"] = "application/json; charset=utf-8"

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
