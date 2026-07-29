from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "tools" / "market-state-study" / "run_market_state_transition_study.py"
CONFIG_PATH = ROOT / "config" / "studies" / "study-04-market-state-transition.json"
ANALYZER_PATH = ROOT / "tools" / "endpoint-analysis" / "analyze_endpoint_captures.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("study04_tool", TOOL_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tool():
    return load_tool()


class FakeSession:
    def __init__(self, timeout: float):
        self.timeout = timeout

    def public_summary(self):
        return {
            "session_strategy": "synthetic",
            "cookie_count": 1,
            "crumb_retrieved": True,
            "session_refresh_count": 0,
            "sensitive_values_persisted": False,
        }


class StepNow:
    def __init__(self, start: datetime):
        self.value = start

    def __call__(self):
        current = self.value
        self.value += timedelta(seconds=1)
        return current


def state_for(symbol: str, round_index: int) -> str:
    if symbol == "BTC-USD":
        return "REGULAR"
    if symbol == "AAPL":
        return "PRE" if round_index == 1 else "REGULAR"
    if symbol == "RY.TO":
        return "POSTPOST" if round_index == 1 else "PREPRE"
    if symbol == "HSBA.L":
        return "PREPRE" if round_index == 1 else "REGULAR"
    if symbol == "BHP.AX":
        return "PRE" if round_index == 1 else "REGULAR"
    return "PREPRE" if round_index == 1 else "PRE"


def synthetic_capture(planned, session, *, maximum_attempts, sleep, now, clock):
    del session, maximum_attempts, sleep, clock
    match = re.search(r"_r(\d{4})_", planned.sample_id)
    assert match
    round_index = int(match.group(1))
    symbol = planned.subject.symbol
    state = state_for(symbol, round_index)
    record = {
        "symbol": symbol,
        "quoteType": planned.subject.expected_quote_type,
        "typeDisp": planned.subject.expected_quote_type,
        "exchange": "SYN",
        "fullExchangeName": "Synthetic",
        "currency": planned.subject.expected_currency,
        "exchangeTimezoneName": "UTC",
        "marketState": state,
        "market": "synthetic_market",
        "regularMarketPrice": 100.0 + round_index,
        "regularMarketTime": 1_800_000_000 + round_index,
    }
    if state == "PRE":
        record.update(
            {
                "preMarketPrice": 99.5,
                "preMarketTime": 1_800_000_001,
                "preMarketChange": -0.5,
                "preMarketChangePercent": -0.5,
            }
        )
    if state == "POST":
        record.update(
            {
                "postMarketPrice": 100.5,
                "postMarketTime": 1_800_000_002,
                "postMarketChange": 0.5,
                "postMarketChangePercent": 0.5,
            }
        )
    parsed = {"quoteResponse": {"result": [record], "error": None}}
    body = json.dumps(parsed, separators=(",", ":"), sort_keys=True).encode("utf-8")
    requested_at = now().astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    received_at = now().astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    metadata = {
        "study_id": "study-03-international-exchange-region",
        "study_version": "0.1.0",
        "study_variable": "listing_exchange_and_region",
        "study_condition": planned.subject.country_or_market,
        "sequence": planned.sequence,
        "sample_id": planned.sample_id,
        "request_id": f"quote-{planned.subject.subject_id}",
        "endpoint_id": planned.endpoint.endpoint_id,
        "request_subject": symbol,
        "requested_symbol": symbol,
        "requested_symbols": [symbol],
        "returned_symbols": [symbol],
        "geographic_region": planned.subject.geographic_region,
        "country_or_market": planned.subject.country_or_market,
        "yahoo_symbol_suffix": planned.subject.yahoo_symbol_suffix,
        "expected_quote_type": planned.subject.expected_quote_type,
        "expected_exchange_label": planned.subject.expected_exchange_label,
        "expected_currency": planned.subject.expected_currency,
        "subject_name": planned.subject.name,
        "subject_id": planned.subject.subject_id,
        "selection_role": planned.subject.selection_role,
        "source_inventory": planned.subject.source_inventory,
        "session_mode": "cookie-crumb",
        "method": planned.endpoint.method,
        "request_parameters": dict(planned.request_parameters),
        "request_parameters_canonical": dict(planned.request_parameters),
        "request_parameters_sha256": planned.request_parameters_sha256,
        "request_url_redacted": "https://query1.finance.yahoo.com/v7/finance/quote?symbols=" + symbol + "&crumb=REDACTED",
        "expected_top_level": planned.endpoint.expected_top_level,
        "expected_top_level_found": True,
        "requested_symbol_returned": True,
        "returned_record_count": 1,
        "returned_symbol": symbol,
        "returned_quote_type": planned.subject.expected_quote_type,
        "quote_type_match": True,
        "selected_quote_fields": {
            key: record.get(key)
            for key in (
                "symbol",
                "quoteType",
                "typeDisp",
                "exchange",
                "fullExchangeName",
                "currency",
                "exchangeTimezoneName",
                "marketState",
                "market",
                "regularMarketPrice",
                "regularMarketTime",
            )
        },
        "returned_field_count": len(record),
        "quote_response_error": None,
        "http_status": 200,
        "content_type": "application/json",
        "response_bytes": len(body),
        "raw_response_sha256": hashlib.sha256(body).hexdigest(),
        "canonical_json_sha256": hashlib.sha256(
            json.dumps(parsed, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "parse_status": "VALID_JSON",
        "parse_error": None,
        "result_classification": "EXPECTED_SYMBOL_RETURNED",
        "error": None,
        "attempt_count": 1,
        "attempts": [
            {
                "attempt": 1,
                "requested_at_utc": requested_at,
                "response_received_at_utc": received_at,
                "elapsed_ms": 1,
                "http_status": 200,
                "error": None,
            }
        ],
        "auth_refresh_performed": False,
        "requested_at_utc": requested_at,
        "response_received_at_utc": received_at,
        "elapsed_ms": 1,
        "session_strategy": "synthetic",
        "cookie_count": 1,
        "crumb_retrieved": True,
        "crumb_sent": True,
        "sensitive_values_persisted": False,
    }
    return body, metadata


def run_synthetic(tool, tmp_path: Path):
    now = StepNow(datetime(2026, 7, 29, 0, 0, tzinfo=timezone.utc))
    return tool.run_study(
        definition_path=CONFIG_PATH,
        output_parent=tmp_path,
        timeout=1.0,
        maximum_attempts=1,
        pause_ms=0,
        duration_hours=26,
        interval_minutes=15,
        maximum_rounds=2,
        session_factory=FakeSession,
        capture_function=synthetic_capture,
        sleep=lambda _: None,
        now=now,
        monotonic=lambda: 0.0,
    )


def test_default_definition_and_plan_counts(tool):
    definition, endpoint, subjects, sampling = tool.load_definition(CONFIG_PATH)
    assert definition["study_id"] == "study-04-market-state-transition"
    assert endpoint.endpoint_id == "quote"
    assert len(subjects) == 13
    assert subjects[-1].symbol == "BTC-USD"
    assert tool.calculate_round_count(sampling.duration_hours, sampling.interval_minutes) == 105
    assert 105 * len(subjects) == 1365


def test_rejects_nonpositive_sampling(tool, tmp_path):
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    data["sampling"]["interval_minutes"] = 0
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(tool.StudyError, match="interval_minutes"):
        tool.load_definition(path)


def test_rejects_duplicate_symbol(tool, tmp_path):
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    data["subjects"][1]["symbol"] = data["subjects"][0]["symbol"]
    path = tmp_path / "duplicate.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(tool.StudyError, match="Duplicate symbol"):
        tool.load_definition(path)


def test_dry_run_reports_rounds_and_requests(tool, capsys):
    tool.print_dry_run(
        CONFIG_PATH,
        maximum_rounds=2,
        now=datetime(2026, 7, 29, tzinfo=timezone.utc),
    )
    output = capsys.readouterr().out
    assert "Subjects: 13" in output
    assert "Planned rounds: 2" in output
    assert "Planned requests: 26" in output
    assert "PREPRE" not in output
    assert "BTC-USD" in output


def test_base_url_redaction_preserves_no_secret(tool):
    url = tool.BASE.build_url(
        "https://query1.finance.yahoo.com/v7/finance/quote",
        {"symbols": "AAPL", "formatted": "false"},
        "secret-value",
    )
    redacted = tool.BASE.redact_url(url)
    assert "secret-value" not in redacted
    assert "crumb=REDACTED" in redacted


def test_transition_builder_preserves_exact_duplicate_tokens(tool):
    rows = [
        {
            "requested_symbol": "RY.TO",
            "subject_name": "Royal Bank",
            "country_or_market": "Canada",
            "round_index": 1,
            "sequence": 1,
            "requested_at_utc": "2026-07-29T00:00:00.000Z",
            "selected_quote_fields": {"marketState": "POSTPOST"},
            "pre_market_fields_present": False,
            "post_market_fields_present": False,
        },
        {
            "requested_symbol": "RY.TO",
            "subject_name": "Royal Bank",
            "country_or_market": "Canada",
            "round_index": 2,
            "sequence": 2,
            "requested_at_utc": "2026-07-29T00:15:00.000Z",
            "selected_quote_fields": {"marketState": "PREPRE"},
            "pre_market_fields_present": False,
            "post_market_fields_present": False,
        },
    ]
    transitions = tool.build_transitions(rows)
    assert len(transitions) == 1
    assert transitions[0]["from_market_state"] == "POSTPOST"
    assert transitions[0]["to_market_state"] == "PREPRE"


def test_enrich_metadata_detects_extended_fields(tool):
    definition, endpoint, subjects, _ = tool.load_definition(CONFIG_PATH)
    plan = tool.build_plan(
        endpoint,
        subjects,
        run_id="synthetic",
        run_started=datetime(2026, 7, 29, tzinfo=timezone.utc),
        round_count=1,
        interval_minutes=15,
    )
    planned = plan[0]
    metadata = {
        "requested_at_utc": "2026-07-29T00:00:00.000Z",
        "selected_quote_fields": {"marketState": "PRE"},
    }
    enriched = tool.enrich_metadata(
        metadata,
        planned,
        raw_record={"preMarketPrice": 1.0, "preMarketTime": 2},
        round_started_at_utc="2026-07-29T00:00:00.000Z",
    )
    assert enriched["pre_market_fields_present"] is True
    assert enriched["pre_market_field_count"] == 2
    assert enriched["post_market_fields_present"] is False


def test_full_synthetic_run_writes_complete_outputs(tool, tmp_path):
    run_dir, manifest = run_synthetic(tool, tmp_path)
    assert manifest["run_status"] == "completed"
    assert manifest["planned_round_count"] == 2
    assert manifest["planned_request_count"] == 26
    assert manifest["summary"]["evidence_record_count"] == 26
    assert manifest["summary"]["fully_completed_round_count"] == 2
    assert set(manifest["summary"]["observed_market_state_values"]) >= {
        "PREPRE",
        "PRE",
        "REGULAR",
        "POSTPOST",
    }
    assert len(list((run_dir / "raw").glob("*.raw.json"))) == 26
    assert len(list((run_dir / "metadata").glob("*.meta.json"))) == 26
    with (run_dir / "comparison" / "market-state-observations.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        assert len(list(csv.DictReader(handle))) == 26
    with (run_dir / "comparison" / "market-state-transitions.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        transitions = list(csv.DictReader(handle))
    assert any(row["from_market_state"] == "POSTPOST" for row in transitions)


def test_manifest_and_sidecars_contain_no_session_secrets(tool, tmp_path):
    run_dir, manifest = run_synthetic(tool, tmp_path)
    text = (run_dir / "run-manifest.json").read_text(encoding="utf-8").lower()
    assert "secret-value" not in text
    assert '"sensitive_values_persisted": false' in text
    assert manifest["authentication"]["sensitive_values_persisted"] is False
    for path in (run_dir / "metadata").glob("*.meta.json"):
        content = path.read_text(encoding="utf-8")
        assert "crumb=REDACTED" in content
        assert "secret-value" not in content


def test_existing_analyzer_processes_synthetic_run(tool, tmp_path):
    run_dir, _ = run_synthetic(tool, tmp_path)
    output_dir = tmp_path / "analysis"
    result = subprocess.run(
        [
            sys.executable,
            str(ANALYZER_PATH),
            str(run_dir),
            "--output-dir",
            str(output_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    validation = json.loads((output_dir / "validation.json").read_text(encoding="utf-8"))
    assert validation["summary"]["sample_count"] == 26
    assert validation["summary"]["type_conflict_count"] == 0
