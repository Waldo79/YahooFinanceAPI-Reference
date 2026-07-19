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

import pytest


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "session-mode-study"
    / "run_session_mode_study.py"
)
SPEC = importlib.util.spec_from_file_location("run_session_mode_study", MODULE_PATH)
assert SPEC and SPEC.loader
study = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = study
SPEC.loader.exec_module(study)

CONFIG_PATH = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "studies"
    / "study-01-session-modes.json"
)


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        url: str = "https://example.test",
        content_type: str = "application/json",
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
        self.value = datetime(2026, 7, 19, 2, 0, 0, tzinfo=timezone.utc)

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


class FakePreparedSession:
    def __init__(self, mode: study.ModeDefinition, response_factory):
        self.mode = mode
        self.response_factory = response_factory
        self.crumb = "secret/crumb=="
        self.strategy = "fake-prepared"
        self.refresh_count = 0
        self.calls: list[str] = []

    def prepare(self, force_refresh: bool = False):
        if force_refresh:
            self.refresh_count += 1
            self.crumb = "new-secret"
        return self.crumb

    def open(self, request):
        self.calls.append(request.full_url)
        return self.response_factory(request.full_url, self.mode)

    def public_summary(self):
        return {
            "session_strategy": self.strategy,
            "cookie_count": 1,
            "crumb_retrieved": True,
            "session_refresh_count": self.refresh_count,
            "sensitive_values_persisted": False,
        }


class FakeNoSession:
    def __init__(self, mode: study.ModeDefinition, response_factory):
        self.mode = mode
        self.response_factory = response_factory
        self.calls: list[str] = []

    def prepare(self, force_refresh: bool = False):
        raise AssertionError("no-session must not call prepare")

    def open(self, request):
        self.calls.append(request.full_url)
        return self.response_factory(request.full_url, self.mode)

    def public_summary(self):
        return {
            "session_strategy": "no-prepared-session",
            "cookie_count": 0,
            "crumb_retrieved": False,
            "session_refresh_count": 0,
            "sensitive_values_persisted": False,
        }


def payload_for(endpoint_id: str) -> bytes:
    expected = {
        "quote": "quoteResponse",
        "chart": "chart",
        "quote-summary": "quoteSummary",
        "search": "quotes",
        "screener": "finance",
        "fundamentals-timeseries": "timeseries",
        "options": "optionChain",
    }[endpoint_id]
    value = [{"symbol": "AAPL"}] if expected == "quotes" else {"result": [{"symbol": "AAPL"}]}
    return json.dumps({expected: value}, separators=(",", ":")).encode()


def response_factory(url: str, mode: study.ModeDefinition):
    del mode
    path = urlsplit(url).path
    endpoint_id = (
        "quote-summary" if "quoteSummary" in path
        else "fundamentals-timeseries" if "fundamentals-timeseries" in path
        else "screener" if "screener" in path
        else "options" if "/options/" in path
        else "chart" if "/chart/" in path
        else "search" if path.endswith("/search")
        else "quote"
    )
    return FakeResponse(payload_for(endpoint_id), url=url)


def read_csv(path: Path):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_definition_builds_seven_by_three_endpoint_major_plan():
    now = datetime(2026, 7, 19, 2, 0, 0, tzinfo=timezone.utc)
    definition, modes, requests = study.load_study_definition(CONFIG_PATH, now=now)
    plan = study.build_execution_plan(definition, modes, requests, run_id="test-run")

    assert len(modes) == 3
    assert len(requests) == 7
    assert len(plan) == 21
    assert [item.mode.session_mode for item in plan[:3]] == [
        "cookie-crumb",
        "cookie-only",
        "no-session",
    ]
    assert {item.request.endpoint_id for item in plan} == {
        "quote",
        "chart",
        "quote-summary",
        "search",
        "screener",
        "fundamentals-timeseries",
        "options",
    }
    fundamentals = next(
        item.request for item in plan
        if item.request.endpoint_id == "fundamentals-timeseries"
    )
    assert int(fundamentals.params["period2"]) == int(now.timestamp())
    assert int(fundamentals.params["period2"]) - int(fundamentals.params["period1"]) == 730 * 86400


def test_cookie_crumb_sends_secret_but_metadata_redacts_it():
    definition, modes, requests = study.load_study_definition(
        CONFIG_PATH,
        now=datetime(2026, 7, 19, tzinfo=timezone.utc),
    )
    plan = study.build_execution_plan(definition, modes, requests, run_id="test")
    planned = next(item for item in plan if item.mode.session_mode == "cookie-crumb")
    session = FakePreparedSession(planned.mode, response_factory)

    body, metadata = study.capture_one(
        planned,
        session,
        maximum_attempts=1,
        now=IncrementingNow(),
        clock=IncrementingClock(),
    )

    assert body
    assert "crumb=secret%2Fcrumb%3D%3D" in session.calls[0]
    assert "secret" not in json.dumps(metadata)
    assert "crumb=REDACTED" in metadata["request_url_redacted"]
    assert metadata["crumb_sent"] is True
    assert metadata["canonical_json_sha256"] == study.sha256_json(json.loads(body))


def test_cookie_only_prepares_but_does_not_send_crumb():
    definition, modes, requests = study.load_study_definition(
        CONFIG_PATH,
        now=datetime(2026, 7, 19, tzinfo=timezone.utc),
    )
    plan = study.build_execution_plan(definition, modes, requests, run_id="test")
    planned = next(item for item in plan if item.mode.session_mode == "cookie-only")
    session = FakePreparedSession(planned.mode, response_factory)

    _, metadata = study.capture_one(
        planned,
        session,
        maximum_attempts=1,
        now=IncrementingNow(),
        clock=IncrementingClock(),
    )

    assert "crumb=" not in session.calls[0]
    assert metadata["crumb_retrieved"] is True
    assert metadata["crumb_sent"] is False


def test_no_session_never_prepares_or_sends_crumb():
    definition, modes, requests = study.load_study_definition(
        CONFIG_PATH,
        now=datetime(2026, 7, 19, tzinfo=timezone.utc),
    )
    plan = study.build_execution_plan(definition, modes, requests, run_id="test")
    planned = next(item for item in plan if item.mode.session_mode == "no-session")
    session = FakeNoSession(planned.mode, response_factory)

    _, metadata = study.capture_one(
        planned,
        session,
        maximum_attempts=1,
        now=IncrementingNow(),
        clock=IncrementingClock(),
    )

    assert "crumb=" not in session.calls[0]
    assert metadata["cookie_count"] == 0
    assert metadata["crumb_retrieved"] is False
    assert metadata["crumb_sent"] is False


def test_prepared_mode_refreshes_once_on_401_and_keeps_secret_redacted():
    definition, modes, requests = study.load_study_definition(
        CONFIG_PATH,
        now=datetime(2026, 7, 19, tzinfo=timezone.utc),
    )
    plan = study.build_execution_plan(definition, modes, requests, run_id="test")
    planned = next(item for item in plan if item.mode.session_mode == "cookie-crumb")
    calls = []

    def factory(url: str, mode: study.ModeDefinition):
        del mode
        calls.append(url)
        if len(calls) == 1:
            headers = Message()
            headers["Content-Type"] = "application/json"
            raise HTTPError(url, 401, "Unauthorized", headers, None)
        return FakeResponse(payload_for(planned.request.endpoint_id), url=url)

    session = FakePreparedSession(planned.mode, factory)
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
    assert "secret" not in json.dumps(metadata)
    assert "new-secret" not in json.dumps(metadata)


def test_full_synthetic_run_writes_21_compatible_evidence_records(tmp_path: Path):
    sessions = {}

    def session_factory(mode: study.ModeDefinition, timeout: float):
        del timeout
        if mode.prepare_cookie:
            session = FakePreparedSession(mode, response_factory)
        else:
            session = FakeNoSession(mode, response_factory)
        sessions[mode.session_mode] = session
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

    assert manifest["summary"]["planned_request_count"] == 21
    assert manifest["summary"]["evidence_record_count"] == 21
    assert manifest["summary"]["http_response_count"] == 21
    assert manifest["summary"]["expected_top_level_found_count"] == 21
    assert len(manifest["requests"]) == 21

    resolved_path = run_dir / "study-definition.resolved.json"
    assert manifest["study_definition_file"] == "study-definition.resolved.json"
    assert manifest["study_definition_source_file"] == "config/studies/study-01-session-modes.json"
    assert resolved_path.exists()
    assert hashlib.sha256(resolved_path.read_bytes()).hexdigest() == manifest["study_definition_sha256"]
    resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
    assert len(resolved["requests"]) == 7
    assert len(resolved["modes"]) == 3
    assert all("{subject}" not in request["base_url"] for request in resolved["requests"])

    for request in manifest["requests"]:
        raw_path = run_dir / request["raw_response_file"]
        metadata_path = run_dir / request["metadata_file"]
        assert raw_path.exists()
        assert metadata_path.exists()
        assert hashlib.sha256(raw_path.read_bytes()).hexdigest() == request["raw_response_sha256"]
        assert len(raw_path.read_bytes()) == request["response_bytes"]
        assert request["canonical_json_sha256"] == study.sha256_json(json.loads(raw_path.read_bytes()))
        assert json.loads(metadata_path.read_text(encoding="utf-8")) == request

    mode_rows = read_csv(run_dir / "comparison" / "session-mode-results.csv")
    endpoint_rows = read_csv(run_dir / "comparison" / "endpoint-session-summary.csv")
    assert len(mode_rows) == 21
    assert len(endpoint_rows) == 7
    assert all(row["successful_mode_count"] == "3" for row in endpoint_rows)
    assert all(row["canonical_json_hashes_equal"] == "True" for row in endpoint_rows)

    saved_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in run_dir.rglob("*")
        if path.is_file() and path.suffix in {".json", ".csv", ".txt"}
    )
    assert "secret/crumb" not in saved_text
    assert "new-secret" not in saved_text


def test_canonical_json_hash_ignores_object_key_order():
    first = {"outer": {"a": 1, "b": 2}, "items": [{"x": 1, "y": 2}]}
    second = {"items": [{"y": 2, "x": 1}], "outer": {"b": 2, "a": 1}}

    assert json.dumps(first, separators=(",", ":")) != json.dumps(second, separators=(",", ":"))
    assert study.sha256_json(first) == study.sha256_json(second)


def test_no_session_401_is_evidence_without_auth_refresh():
    definition, modes, requests = study.load_study_definition(
        CONFIG_PATH,
        now=datetime(2026, 7, 19, tzinfo=timezone.utc),
    )
    plan = study.build_execution_plan(definition, modes, requests, run_id="test")
    planned = next(item for item in plan if item.mode.session_mode == "no-session")
    calls = []

    def factory(url: str, mode: study.ModeDefinition):
        del mode
        calls.append(url)
        headers = Message()
        headers["Content-Type"] = "application/json"
        raise HTTPError(url, 401, "Unauthorized", headers, None)

    session = FakeNoSession(planned.mode, factory)
    _, metadata = study.capture_one(
        planned,
        session,
        maximum_attempts=3,
        now=IncrementingNow(),
        clock=IncrementingClock(),
    )

    assert len(calls) == 1
    assert metadata["http_status"] == 401
    assert metadata["auth_refresh_performed"] is False
    assert metadata["attempt_count"] == 1
