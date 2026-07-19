from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "security-type-study"
    / "run_security_type_quote_study.py"
)
SPEC = importlib.util.spec_from_file_location("run_security_type_quote_study", MODULE_PATH)
assert SPEC and SPEC.loader
study = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = study
SPEC.loader.exec_module(study)

CONFIG_PATH = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "studies"
    / "study-02a-security-type-quote.json"
)


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


class IncrementingNow:
    def __init__(self):
        self.value = datetime(2026, 7, 19, 5, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        current = self.value
        self.value += timedelta(milliseconds=100)
        return current


class IncrementingClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        current = self.value
        self.value += 0.050
        return current


class FakeSession:
    def __init__(self, response_factory):
        self.response_factory = response_factory
        self.crumb = "secret/crumb=="
        self.refresh_count = 0
        self.calls: list[str] = []

    def prepare(self, force_refresh: bool = False):
        if force_refresh:
            self.refresh_count += 1
            self.crumb = "new-secret"
        return self.crumb

    def open(self, request):
        self.calls.append(request.full_url)
        return self.response_factory(request.full_url)

    def public_summary(self):
        return {
            "session_strategy": "fake-prepared",
            "cookie_count": 1,
            "crumb_retrieved": True,
            "session_refresh_count": self.refresh_count,
            "sensitive_values_persisted": False,
        }


def payload(symbol: str, quote_type: str, *, reverse_keys: bool = False) -> bytes:
    record = {
        "symbol": symbol,
        "quoteType": quote_type,
        "typeDisp": quote_type.title(),
        "exchange": "TEST",
        "fullExchangeName": "Test Exchange",
        "currency": "USD",
        "exchangeTimezoneName": "America/New_York",
        "marketState": "CLOSED",
        "market": "us_market",
        "regularMarketPrice": 100.0,
        "regularMarketTime": 1784430000,
    }
    if reverse_keys:
        record = dict(reversed(list(record.items())))
    body = {"quoteResponse": {"result": [record], "error": None}}
    return json.dumps(body, separators=(",", ":")).encode()


def subject_quote_types():
    definition, endpoint, subjects = study.load_study_definition(CONFIG_PATH)
    del definition, endpoint
    return {subject.symbol: subject.expected_quote_type for subject in subjects}


def response_factory(url: str):
    symbol = parse_qs(urlsplit(url).query)["symbols"][0]
    return FakeResponse(payload(symbol, subject_quote_types()[symbol]), url=url)


def read_csv(path: Path):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_definition_has_twelve_unique_reviewed_subjects():
    definition, endpoint, subjects = study.load_study_definition(CONFIG_PATH)

    assert definition["study_id"] == "study-02a-security-type-quote-baseline"
    assert endpoint.endpoint_id == "quote"
    assert len(subjects) == 12
    assert len({subject.symbol for subject in subjects}) == 12
    assert {subject.symbol for subject in subjects} == {
        "AAPL", "PSA", "PAA", "BRK-B", "SPY", "SHY", "PDI", "VTSAX",
        "^GSPC", "EURUSD=X", "BTC-USD", "CL=F",
    }


def test_plan_preserves_special_symbols_and_builds_twelve_requests():
    definition, endpoint, subjects = study.load_study_definition(CONFIG_PATH)
    plan = study.build_execution_plan(definition, endpoint, subjects, run_id="test-run")

    assert len(plan) == 12
    assert plan[0].subject.symbol == "AAPL"
    assert plan[-1].subject.symbol == "CL=F"
    by_symbol = {item.subject.symbol: item for item in plan}
    assert by_symbol["^GSPC"].request_parameters["symbols"] == "^GSPC"
    assert by_symbol["EURUSD=X"].request_parameters["symbols"] == "EURUSD=X"
    assert by_symbol["BRK-B"].request_parameters["symbols"] == "BRK-B"


def test_canonical_hash_ignores_object_key_order():
    first = json.loads(payload("AAPL", "EQUITY", reverse_keys=False))
    second = json.loads(payload("AAPL", "EQUITY", reverse_keys=True))

    assert json.dumps(first, separators=(",", ":")) != json.dumps(second, separators=(",", ":"))
    assert study.sha256_json(first) == study.sha256_json(second)


def test_capture_extracts_quote_fields_and_redacts_crumb():
    definition, endpoint, subjects = study.load_study_definition(CONFIG_PATH)
    plan = study.build_execution_plan(definition, endpoint, subjects, run_id="test")
    planned = plan[0]
    session = FakeSession(response_factory)

    body, metadata = study.capture_one(
        planned,
        session,
        maximum_attempts=1,
        now=IncrementingNow(),
        clock=IncrementingClock(),
    )

    assert body
    assert metadata["result_classification"] == "EXPECTED_SYMBOL_RETURNED"
    assert metadata["returned_symbol"] == "AAPL"
    assert metadata["returned_quote_type"] == "EQUITY"
    assert metadata["quote_type_match"] is True
    assert metadata["selected_quote_fields"]["marketState"] == "CLOSED"
    assert "crumb=secret%2Fcrumb%3D%3D" in session.calls[0]
    assert "secret" not in json.dumps(metadata)
    assert "crumb=REDACTED" in metadata["request_url_redacted"]
    assert metadata["canonical_json_sha256"] == study.sha256_json(json.loads(body))


def test_missing_requested_symbol_is_preserved_as_evidence():
    definition, endpoint, subjects = study.load_study_definition(CONFIG_PATH)
    planned = study.build_execution_plan(definition, endpoint, subjects, run_id="test")[0]

    def wrong_symbol_factory(url: str):
        return FakeResponse(payload("MSFT", "EQUITY"), url=url)

    session = FakeSession(wrong_symbol_factory)
    _, metadata = study.capture_one(
        planned,
        session,
        maximum_attempts=1,
        now=IncrementingNow(),
        clock=IncrementingClock(),
    )

    assert metadata["result_classification"] == "REQUESTED_SYMBOL_MISSING_FROM_RESULT"
    assert metadata["requested_symbol_returned"] is False
    assert metadata["returned_symbols"] == ["MSFT"]


def test_401_refreshes_once_and_never_persists_secret():
    definition, endpoint, subjects = study.load_study_definition(CONFIG_PATH)
    planned = study.build_execution_plan(definition, endpoint, subjects, run_id="test")[0]
    calls = []

    def factory(url: str):
        calls.append(url)
        if len(calls) == 1:
            headers = Message()
            headers["Content-Type"] = "application/json"
            raise HTTPError(url, 401, "Unauthorized", headers, None)
        return FakeResponse(payload("AAPL", "EQUITY"), url=url)

    session = FakeSession(factory)
    _, metadata = study.capture_one(
        planned,
        session,
        maximum_attempts=2,
        now=IncrementingNow(),
        clock=IncrementingClock(),
    )

    assert metadata["auth_refresh_performed"] is True
    assert metadata["attempt_count"] == 2
    assert [attempt["http_status"] for attempt in metadata["attempts"]] == [401, 200]
    saved = json.dumps(metadata)
    assert "secret/crumb" not in saved
    assert "new-secret" not in saved


def test_full_synthetic_run_writes_twelve_records_and_comparison_tables(tmp_path: Path):
    sessions = []

    def session_factory(timeout: float):
        del timeout
        session = FakeSession(response_factory)
        sessions.append(session)
        return session

    run_dir, manifest = study.run_study(
        definition_path=CONFIG_PATH,
        output_parent=tmp_path,
        timeout=10,
        maximum_attempts=1,
        pause_ms=0,
        session_factory=session_factory,
        sleep=lambda seconds: None,
        now=IncrementingNow(),
        clock=IncrementingClock(),
    )

    assert manifest["summary"]["planned_request_count"] == 12
    assert manifest["summary"]["evidence_record_count"] == 12
    assert manifest["summary"]["expected_symbol_returned_count"] == 12
    assert manifest["summary"]["quote_type_match_count"] == 12
    assert len(manifest["requests"]) == 12

    resolved_path = run_dir / "study-definition.resolved.json"
    assert manifest["study_definition_file"] == "study-definition.resolved.json"
    assert manifest["study_definition_source_file"] == "config/studies/study-02a-security-type-quote.json"
    assert resolved_path.exists()
    assert hashlib.sha256(resolved_path.read_bytes()).hexdigest() == manifest["study_definition_sha256"]

    for request in manifest["requests"]:
        raw_path = run_dir / request["raw_response_file"]
        metadata_path = run_dir / request["metadata_file"]
        raw = raw_path.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == request["raw_response_sha256"]
        assert len(raw) == request["response_bytes"]
        assert request["canonical_json_sha256"] == study.sha256_json(json.loads(raw))
        assert json.loads(metadata_path.read_text(encoding="utf-8")) == request

    result_rows = read_csv(run_dir / "comparison" / "security-type-results.csv")
    summary_rows = read_csv(run_dir / "comparison" / "quote-type-summary.csv")
    assert len(result_rows) == 12
    assert len(summary_rows) == 7
    assert all(row["requested_symbol_returned"] == "True" for row in result_rows)
    assert all(row["quote_type_match"] == "True" for row in result_rows)

    saved_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in run_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".json", ".csv", ".txt"}
    )
    assert "secret/crumb" not in saved_text
    assert "new-secret" not in saved_text


def test_quote_type_mismatch_is_recorded_without_discarding_response():
    definition, endpoint, subjects = study.load_study_definition(CONFIG_PATH)
    spy = next(subject for subject in subjects if subject.symbol == "SPY")
    planned = next(
        item
        for item in study.build_execution_plan(definition, endpoint, subjects, run_id="test")
        if item.subject.symbol == "SPY"
    )

    def mismatch_factory(url: str):
        return FakeResponse(payload(spy.symbol, "EQUITY"), url=url)

    session = FakeSession(mismatch_factory)
    _, metadata = study.capture_one(
        planned,
        session,
        maximum_attempts=1,
        now=IncrementingNow(),
        clock=IncrementingClock(),
    )

    assert metadata["result_classification"] == "EXPECTED_SYMBOL_RETURNED"
    assert metadata["returned_quote_type"] == "EQUITY"
    assert metadata["expected_quote_type"] == "ETF"
    assert metadata["quote_type_match"] is False
