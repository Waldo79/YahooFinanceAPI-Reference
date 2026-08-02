from __future__ import annotations

import csv
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "tools" / "day-change-study" / "run_day_change_validity_study.py"
CONFIG_PATH = ROOT / "config" / "studies" / "study-07-day-change-validity.json"


def load_tool():
    spec = importlib.util.spec_from_file_location("study07_tool", TOOL_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tool():
    return load_tool()


def epoch(text: str) -> int:
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())


def quote_record(
    *,
    symbol: str = "AAPL",
    price: float = 100.0,
    previous: float = 100.0,
    reported_change: float | None = None,
    reported_percent: float | None = None,
    regular_time: int | None = None,
    market_state: str = "REGULAR",
    volume: int | None = 1000,
):
    change = price - previous if reported_change is None else reported_change
    percent = change / previous * 100 if reported_percent is None else reported_percent
    return {
        "symbol": symbol,
        "quoteType": "EQUITY",
        "exchange": "NMS",
        "exchangeTimezoneName": "America/New_York",
        "gmtOffSetMilliseconds": -4 * 60 * 60 * 1000,
        "marketState": market_state,
        "regularMarketPrice": price,
        "regularMarketPreviousClose": previous,
        "regularMarketChange": change,
        "regularMarketChangePercent": percent,
        "regularMarketTime": regular_time or epoch("2026-08-03T18:59:00Z"),
        "regularMarketVolume": volume,
    }


def quote_body(record: dict) -> bytes:
    return json.dumps(
        {"quoteResponse": {"result": [record], "error": None}},
        separators=(",", ":"),
    ).encode()


def chart_body(
    *,
    symbol: str = "AAPL",
    previous_close: float = 100.0,
    current_close: float = 100.0,
) -> bytes:
    timestamps = [epoch("2026-07-31T13:30:00Z"), epoch("2026-08-03T13:30:00Z")]
    payload = {
        "chart": {
            "result": [
                {
                    "meta": {
                        "symbol": symbol,
                        "gmtoffset": -4 * 60 * 60,
                        "dataGranularity": "1d",
                        "range": "5d",
                    },
                    "timestamp": timestamps,
                    "indicators": {
                        "quote": [
                            {
                                "open": [previous_close, current_close],
                                "high": [previous_close, current_close],
                                "low": [previous_close, current_close],
                                "close": [previous_close, current_close],
                                "volume": [1000, 1000],
                            }
                        ],
                        "adjclose": [{"adjclose": [previous_close, current_close]}],
                    },
                }
            ],
            "error": None,
        }
    }
    return json.dumps(payload, separators=(",", ":")).encode()


def tolerances(tool):
    definition, *_ = tool.load_definition(CONFIG_PATH)
    return definition["numeric_tolerances"]


def test_default_definition_uses_24_subjects_and_48_requests(tool):
    definition, _, _, _, subjects, chart_endpoint = tool.load_definition(CONFIG_PATH)
    assert definition["study_id"] == "study-07-day-change-validity"
    assert len(subjects) == 24
    assert definition["expected_request_count"] == 48
    assert chart_endpoint["params"]["interval"] == "1d"


def test_near_zero_equal_prices_is_control(tool):
    result = tool.evaluate_day_change(
        quote_record=quote_record(),
        chart_body=chart_body(),
        capture_at=datetime(2026, 8, 3, 19, 0, tzinfo=timezone.utc),
        project_security_type="Common Stock",
        tolerances=tolerances(tool),
        required_volume_types={"Common Stock"},
    )
    assert result["validity_classification"] == "NEAR_ZERO_PRICE_EQUAL"
    assert result["display_recommendation"] == "PERCENT"


def test_near_zero_unequal_prices_is_exception(tool):
    result = tool.evaluate_day_change(
        quote_record=quote_record(price=100.0005, previous=100.0),
        chart_body=chart_body(previous_close=100.0, current_close=100.0005),
        capture_at=datetime(2026, 8, 3, 19, 0, tzinfo=timezone.utc),
        project_security_type="Common Stock",
        tolerances=tolerances(tool),
        required_volume_types={"Common Stock"},
    )
    assert 0 < result["calculated_percent"] <= 0.001
    assert result["reported_percent_in_near_zero_band"] is True
    assert result["current_price_equals_previous_close"] is False
    assert result["near_zero_exception"] is True
    assert result["near_zero_exception_cause"] == "SMALL_NONZERO_PRICE_MOVE"
    assert result["validity_classification"] == "NEAR_ZERO_PRICE_UNEQUAL_EXCEPTION"
    assert result["display_recommendation"] == "REVIEW"


def test_outside_near_zero_band_is_control(tool):
    result = tool.evaluate_day_change(
        quote_record=quote_record(price=100.01, previous=100.0),
        chart_body=chart_body(previous_close=100.0, current_close=100.01),
        capture_at=datetime(2026, 8, 3, 19, 0, tzinfo=timezone.utc),
        project_security_type="Common Stock",
        tolerances=tolerances(tool),
        required_volume_types={"Common Stock"},
    )
    assert result["reported_percent_in_near_zero_band"] is False
    assert result["validity_classification"] == "OUTSIDE_NEAR_ZERO_BAND"
    assert result["display_recommendation"] == "PERCENT"


def test_weekend_value_is_not_current(tool):
    result = tool.evaluate_day_change(
        quote_record=quote_record(
            regular_time=epoch("2026-07-31T19:59:00Z"), market_state="CLOSED"
        ),
        chart_body=chart_body(),
        capture_at=datetime(2026, 8, 2, 22, 0, tzinfo=timezone.utc),
        project_security_type="Common Stock",
        tolerances=tolerances(tool),
        required_volume_types={"Common Stock"},
    )
    assert result["validity_classification"] == "NOT_CURRENT_SESSION"
    assert result["display_recommendation"] == "N/A"


def test_previous_close_mismatch_is_detected(tool):
    result = tool.evaluate_day_change(
        quote_record=quote_record(previous=99.0, price=100.0),
        chart_body=chart_body(previous_close=100.0, current_close=100.0),
        capture_at=datetime(2026, 8, 3, 19, 0, tzinfo=timezone.utc),
        project_security_type="Common Stock",
        tolerances=tolerances(tool),
        required_volume_types={"Common Stock"},
    )
    assert result["validity_classification"] == "PREVIOUS_CLOSE_MISMATCH"


def test_volume_gate_only_applies_to_configured_regular_types(tool):
    common = tool.evaluate_day_change(
        quote_record=quote_record(volume=0),
        chart_body=chart_body(),
        capture_at=datetime(2026, 8, 3, 19, 0, tzinfo=timezone.utc),
        project_security_type="Common Stock",
        tolerances=tolerances(tool),
        required_volume_types={"Common Stock"},
    )
    mutual = tool.evaluate_day_change(
        quote_record=quote_record(volume=0),
        chart_body=chart_body(),
        capture_at=datetime(2026, 8, 3, 19, 0, tzinfo=timezone.utc),
        project_security_type="Mutual Fund",
        tolerances=tolerances(tool),
        required_volume_types={"Common Stock"},
    )
    assert common["validity_classification"] == "VOLUME_NOT_CONFIRMED"
    assert mutual["validity_classification"] == "NEAR_ZERO_PRICE_EQUAL"


def test_chart_reference_uses_preceding_observed_bar(tool):
    reference = tool.daily_close_reference(
        chart_body(previous_close=98.5, current_close=101.0),
        target_date=datetime(2026, 8, 3).date(),
        fallback_offset_seconds=-4 * 60 * 60,
    )
    assert reference["matching_session_date"] == "2026-08-03"
    assert reference["previous_session_date"] == "2026-07-31"
    assert reference["previous_session_close"] == 98.5


class FakeSession:
    def public_summary(self):
        return {
            "session_strategy": "synthetic",
            "cookie_count": 1,
            "crumb_retrieved": True,
            "session_refresh_count": 0,
            "sensitive_values_persisted": False,
        }


def test_full_synthetic_run_writes_48_analyzer_compatible_records(tool, tmp_path):
    fixed_time = datetime(2026, 8, 3, 19, 0, tzinfo=timezone.utc)

    def fake_quote_capture(planned, session, **kwargs):
        del session, kwargs
        body = quote_body(quote_record(symbol=planned.subject.symbol))
        parsed = json.loads(body)
        return body, {
            "sequence": planned.sequence,
            "sample_id": planned.sample_id,
            "request_id": f"quote-{planned.subject.subject_id}",
            "endpoint_id": "quote",
            "request_subject": planned.subject.symbol,
            "requested_symbol": planned.subject.symbol,
            "requested_symbols": [planned.subject.symbol],
            "returned_symbols": [planned.subject.symbol],
            "project_security_type": planned.subject.project_security_type,
            "expected_quote_type": planned.subject.expected_quote_type,
            "expected_exchange": planned.subject.expected_exchange,
            "subject_name": planned.subject.name,
            "subject_id": planned.subject.subject_id,
            "pair_id": planned.subject.pair_id,
            "representative_role": planned.subject.representative_role,
            "session_mode": "cookie-crumb",
            "method": "GET",
            "request_parameters": dict(planned.request_parameters),
            "request_parameters_canonical": dict(planned.request_parameters),
            "request_parameters_sha256": planned.request_parameters_sha256,
            "request_url_redacted": "https://example.invalid/quote?crumb=REDACTED",
            "expected_top_level": "quoteResponse",
            "expected_top_level_found": True,
            "requested_symbol_returned": True,
            "http_status": 200,
            "content_type": "application/json",
            "response_bytes": len(body),
            "raw_response_sha256": tool.QUOTE.sha256_bytes(body),
            "canonical_json_sha256": tool.QUOTE.sha256_json(parsed),
            "parse_status": "VALID_JSON",
            "result_classification": "EXPECTED_SYMBOL_RETURNED",
            "error": None,
            "attempt_count": 1,
            "attempts": [],
            "auth_refresh_performed": False,
            "requested_at_utc": tool.format_utc(fixed_time),
            "response_received_at_utc": tool.format_utc(fixed_time),
            "elapsed_ms": 1,
            "sensitive_values_persisted": False,
        }

    def fake_chart_capture(planned, session, **kwargs):
        del session, kwargs
        symbol = planned.request.base_url.rstrip("/").rsplit("/", 1)[-1]
        body = chart_body(symbol=symbol)
        parsed = json.loads(body)
        return body, {
            "sequence": planned.sequence,
            "sample_id": planned.sample_id,
            "request_id": planned.request.request_id,
            "endpoint_id": "chart",
            "session_mode": "cookie-crumb",
            "method": "GET",
            "request_parameters": dict(planned.request.params),
            "request_parameters_canonical": dict(planned.request.params),
            "request_parameters_sha256": planned.request_parameters_sha256,
            "request_url_redacted": "https://example.invalid/chart?crumb=REDACTED",
            "expected_top_level": "chart",
            "expected_top_level_found": True,
            "http_status": 200,
            "content_type": "application/json",
            "response_bytes": len(body),
            "raw_response_sha256": tool.QUOTE.sha256_bytes(body),
            "canonical_json_sha256": tool.QUOTE.sha256_json(parsed),
            "parse_status": "VALID_JSON",
            "result_classification": "EXPECTED_TOP_LEVEL_PRESENT",
            "error": None,
            "attempt_count": 1,
            "attempts": [],
            "auth_refresh_performed": False,
            "requested_at_utc": tool.format_utc(fixed_time),
            "response_received_at_utc": tool.format_utc(fixed_time),
            "elapsed_ms": 1,
            "sensitive_values_persisted": False,
        }

    run_dir, manifest = tool.run_study(
        definition_path=CONFIG_PATH,
        output_parent=tmp_path / "captures",
        label="synthetic",
        timeout=30.0,
        maximum_attempts=3,
        pause_ms=0,
        quote_session_factory=lambda timeout: FakeSession(),
        chart_session_factory=lambda timeout: FakeSession(),
        quote_capture=fake_quote_capture,
        chart_capture=fake_chart_capture,
        sleep=lambda seconds: None,
        now=lambda: fixed_time,
        clock=lambda: 1.0,
    )
    assert manifest["summary"]["evidence_record_count"] == 48
    assert manifest["summary"]["publishable_percent_count"] == 24
    assert len(list((run_dir / "raw" / "quote").glob("*.raw.json"))) == 24
    assert len(list((run_dir / "raw" / "chart").glob("*.raw.json"))) == 24
    for entry in manifest["requests"]:
        sidecar = json.loads((run_dir / entry["metadata_file"]).read_text())
        assert entry == sidecar
    with (run_dir / "comparison" / "day-change-validity.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 24
    assert {row["validity_classification"] for row in rows} == {"NEAR_ZERO_PRICE_EQUAL"}
