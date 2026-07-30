from __future__ import annotations

import csv
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = (
    ROOT
    / "tools"
    / "chart-stability-study"
    / "run_chart_repeated_day_stability_study.py"
)
CONFIG_PATH = (
    ROOT
    / "config"
    / "studies"
    / "study-06-chart-repeated-day-stability.json"
)


def load_tool():
    spec = importlib.util.spec_from_file_location("study06_tool", TOOL_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tool():
    return load_tool()


def test_default_definition_and_schedule(tool):
    definition, schedule, _, source, endpoint, variants = tool.load_definition(CONFIG_PATH)
    assert definition["study_id"] == "study-06-chart-repeated-day-stability"
    assert source["study_id"] == "study-05-chart-parameter-variation"
    assert endpoint["endpoint_id"] == "chart"
    assert len(variants) == 9
    assert len(schedule.rounds) == 3
    assert [item.scheduled_date for item in schedule.rounds] == [
        "2026-07-31",
        "2026-08-03",
        "2026-08-04",
    ]


def test_target_time_conversions(tool):
    _, schedule, _, _, _, _ = tool.load_definition(CONFIG_PATH)
    first = schedule.rounds[0]
    local = tool.target_local_datetime(schedule, first)
    utc = tool.target_utc_datetime(schedule, first)
    pacific = utc.astimezone(timezone(timedelta(hours=-7)))
    assert local.isoformat() == "2026-07-31T15:45:00-04:00"
    assert tool.format_utc(utc) == "2026-07-31T19:45:00.000Z"
    assert pacific.isoformat() == "2026-07-31T12:45:00-07:00"


def test_schedule_decisions(tool):
    _, schedule, _, _, _, _ = tool.load_definition(CONFIG_PATH)
    first = schedule.rounds[0]
    target = tool.target_utc_datetime(schedule, first)

    with pytest.raises(tool.StudyError, match="Start the tool on that date"):
        tool.schedule_decision(
            schedule,
            first,
            now_utc=datetime(2026, 7, 30, 20, 0, tzinfo=timezone.utc),
            run_now=False,
        )

    planned, mode = tool.schedule_decision(
        schedule,
        first,
        now_utc=target - timedelta(minutes=5),
        run_now=False,
    )
    assert planned == target
    assert mode == "scheduled-wait"

    planned, mode = tool.schedule_decision(
        schedule,
        first,
        now_utc=target + timedelta(minutes=2),
        run_now=False,
    )
    assert planned == target
    assert mode == "same-day-late"


def test_wait_for_target(tool):
    state = {"now": datetime(2026, 7, 31, 19, 44, 0, tzinfo=timezone.utc)}
    sleeps = []

    def fake_now():
        return state["now"]

    def fake_sleep(seconds):
        sleeps.append(seconds)
        state["now"] += timedelta(seconds=seconds)

    tool.wait_for_target(
        datetime(2026, 7, 31, 19, 45, 0, tzinfo=timezone.utc),
        now=fake_now,
        sleep=fake_sleep,
    )
    assert sum(sleeps) == 60
    assert max(sleeps) <= 30


def synthetic_chart_body(*, day_offset: int, interval: str, prepost: bool) -> bytes:
    counts = {"1d": 5, "1h": 36, "5m": 390, "1m": 1950}
    count = counts[interval] + (522 if interval == "5m" and prepost else 0)
    step = {"1d": 86400, "1h": 3600, "5m": 300, "1m": 60}[interval]
    start = 1_800_000_000 + day_offset * 86400
    timestamps = [start + index * step for index in range(count)]
    quote = {
        "open": [100.0 + day_offset + index / 1000 for index in range(count)],
        "high": [101.0 + day_offset + index / 1000 for index in range(count)],
        "low": [99.0 + day_offset + index / 1000 for index in range(count)],
        "close": [100.5 + day_offset + index / 1000 for index in range(count)],
        "volume": [1000 + day_offset + index for index in range(count)],
    }
    indicators = {"quote": [quote]}
    if interval == "1d":
        indicators["adjclose"] = [
            {"adjclose": [100.4 + day_offset + index / 1000 for index in range(count)]}
        ]
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
                        "dataGranularity": interval,
                        "range": "5d",
                    },
                    "timestamp": timestamps,
                    "indicators": indicators,
                    "events": {},
                }
            ],
            "error": None,
        }
    }
    return json.dumps(body, separators=(",", ":")).encode("utf-8")


class FakeSession:
    def public_summary(self):
        return {
            "session_strategy": "synthetic",
            "cookie_count": 1,
            "crumb_retrieved": True,
            "session_refresh_count": 0,
            "sensitive_values_persisted": False,
        }


def make_capture(tool, *, day_offset: int, fixed_time: datetime):
    def fake_capture(planned, session, **kwargs):
        del session, kwargs
        params = planned.request.params
        interval = params["interval"]
        prepost = params.get("includePrePost") == "true"
        body = synthetic_chart_body(
            day_offset=day_offset,
            interval=interval,
            prepost=prepost,
        )
        parsed = json.loads(body)
        return body, {
            "sequence": planned.sequence,
            "sample_id": planned.sample_id,
            "request_id": planned.request.params.get("interval"),
            "endpoint_id": "chart",
            "session_mode": "cookie-crumb",
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

    return fake_capture


def run_synthetic_round(tool, output_parent, *, day_offset, fixed_time):
    return tool.run_one_invocation(
        definition_path=CONFIG_PATH,
        output_parent=output_parent,
        timeout=30.0,
        maximum_attempts=3,
        pause_ms=0,
        run_now=True,
        session_factory=lambda timeout: FakeSession(),
        capture_function=make_capture(
            tool, day_offset=day_offset, fixed_time=fixed_time
        ),
        sleep=lambda seconds: None,
        now=lambda: fixed_time,
        clock=lambda: 1.0,
    )


def test_first_round_initializes_and_exits(tool, tmp_path):
    series_dir, manifest, round_manifest = run_synthetic_round(
        tool,
        tmp_path / "captures",
        day_offset=0,
        fixed_time=datetime(2026, 7, 31, 19, 45, tzinfo=timezone.utc),
    )
    assert round_manifest is not None
    assert round_manifest["round_id"] == "day-01"
    assert manifest["run_status"] == "in_progress"
    assert manifest["summary"]["completed_round_count"] == 1
    assert manifest["summary"]["evidence_record_count"] == 9
    round_dirs = list((series_dir / "rounds").glob("day-01_*"))
    assert len(round_dirs) == 1
    assert (round_dirs[0] / "run-manifest.json").is_file()

    with (series_dir / "comparison" / "chart-day-results.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        result_rows = list(csv.DictReader(handle))
    with (series_dir / "comparison" / "chart-day-stability.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        stability_rows = list(csv.DictReader(handle))
    assert len(result_rows) == 9
    assert len(stability_rows) == 9
    assert {row["control_round_id"] for row in stability_rows} == {"day-01"}


def test_three_separate_invocations_complete_series(tool, tmp_path):
    output_parent = tmp_path / "captures"
    times = [
        datetime(2026, 7, 31, 19, 45, tzinfo=timezone.utc),
        datetime(2026, 8, 3, 19, 45, tzinfo=timezone.utc),
        datetime(2026, 8, 4, 19, 45, tzinfo=timezone.utc),
    ]
    final = None
    for day_offset, fixed_time in enumerate(times):
        final = run_synthetic_round(
            tool,
            output_parent,
            day_offset=day_offset,
            fixed_time=fixed_time,
        )
    assert final is not None
    series_dir, manifest, round_manifest = final
    assert round_manifest is not None
    assert round_manifest["round_id"] == "day-03"
    assert manifest["run_status"] == "completed"
    assert manifest["summary"]["completed_round_count"] == 3
    assert manifest["summary"]["evidence_record_count"] == 27
    assert manifest["summary"]["http_200_count"] == 27
    assert manifest["summary"]["all_evidence_records_written"] is True

    with (series_dir / "comparison" / "chart-day-results.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        result_rows = list(csv.DictReader(handle))
    with (series_dir / "comparison" / "chart-day-stability.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        stability_rows = list(csv.DictReader(handle))
    assert len(result_rows) == 27
    assert len(stability_rows) == 27
    assert {row["round_id"] for row in stability_rows} == {
        "day-01",
        "day-02",
        "day-03",
    }

    series_dir2, manifest2, round_manifest2 = run_synthetic_round(
        tool,
        output_parent,
        day_offset=3,
        fixed_time=datetime(2026, 8, 5, 19, 45, tzinfo=timezone.utc),
    )
    assert series_dir2 == series_dir
    assert manifest2["run_status"] == "completed"
    assert round_manifest2 is None


def test_dry_run_output(tool, capsys, tmp_path):
    definition, schedule, _, _, _, variants = tool.load_definition(CONFIG_PATH)
    tool.print_schedule(
        definition,
        schedule,
        variants,
        output_parent=tmp_path / "captures",
    )
    output = capsys.readouterr().out
    assert "Planned requests: 27" in output
    assert "Friday, 2026-07-31 15:45:00" in output
    assert "Friday, 2026-07-31 12:45:00" in output
    assert "day-03" in output


def test_rejects_weekend_schedule(tool, tmp_path):
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    data["schedule"]["rounds"][0]["scheduled_date"] = "2026-08-01"
    path = tmp_path / "weekend.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(tool.StudyError, match="weekend"):
        tool.load_definition(path)
