from __future__ import annotations

import csv
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "tools" / "chart-parameter-study" / "run_chart_parameter_variation_study.py"
CONFIG_PATH = ROOT / "config" / "studies" / "study-05-chart-parameter-variation.json"


def load_tool():
    spec = importlib.util.spec_from_file_location("study05_tool", TOOL_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tool():
    return load_tool()


def test_default_definition_and_plan(tool):
    definition, endpoint, variants = tool.load_definition(CONFIG_PATH)
    assert definition["study_id"] == "study-05-chart-parameter-variation"
    assert endpoint["endpoint_id"] == "chart"
    assert len(variants) == 9
    assert variants[0].variant_id == "baseline-5d-1d"

    run_started = datetime(2026, 7, 30, 19, 0, tzinfo=timezone.utc)
    plan = tool.build_plan(
        definition,
        endpoint,
        variants,
        run_id="synthetic-study05",
        run_started=run_started,
    )
    assert len(plan) == 9
    assert [item.sequence for item in plan] == list(range(1, 10))
    assert plan[0].base_planned.request.params == {
        "range": "5d",
        "interval": "1d",
        "includePrePost": "false",
        "events": "div,splits,capitalGains",
    }


def test_explicit_period_is_resolved(tool):
    definition, endpoint, variants = tool.load_definition(CONFIG_PATH)
    run_started = datetime(2026, 7, 30, 19, 0, tzinfo=timezone.utc)
    plan = tool.build_plan(
        definition,
        endpoint,
        variants,
        run_id="synthetic-study05",
        run_started=run_started,
    )
    explicit = next(
        item for item in plan if item.variant.variant_id == "explicit-period-5d-1d"
    )
    params = explicit.base_planned.request.params
    assert "range" not in params
    assert int(params["period2"]) == int(run_started.timestamp())
    assert int(params["period2"]) - int(params["period1"]) == 5 * 24 * 60 * 60


def test_rejects_duplicate_variant_id(tool, tmp_path):
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    data["variants"].append(dict(data["variants"][0]))
    path = tmp_path / "duplicate.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(tool.StudyError, match="Duplicate variant_id"):
        tool.load_definition(path)


def test_rejects_baseline_not_first(tool, tmp_path):
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    data["variant_order"][0], data["variant_order"][1] = (
        data["variant_order"][1],
        data["variant_order"][0],
    )
    path = tmp_path / "baseline-order.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(tool.StudyError, match="baseline variant must be first"):
        tool.load_definition(path)


def synthetic_chart_body(*, timestamp_count: int = 3, include_event: bool = True) -> bytes:
    timestamps = [1_800_000_000 + index * 86_400 for index in range(timestamp_count)]
    quote = {
        "open": [100.0 + index for index in range(timestamp_count)],
        "high": [101.0 + index for index in range(timestamp_count)],
        "low": [99.0 + index for index in range(timestamp_count)],
        "close": [100.5 + index for index in range(timestamp_count)],
        "volume": [1000 + index for index in range(timestamp_count)],
    }
    events = (
        {
            "dividends": {
                str(timestamps[0]): {
                    "amount": 0.25,
                    "date": timestamps[0],
                }
            }
        }
        if include_event
        else {}
    )
    body = {
        "chart": {
            "result": [
                {
                    "meta": {
                        "currency": "USD",
                        "symbol": "AAPL",
                        "exchangeName": "NMS",
                        "instrumentType": "EQUITY",
                        "exchangeTimezoneName": "America/New_York",
                        "dataGranularity": "1d",
                        "range": "5d",
                        "currentTradingPeriod": {
                            "regular": {"start": timestamps[0], "end": timestamps[-1]}
                        },
                    },
                    "timestamp": timestamps,
                    "indicators": {
                        "quote": [quote],
                        "adjclose": [{"adjclose": [100.4 + index for index in range(timestamp_count)]}],
                    },
                    "events": events,
                }
            ],
            "error": None,
        }
    }
    return json.dumps(body, separators=(",", ":")).encode("utf-8")


def test_extract_chart_metrics(tool):
    metrics = tool.extract_chart_metrics(synthetic_chart_body(timestamp_count=4))
    assert metrics["chart_result_count"] == 1
    assert metrics["timestamp_count"] == 4
    assert metrics["quote_bar_count"] == 4
    assert metrics["adjclose_count"] == 4
    assert metrics["event_type_count"] == 1
    assert metrics["event_count"] == 1
    assert metrics["returned_instrument_type"] == "EQUITY"
    assert metrics["meta_field_count"] >= 7
    assert metrics["indicator_field_count"] == 6


def test_controlled_comparison(tool):
    baseline_body = synthetic_chart_body(timestamp_count=3)
    variant_body = synthetic_chart_body(timestamp_count=4, include_event=False)
    baseline_metrics = tool.extract_chart_metrics(baseline_body)
    variant_metrics = tool.extract_chart_metrics(variant_body)
    records = [
        {
            "metadata": {
                "variant_id": "baseline-5d-1d",
                "control_variant_id": "baseline-5d-1d",
                "canonical_json_sha256": tool.BASE.sha256_json(json.loads(baseline_body)),
                "response_bytes": len(baseline_body),
            },
            "metrics": baseline_metrics,
        },
        {
            "metadata": {
                "variant_id": "variant",
                "control_variant_id": "baseline-5d-1d",
                "canonical_json_sha256": tool.BASE.sha256_json(json.loads(variant_body)),
                "response_bytes": len(variant_body),
            },
            "metrics": variant_metrics,
        },
    ]
    rows = tool.build_controlled_comparisons(records)
    variant = rows[1]
    assert variant["timestamp_overlap_count"] == 3
    assert variant["timestamps_only_in_variant"] == 1
    assert variant["event_identity_set_equal"] is False
    assert variant["timestamp_count_delta"] == 1


class FakeSession:
    def public_summary(self):
        return {
            "session_strategy": "synthetic",
            "cookie_count": 1,
            "crumb_retrieved": True,
            "session_refresh_count": 0,
            "sensitive_values_persisted": False,
        }


def test_full_synthetic_run(tool, tmp_path):
    fixed_time = datetime(2026, 7, 30, 19, 0, tzinfo=timezone.utc)

    def fake_capture(planned, session, **kwargs):
        del session, kwargs
        count_by_interval = {"1d": 5, "1h": 35, "5m": 390, "1m": 1950}
        timestamp_count = count_by_interval[planned.request.params["interval"]]
        include_event = planned.request.params.get("events") not in {None, "div"}
        body = synthetic_chart_body(
            timestamp_count=timestamp_count,
            include_event=include_event,
        )
        parsed = json.loads(body)
        metadata = {
            "study_id": "study-01-session-mode-requirements",
            "study_version": "0.1.0",
            "study_variable": "session_mode",
            "study_condition": "cookie-crumb",
            "sequence": planned.sequence,
            "sample_id": planned.sample_id,
            "request_id": planned.request.request_id,
            "endpoint_id": planned.request.endpoint_id,
            "session_mode": planned.mode.session_mode,
            "method": planned.request.method,
            "request_parameters": dict(planned.request.params),
            "request_parameters_canonical": dict(planned.request.params),
            "request_parameters_sha256": planned.request_parameters_sha256,
            "request_url_redacted": "https://example.invalid/chart?crumb=REDACTED",
            "expected_top_level": "chart",
            "expected_top_level_found": True,
            "http_status": 200,
            "content_type": "application/json",
            "response_bytes": len(body),
            "raw_response_sha256": tool.BASE.sha256_bytes(body),
            "canonical_json_sha256": tool.BASE.sha256_json(parsed),
            "parse_status": "VALID_JSON",
            "parse_error": None,
            "result_classification": "EXPECTED_TOP_LEVEL_PRESENT",
            "error": None,
            "attempt_count": 1,
            "attempts": [],
            "auth_refresh_performed": False,
            "requested_at_utc": tool.format_utc(fixed_time),
            "response_received_at_utc": tool.format_utc(fixed_time),
            "elapsed_ms": 1,
            "session_strategy": "synthetic",
            "cookie_count": 1,
            "crumb_retrieved": True,
            "crumb_sent": True,
            "sensitive_values_persisted": False,
        }
        return body, metadata

    output_parent = tmp_path / "captures"
    run_dir, manifest = tool.run_study(
        definition_path=CONFIG_PATH,
        output_parent=output_parent,
        timeout=30.0,
        maximum_attempts=3,
        pause_ms=0,
        session_factory=lambda timeout: FakeSession(),
        capture_function=fake_capture,
        sleep=lambda seconds: None,
        now=lambda: fixed_time,
        clock=lambda: 1.0,
    )

    assert manifest["summary"]["evidence_record_count"] == 9
    assert manifest["summary"]["http_200_count"] == 9
    assert manifest["summary"]["chart_result_present_count"] == 9
    assert len(list((run_dir / "raw" / "chart").glob("*.raw.json"))) == 9
    assert len(list((run_dir / "metadata" / "chart").glob("*.meta.json"))) == 9

    with (run_dir / "comparison" / "chart-parameter-results.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        result_rows = list(csv.DictReader(handle))
    with (run_dir / "comparison" / "chart-controlled-comparison.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        comparison_rows = list(csv.DictReader(handle))
    assert len(result_rows) == 9
    assert len(comparison_rows) == 9
    assert result_rows[0]["variant_id"] == "baseline-5d-1d"
    assert comparison_rows[0]["is_control_identity"] == "True"


def test_dry_run_output(tool, capsys):
    tool.print_dry_run(
        CONFIG_PATH,
        now=datetime(2026, 7, 30, 19, 0, tzinfo=timezone.utc),
    )
    output = capsys.readouterr().out
    assert "Planned requests: 9" in output
    assert "baseline-5d-1d" in output
    assert "crumb=REDACTED" in output
