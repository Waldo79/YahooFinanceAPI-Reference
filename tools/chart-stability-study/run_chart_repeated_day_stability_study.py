#!/usr/bin/env python3
r"""Run Study 06: repeated-day stability for the validated Study 05 Chart variants.

Run from repository root on each scheduled date before the target time:

    py tools\chart-stability-study\run_chart_repeated_day_stability_study.py --dry-run
    py tools\chart-stability-study\run_chart_repeated_day_stability_study.py

Each normal invocation waits only for the target time on the current scheduled date,
captures one nine-request round, updates the cross-day comparison tables, and exits.
The computer does not need to remain running between scheduled dates.

The script reuses the validated Study 05 Chart implementation and the Study 01
cookie-and-crumb session implementation. Raw response bytes are never modified, and
cookie or crumb values are never written to evidence.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as wall_time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

TOOL_VERSION = "0.1.0"
STUDY_SCHEMA_VERSION = "0.5.0"

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = (
    REPOSITORY_ROOT
    / "config"
    / "studies"
    / "study-06-chart-repeated-day-stability.json"
)
DEFAULT_OUTDIR = REPOSITORY_ROOT / "captures" / "local"
STUDY05_TOOL_PATH = (
    REPOSITORY_ROOT
    / "tools"
    / "chart-parameter-study"
    / "run_chart_parameter_variation_study.py"
)

DAY_RESULT_PREFIX_COLUMNS = [
    "round_index",
    "round_id",
    "scheduled_date",
    "scheduled_local_time",
    "scheduled_at_utc",
    "round_started_at_utc",
    "round_completed_at_utc",
    "schedule_offset_seconds",
    "schedule_override",
    "round_folder",
]

DAY_STABILITY_COLUMNS = [
    "variant_id",
    "round_index",
    "round_id",
    "control_round_index",
    "control_round_id",
    "scheduled_date",
    "control_scheduled_date",
    "request_parameters_equal",
    "timestamp_sequence_equal",
    "timestamp_overlap_count",
    "timestamps_only_in_round",
    "timestamps_only_in_control",
    "meta_key_set_equal",
    "indicator_key_set_equal",
    "event_identity_set_equal",
    "canonical_json_equal",
    "raw_response_equal",
    "response_bytes_delta",
    "timestamp_count_delta",
    "quote_bar_count_delta",
    "adjclose_count_delta",
    "null_indicator_value_count_delta",
    "event_count_delta",
]

SERIES_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class StudyError(RuntimeError):
    """Raised when the study definition, schedule, or capture state is invalid."""


@dataclass(frozen=True)
class RoundDefinition:
    round_index: int
    round_id: str
    scheduled_date: str
    label: str


@dataclass(frozen=True)
class ScheduleDefinition:
    timezone_name: str
    utc_offset: str
    local_time: str
    rounds: list[RoundDefinition]


def load_study05_tool() -> Any:
    if not STUDY05_TOOL_PATH.exists():
        raise StudyError(
            "Study 06 requires "
            "tools/chart-parameter-study/run_chart_parameter_variation_study.py"
        )
    spec = importlib.util.spec_from_file_location("study05_chart_base", STUDY05_TOOL_PATH)
    if spec is None or spec.loader is None:
        raise StudyError(f"Could not load Study 05 Chart tool: {STUDY05_TOOL_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


STUDY05 = load_study05_tool()
BASE = STUDY05.BASE


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def write_json(path: Path, value: Any) -> None:
    BASE.write_json(path, value)


def write_csv(path: Path, columns: list[str], rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="raise")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})
            count += 1
    return count


def parse_utc_offset(value: str) -> timezone:
    match = re.fullmatch(r"([+-])(\d{2}):(\d{2})", value)
    if not match:
        raise StudyError("schedule.utc_offset must use the form +HH:MM or -HH:MM.")
    sign = 1 if match.group(1) == "+" else -1
    hours = int(match.group(2))
    minutes = int(match.group(3))
    if hours > 23 or minutes > 59:
        raise StudyError("schedule.utc_offset contains an invalid hour or minute.")
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


def parse_local_time(value: str) -> wall_time:
    try:
        parsed = wall_time.fromisoformat(value)
    except ValueError as exc:
        raise StudyError("schedule.local_time must be an ISO time such as 15:45:00.") from exc
    if parsed.tzinfo is not None:
        raise StudyError("schedule.local_time must not contain a time-zone offset.")
    if parsed.microsecond:
        raise StudyError("schedule.local_time must not contain fractional seconds.")
    return parsed


def parse_scheduled_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise StudyError(f"Invalid scheduled date: {value}") from exc


def load_definition(
    path: Path,
) -> tuple[dict[str, Any], ScheduleDefinition, Path, dict[str, Any], dict[str, str], list[Any]]:
    try:
        definition = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise StudyError(f"Could not read study definition {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise StudyError(f"Study definition is not valid JSON: {exc}") from exc

    if definition.get("study_id") != "study-06-chart-repeated-day-stability":
        raise StudyError("Study 06 definition has the wrong study_id.")
    if definition.get("session_mode") != "cookie-crumb":
        raise StudyError("Study 06 requires session_mode=cookie-crumb.")

    series_id = str(definition.get("series_id") or "").strip()
    if not SERIES_ID_PATTERN.fullmatch(series_id):
        raise StudyError(
            "series_id must contain only letters, numbers, periods, underscores, and hyphens."
        )

    source_relative = str(definition.get("source_study_definition") or "").strip()
    if not source_relative:
        raise StudyError("source_study_definition is required.")
    source_path = REPOSITORY_ROOT / source_relative
    if not source_path.is_file():
        raise StudyError(f"Source Study 05 definition was not found: {source_path}")

    raw_schedule = definition.get("schedule")
    if not isinstance(raw_schedule, dict):
        raise StudyError("Study definition must contain a schedule object.")
    timezone_name = str(raw_schedule.get("timezone_name") or "").strip()
    utc_offset = str(raw_schedule.get("utc_offset") or "").strip()
    local_time = str(raw_schedule.get("local_time") or "").strip()
    if not timezone_name:
        raise StudyError("schedule.timezone_name is required.")
    parse_utc_offset(utc_offset)
    parse_local_time(local_time)

    raw_rounds = raw_schedule.get("rounds")
    if not isinstance(raw_rounds, list) or not raw_rounds:
        raise StudyError("schedule.rounds must be a nonempty array.")

    rounds: list[RoundDefinition] = []
    seen_ids: set[str] = set()
    seen_dates: set[str] = set()
    previous_date: date | None = None
    for index, raw in enumerate(raw_rounds, 1):
        round_id = str(raw.get("round_id") or "").strip()
        scheduled_date = str(raw.get("scheduled_date") or "").strip()
        scheduled_day = parse_scheduled_date(scheduled_date)
        if not round_id:
            raise StudyError("Every scheduled round requires a round_id.")
        if round_id in seen_ids:
            raise StudyError(f"Duplicate round_id: {round_id}")
        if scheduled_date in seen_dates:
            raise StudyError(f"Duplicate scheduled_date: {scheduled_date}")
        if previous_date is not None and scheduled_day <= previous_date:
            raise StudyError("Scheduled dates must be strictly increasing.")
        if scheduled_day.weekday() >= 5:
            raise StudyError(f"Scheduled date is a weekend: {scheduled_date}")
        rounds.append(
            RoundDefinition(
                round_index=index,
                round_id=round_id,
                scheduled_date=scheduled_date,
                label=str(raw.get("label") or ""),
            )
        )
        seen_ids.add(round_id)
        seen_dates.add(scheduled_date)
        previous_date = scheduled_day

    expected_round_count = int(raw_schedule.get("expected_round_count") or len(rounds))
    if expected_round_count != len(rounds):
        raise StudyError(
            f"expected_round_count={expected_round_count} does not match {len(rounds)} rounds."
        )
    if len(rounds) != 3:
        raise StudyError(f"Study 06 requires exactly three rounds; found {len(rounds)}.")

    source_definition, endpoint, variants = STUDY05.load_definition(source_path)
    if len(variants) != 9:
        raise StudyError(f"Study 06 requires the nine Study 05 variants; found {len(variants)}.")
    if str(source_definition.get("subject") or "") != str(definition.get("subject") or ""):
        raise StudyError("Study 06 subject must match the source Study 05 subject.")

    expected_request_count = int(
        definition.get("expected_request_count") or len(rounds) * len(variants)
    )
    if expected_request_count != len(rounds) * len(variants):
        raise StudyError(
            "expected_request_count does not match scheduled rounds multiplied by variants."
        )

    return (
        definition,
        ScheduleDefinition(
            timezone_name=timezone_name,
            utc_offset=utc_offset,
            local_time=local_time,
            rounds=rounds,
        ),
        source_path,
        source_definition,
        endpoint,
        variants,
    )


def target_local_datetime(
    schedule: ScheduleDefinition, round_definition: RoundDefinition
) -> datetime:
    local_zone = parse_utc_offset(schedule.utc_offset)
    return datetime.combine(
        parse_scheduled_date(round_definition.scheduled_date),
        parse_local_time(schedule.local_time),
        tzinfo=local_zone,
    )


def target_utc_datetime(
    schedule: ScheduleDefinition, round_definition: RoundDefinition
) -> datetime:
    return target_local_datetime(schedule, round_definition).astimezone(timezone.utc)


def local_datetime_for_utc(value: datetime, schedule: ScheduleDefinition) -> datetime:
    return value.astimezone(parse_utc_offset(schedule.utc_offset))


def portable_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPOSITORY_ROOT.resolve()).as_posix()
    except ValueError:
        return path.name


def comparison_paths() -> dict[str, str]:
    return {
        "chart_day_results": "comparison/chart-day-results.csv",
        "chart_day_stability": "comparison/chart-day-stability.csv",
    }


def round_relative_path(round_definition: RoundDefinition) -> str:
    return f"rounds/{round_definition.round_id}_{round_definition.scheduled_date}"


def build_resolved_definition(
    definition: dict[str, Any],
    schedule: ScheduleDefinition,
    source_path: Path,
    source_definition: dict[str, Any],
    endpoint: dict[str, str],
    variants: list[Any],
) -> dict[str, Any]:
    return {
        "study_schema_version": STUDY_SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "study_id": definition["study_id"],
        "study_version": definition["study_version"],
        "study_title": definition["study_title"],
        "study_variable": definition["study_variable"],
        "series_id": definition["series_id"],
        "subject": definition["subject"],
        "session_mode": definition["session_mode"],
        "source_study_definition": portable_path(source_path),
        "source_study_id": source_definition["study_id"],
        "source_study_version": source_definition["study_version"],
        "endpoint": endpoint,
        "schedule": {
            "timezone_name": schedule.timezone_name,
            "utc_offset": schedule.utc_offset,
            "local_time": schedule.local_time,
            "rounds": [
                {
                    **asdict(item),
                    "scheduled_at_local": target_local_datetime(
                        schedule, item
                    ).isoformat(timespec="seconds"),
                    "scheduled_at_utc": format_utc(target_utc_datetime(schedule, item)),
                }
                for item in schedule.rounds
            ],
        },
        "variants": [
            {
                **asdict(variant),
            }
            for variant in variants
        ],
        "base_chart_tool": portable_path(STUDY05_TOOL_PATH),
        "base_chart_tool_version": getattr(STUDY05, "TOOL_VERSION", None),
        "base_capture_tool_version": getattr(BASE, "TOOL_VERSION", None),
        "sensitive_values_persisted": False,
    }


def initial_manifest(
    definition: dict[str, Any],
    schedule: ScheduleDefinition,
    source_path: Path,
    source_definition: dict[str, Any],
    resolved_relative: str,
    resolved_sha256: str,
    definition_path: Path,
) -> dict[str, Any]:
    paths = comparison_paths()
    return {
        "study_schema_version": STUDY_SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "series_id": definition["series_id"],
        "run_id": definition["series_id"],
        "run_status": "in_progress",
        "study_id": definition["study_id"],
        "study_version": definition["study_version"],
        "study_title": definition["study_title"],
        "study_variable": definition["study_variable"],
        "subject": definition["subject"],
        "session_mode": definition["session_mode"],
        "study_definition_file": resolved_relative,
        "study_definition_sha256": resolved_sha256,
        "study_definition_source_file": portable_path(definition_path),
        "study_definition_source_sha256": BASE.sha256_bytes(definition_path.read_bytes()),
        "source_study_definition_file": portable_path(source_path),
        "source_study_definition_sha256": BASE.sha256_bytes(source_path.read_bytes()),
        "source_study_id": source_definition["study_id"],
        "source_study_version": source_definition["study_version"],
        "schedule": {
            "timezone_name": schedule.timezone_name,
            "utc_offset": schedule.utc_offset,
            "local_time": schedule.local_time,
            "rounds": [
                {
                    **asdict(item),
                    "scheduled_at_local": target_local_datetime(
                        schedule, item
                    ).isoformat(timespec="seconds"),
                    "scheduled_at_utc": format_utc(target_utc_datetime(schedule, item)),
                }
                for item in schedule.rounds
            ],
        },
        "comparison_files": paths,
        "rounds": [],
        "summary": {
            "planned_round_count": len(schedule.rounds),
            "completed_round_count": 0,
            "planned_request_count": len(schedule.rounds) * 9,
            "evidence_record_count": 0,
            "http_200_count": 0,
            "valid_json_count": 0,
            "expected_top_level_found_count": 0,
            "chart_result_present_count": 0,
            "chart_error_present_count": 0,
            "all_evidence_records_written": False,
            "sensitive_values_persisted": False,
        },
    }


def open_or_initialize_series(
    definition_path: Path,
    output_parent: Path,
    definition: dict[str, Any],
    schedule: ScheduleDefinition,
    source_path: Path,
    source_definition: dict[str, Any],
    endpoint: dict[str, str],
    variants: list[Any],
) -> tuple[Path, dict[str, Any]]:
    series_dir = output_parent / definition["series_id"]
    manifest_path = series_dir / "series-manifest.json"

    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("series_id") != definition["series_id"]:
            raise StudyError("Existing series manifest has the wrong series_id.")
        if manifest.get("study_id") != definition["study_id"]:
            raise StudyError("Existing series manifest has the wrong study_id.")
        if (
            manifest.get("study_definition_source_sha256")
            != BASE.sha256_bytes(definition_path.read_bytes())
        ):
            raise StudyError(
                "The Study 06 definition changed after the series was initialized."
            )
        if (
            manifest.get("source_study_definition_sha256")
            != BASE.sha256_bytes(source_path.read_bytes())
        ):
            raise StudyError(
                "The source Study 05 definition changed after the series was initialized."
            )
        return series_dir, manifest

    if series_dir.exists() and any(series_dir.iterdir()):
        raise StudyError(
            f"Series directory exists without a valid manifest: {series_dir}"
        )

    for relative in ("rounds", "comparison", "errors"):
        (series_dir / relative).mkdir(parents=True, exist_ok=True)

    resolved = build_resolved_definition(
        definition, schedule, source_path, source_definition, endpoint, variants
    )
    resolved_relative = "study-definition.resolved.json"
    resolved_path = series_dir / resolved_relative
    write_json(resolved_path, resolved)
    manifest = initial_manifest(
        definition,
        schedule,
        source_path,
        source_definition,
        resolved_relative,
        BASE.sha256_bytes(resolved_path.read_bytes()),
        definition_path,
    )
    write_json(manifest_path, manifest)
    return series_dir, manifest


def completed_round_ids(manifest: dict[str, Any]) -> set[str]:
    return {
        str(item.get("round_id"))
        for item in manifest.get("rounds", [])
        if item.get("round_status") == "completed"
    }


def next_incomplete_round(
    schedule: ScheduleDefinition, manifest: dict[str, Any]
) -> RoundDefinition | None:
    completed = completed_round_ids(manifest)
    return next((item for item in schedule.rounds if item.round_id not in completed), None)


def wait_for_target(
    target_utc: datetime,
    *,
    now: Callable[[], datetime] = utc_now,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    while True:
        remaining = (target_utc - now()).total_seconds()
        if remaining <= 0:
            return
        sleep(min(30.0, remaining))


def schedule_decision(
    schedule: ScheduleDefinition,
    round_definition: RoundDefinition,
    *,
    now_utc: datetime,
    run_now: bool,
) -> tuple[datetime, str]:
    target_utc = target_utc_datetime(schedule, round_definition)
    if run_now:
        return target_utc, "run-now"

    current_local = local_datetime_for_utc(now_utc, schedule)
    target_local = target_local_datetime(schedule, round_definition)
    if current_local.date() < target_local.date():
        raise StudyError(
            f"The next round is scheduled for {target_local.strftime('%A, %B %d, %Y')} "
            f"at {schedule.local_time} {schedule.timezone_name}. "
            "Start the tool on that date; it will not wait across days."
        )
    if current_local.date() > target_local.date():
        raise StudyError(
            f"The scheduled date for {round_definition.round_id} has passed. "
            "Use --run-now only if a late recovery capture is intentionally required."
        )
    if now_utc < target_utc:
        return target_utc, "scheduled-wait"
    return target_utc, "same-day-late"


def build_round_manifest(
    *,
    definition: dict[str, Any],
    source_definition: dict[str, Any],
    schedule: ScheduleDefinition,
    round_definition: RoundDefinition,
    round_relative: str,
    scheduled_at_utc: str,
    schedule_offset_seconds: float,
    schedule_override: str,
    run_started: datetime,
    run_completed: datetime,
    records: list[dict[str, Any]],
    session: Any,
    timeout: float,
    maximum_attempts: int,
    pause_ms: int,
) -> dict[str, Any]:
    metadata_rows = [record["metadata"] for record in records]
    return {
        "study_schema_version": STUDY_SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "run_id": (
            f"{definition['series_id']}_{round_definition.round_id}_"
            f"{round_definition.scheduled_date}"
        ),
        "run_status": "completed",
        "round_id": round_definition.round_id,
        "round_index": round_definition.round_index,
        "round_status": "completed",
        "study_id": definition["study_id"],
        "study_version": definition["study_version"],
        "study_variable": definition["study_variable"],
        "source_study_id": source_definition["study_id"],
        "source_study_version": source_definition["study_version"],
        "subject": definition["subject"],
        "session_mode": definition["session_mode"],
        "scheduled_date": round_definition.scheduled_date,
        "scheduled_local_time": schedule.local_time,
        "scheduled_timezone_name": schedule.timezone_name,
        "scheduled_utc_offset": schedule.utc_offset,
        "scheduled_at_utc": scheduled_at_utc,
        "round_started_at_utc": format_utc(run_started),
        "round_completed_at_utc": format_utc(run_completed),
        "schedule_offset_seconds": round(schedule_offset_seconds, 3),
        "schedule_override": schedule_override,
        "round_folder": round_relative,
        "default_pause_ms": pause_ms,
        "timeout_seconds": timeout,
        "maximum_attempts": maximum_attempts,
        "authentication": session.public_summary(),
        "comparison_files": STUDY05.comparison_paths(),
        "requests": metadata_rows,
        "summary": {
            "planned_request_count": len(records),
            "evidence_record_count": len(metadata_rows),
            "http_response_count": sum(
                row.get("http_status") is not None for row in metadata_rows
            ),
            "http_200_count": sum(row.get("http_status") == 200 for row in metadata_rows),
            "valid_json_count": sum(
                row.get("parse_status") == "VALID_JSON" for row in metadata_rows
            ),
            "expected_top_level_found_count": sum(
                bool(row.get("expected_top_level_found")) for row in metadata_rows
            ),
            "chart_result_present_count": sum(
                int(row.get("chart_result_count") or 0) > 0 for row in metadata_rows
            ),
            "chart_error_present_count": sum(
                bool(row.get("chart_error_present")) for row in metadata_rows
            ),
            "all_evidence_records_written": len(metadata_rows) == len(records),
            "sensitive_values_persisted": False,
        },
    }


def capture_round(
    *,
    series_dir: Path,
    definition: dict[str, Any],
    source_definition: dict[str, Any],
    endpoint: dict[str, str],
    variants: list[Any],
    schedule: ScheduleDefinition,
    round_definition: RoundDefinition,
    scheduled_at_utc: datetime,
    schedule_override: str,
    timeout: float,
    maximum_attempts: int,
    pause_ms: int,
    session_factory: Callable[[float], Any] | None = None,
    capture_function: Callable[..., tuple[bytes, dict[str, Any]]] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = utc_now,
    clock: Callable[[], float] = time.perf_counter,
) -> tuple[Path, dict[str, Any]]:
    run_started = now()
    round_relative = round_relative_path(round_definition)
    round_dir = series_dir / round_relative
    if round_dir.exists():
        raise StudyError(f"Round directory already exists: {round_dir}")
    for relative in ("raw/chart", "metadata/chart", "errors/chart", "comparison"):
        (round_dir / relative).mkdir(parents=True, exist_ok=True)

    round_run_id = (
        f"{definition['series_id']}_{round_definition.round_id}_"
        f"{round_definition.scheduled_date}"
    )
    plan = STUDY05.build_plan(
        source_definition,
        endpoint,
        variants,
        run_id=round_run_id,
        run_started=run_started,
    )
    session = (session_factory or (lambda value: BASE.PreparedYahooSession(timeout=value)))(
        timeout
    )
    capture = capture_function or BASE.capture_one
    records: list[dict[str, Any]] = []

    for index, planned in enumerate(plan):
        print(
            f"[{planned.sequence:02d}/{len(plan)}] "
            f"{round_definition.round_id} / chart / "
            f"{planned.variant.variant_id} ... ",
            end="",
            flush=True,
        )
        body, metadata = capture(
            planned.base_planned,
            session,
            maximum_attempts=maximum_attempts,
            sleep=sleep,
            now=now,
            clock=clock,
        )
        metrics = STUDY05.extract_chart_metrics(body)
        public_metrics = {
            key: value for key, value in metrics.items() if not key.startswith("_")
        }
        raw_relative = f"raw/chart/{planned.variant.variant_id}.raw.json"
        metadata_relative = f"metadata/chart/{planned.variant.variant_id}.meta.json"
        error_relative = f"errors/chart/{planned.variant.variant_id}.error.txt"
        (round_dir / raw_relative).write_bytes(body)

        metadata.update(
            {
                "study_id": definition["study_id"],
                "study_version": definition["study_version"],
                "study_variable": definition["study_variable"],
                "study_condition": round_definition.round_id,
                "series_id": definition["series_id"],
                "round_id": round_definition.round_id,
                "round_index": round_definition.round_index,
                "scheduled_date": round_definition.scheduled_date,
                "scheduled_local_time": schedule.local_time,
                "scheduled_timezone_name": schedule.timezone_name,
                "scheduled_utc_offset": schedule.utc_offset,
                "scheduled_at_utc": format_utc(scheduled_at_utc),
                "schedule_override": schedule_override,
                "variant_id": planned.variant.variant_id,
                "variant_label": planned.variant.label,
                "variation_group": planned.variant.variation_group,
                "changed_parameter": planned.variant.changed_parameter,
                "control_variant_id": planned.variant.control_variant_id,
                "raw_response_file": raw_relative,
                "metadata_file": metadata_relative,
                "error_file": error_relative if metadata.get("error") else None,
                "round_folder": round_relative,
                "sensitive_values_persisted": False,
                **public_metrics,
            }
        )
        write_json(round_dir / metadata_relative, metadata)
        if metadata.get("error"):
            (round_dir / error_relative).write_text(
                str(metadata["error"]) + "\n", encoding="utf-8", newline="\n"
            )
        records.append({"metadata": metadata, "metrics": metrics})

        status = metadata.get("http_status")
        status_display = status if status is not None else "NO_HTTP"
        print(f"HTTP {status_display} {metadata.get('result_classification')}")
        if pause_ms > 0 and index + 1 < len(plan):
            sleep(pause_ms / 1000.0)

    metadata_rows = [record["metadata"] for record in records]
    paths = STUDY05.comparison_paths()
    write_csv(
        round_dir / paths["chart_parameter_results"],
        STUDY05.RESULT_COLUMNS,
        map(STUDY05.result_row, metadata_rows),
    )
    write_csv(
        round_dir / paths["chart_controlled_comparison"],
        STUDY05.CONTROL_COMPARISON_COLUMNS,
        STUDY05.build_controlled_comparisons(records),
    )

    run_completed = now()
    for metadata in metadata_rows:
        sidecar = json.loads(
            (round_dir / metadata["metadata_file"]).read_text(encoding="utf-8")
        )
        if sidecar != metadata:
            raise StudyError(
                f"Metadata write verification failed: {metadata['metadata_file']}"
            )

    round_manifest = build_round_manifest(
        definition=definition,
        source_definition=source_definition,
        schedule=schedule,
        round_definition=round_definition,
        round_relative=round_relative,
        scheduled_at_utc=format_utc(scheduled_at_utc),
        schedule_offset_seconds=(run_started - scheduled_at_utc).total_seconds(),
        schedule_override=schedule_override,
        run_started=run_started,
        run_completed=run_completed,
        records=records,
        session=session,
        timeout=timeout,
        maximum_attempts=maximum_attempts,
        pause_ms=pause_ms,
    )
    write_json(round_dir / "run-manifest.json", round_manifest)
    return round_dir, round_manifest


def load_completed_records(
    series_dir: Path, manifest: dict[str, Any]
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for round_summary in sorted(
        manifest.get("rounds", []), key=lambda item: int(item["round_index"])
    ):
        round_dir = series_dir / round_summary["round_folder"]
        round_manifest = json.loads(
            (round_dir / "run-manifest.json").read_text(encoding="utf-8")
        )
        for metadata in round_manifest["requests"]:
            raw_path = round_dir / metadata["raw_response_file"]
            metrics = STUDY05.extract_chart_metrics(raw_path.read_bytes())
            records.append(
                {
                    "round": round_manifest,
                    "metadata": metadata,
                    "metrics": metrics,
                }
            )
    return records


def build_day_result_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        round_manifest = record["round"]
        row = STUDY05.result_row(record["metadata"])
        rows.append(
            {
                "round_index": round_manifest["round_index"],
                "round_id": round_manifest["round_id"],
                "scheduled_date": round_manifest["scheduled_date"],
                "scheduled_local_time": round_manifest["scheduled_local_time"],
                "scheduled_at_utc": round_manifest["scheduled_at_utc"],
                "round_started_at_utc": round_manifest["round_started_at_utc"],
                "round_completed_at_utc": round_manifest["round_completed_at_utc"],
                "schedule_offset_seconds": round_manifest["schedule_offset_seconds"],
                "schedule_override": round_manifest["schedule_override"],
                "round_folder": round_manifest["round_folder"],
                **row,
            }
        )
    return rows


def build_day_stability_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_variant: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        variant_id = str(record["metadata"]["variant_id"])
        by_variant.setdefault(variant_id, []).append(record)

    rows: list[dict[str, Any]] = []
    for variant_id in sorted(by_variant):
        ordered = sorted(
            by_variant[variant_id],
            key=lambda item: int(item["round"]["round_index"]),
        )
        control = ordered[0]
        control_round = control["round"]
        control_meta = control["metadata"]
        control_metrics = control["metrics"]
        control_timestamps = set(control_metrics["_timestamps"])
        control_events = {
            canonical_json(item) for item in control_metrics["_event_identities"]
        }

        for record in ordered:
            current_round = record["round"]
            metadata = record["metadata"]
            metrics = record["metrics"]
            timestamps = set(metrics["_timestamps"])
            events = {
                canonical_json(item) for item in metrics["_event_identities"]
            }
            rows.append(
                {
                    "variant_id": variant_id,
                    "round_index": current_round["round_index"],
                    "round_id": current_round["round_id"],
                    "control_round_index": control_round["round_index"],
                    "control_round_id": control_round["round_id"],
                    "scheduled_date": current_round["scheduled_date"],
                    "control_scheduled_date": control_round["scheduled_date"],
                    "request_parameters_equal": (
                        metadata.get("request_parameters_sha256")
                        == control_meta.get("request_parameters_sha256")
                    ),
                    "timestamp_sequence_equal": (
                        metrics["timestamp_sequence_sha256"]
                        == control_metrics["timestamp_sequence_sha256"]
                    ),
                    "timestamp_overlap_count": len(timestamps & control_timestamps),
                    "timestamps_only_in_round": len(timestamps - control_timestamps),
                    "timestamps_only_in_control": len(control_timestamps - timestamps),
                    "meta_key_set_equal": (
                        metrics["_meta_keys"] == control_metrics["_meta_keys"]
                    ),
                    "indicator_key_set_equal": (
                        metrics["_indicator_keys"] == control_metrics["_indicator_keys"]
                    ),
                    "event_identity_set_equal": events == control_events,
                    "canonical_json_equal": (
                        metadata.get("canonical_json_sha256")
                        == control_meta.get("canonical_json_sha256")
                    ),
                    "raw_response_equal": (
                        metadata.get("raw_response_sha256")
                        == control_meta.get("raw_response_sha256")
                    ),
                    "response_bytes_delta": int(metadata.get("response_bytes") or 0)
                    - int(control_meta.get("response_bytes") or 0),
                    "timestamp_count_delta": int(metrics.get("timestamp_count") or 0)
                    - int(control_metrics.get("timestamp_count") or 0),
                    "quote_bar_count_delta": int(metrics.get("quote_bar_count") or 0)
                    - int(control_metrics.get("quote_bar_count") or 0),
                    "adjclose_count_delta": int(metrics.get("adjclose_count") or 0)
                    - int(control_metrics.get("adjclose_count") or 0),
                    "null_indicator_value_count_delta": int(
                        metrics.get("null_indicator_value_count") or 0
                    )
                    - int(
                        control_metrics.get("null_indicator_value_count") or 0
                    ),
                    "event_count_delta": int(metrics.get("event_count") or 0)
                    - int(control_metrics.get("event_count") or 0),
                }
            )
    return rows


def update_series(
    series_dir: Path,
    manifest: dict[str, Any],
    round_manifest: dict[str, Any],
) -> dict[str, Any]:
    existing_ids = {item["round_id"] for item in manifest.get("rounds", [])}
    if round_manifest["round_id"] in existing_ids:
        raise StudyError(f"Round is already recorded: {round_manifest['round_id']}")
    manifest["rounds"].append(
        {
            "round_index": round_manifest["round_index"],
            "round_id": round_manifest["round_id"],
            "round_status": round_manifest["round_status"],
            "scheduled_date": round_manifest["scheduled_date"],
            "scheduled_at_utc": round_manifest["scheduled_at_utc"],
            "round_started_at_utc": round_manifest["round_started_at_utc"],
            "round_completed_at_utc": round_manifest["round_completed_at_utc"],
            "schedule_offset_seconds": round_manifest["schedule_offset_seconds"],
            "schedule_override": round_manifest["schedule_override"],
            "round_folder": round_manifest["round_folder"],
            "round_manifest_file": (
                f"{round_manifest['round_folder']}/run-manifest.json"
            ),
            "summary": round_manifest["summary"],
        }
    )
    manifest["rounds"].sort(key=lambda item: int(item["round_index"]))

    records = load_completed_records(series_dir, manifest)
    paths = comparison_paths()
    day_result_rows = build_day_result_rows(records)
    day_stability_rows = build_day_stability_rows(records)
    write_csv(
        series_dir / paths["chart_day_results"],
        DAY_RESULT_PREFIX_COLUMNS + STUDY05.RESULT_COLUMNS,
        day_result_rows,
    )
    write_csv(
        series_dir / paths["chart_day_stability"],
        DAY_STABILITY_COLUMNS,
        day_stability_rows,
    )

    round_summaries = [item["summary"] for item in manifest["rounds"]]
    completed_count = len(manifest["rounds"])
    planned_count = int(manifest["summary"]["planned_round_count"])
    evidence_count = sum(int(item["evidence_record_count"]) for item in round_summaries)
    manifest["run_status"] = (
        "completed" if completed_count == planned_count else "in_progress"
    )
    manifest["summary"].update(
        {
            "completed_round_count": completed_count,
            "evidence_record_count": evidence_count,
            "http_200_count": sum(int(item["http_200_count"]) for item in round_summaries),
            "valid_json_count": sum(int(item["valid_json_count"]) for item in round_summaries),
            "expected_top_level_found_count": sum(
                int(item["expected_top_level_found_count"]) for item in round_summaries
            ),
            "chart_result_present_count": sum(
                int(item["chart_result_present_count"]) for item in round_summaries
            ),
            "chart_error_present_count": sum(
                int(item["chart_error_present_count"]) for item in round_summaries
            ),
            "all_evidence_records_written": (
                completed_count == planned_count
                and evidence_count == int(manifest["summary"]["planned_request_count"])
            ),
            "sensitive_values_persisted": False,
        }
    )
    write_json(series_dir / "series-manifest.json", manifest)
    return manifest


def print_schedule(
    definition: dict[str, Any],
    schedule: ScheduleDefinition,
    variants: list[Any],
    *,
    output_parent: Path,
) -> None:
    series_dir = output_parent / definition["series_id"]
    completed: set[str] = set()
    status = "not started"
    if (series_dir / "series-manifest.json").exists():
        manifest = json.loads(
            (series_dir / "series-manifest.json").read_text(encoding="utf-8")
        )
        completed = completed_round_ids(manifest)
        status = str(manifest.get("run_status") or "unknown")

    print("Study 06 Chart repeated-day stability dry run")
    print(f"Study: {definition['study_id']} v{definition['study_version']}")
    print(f"Series: {definition['series_id']}")
    print(f"Subject: {definition['subject']}")
    print(f"Variants per round: {len(variants)}")
    print(f"Scheduled rounds: {len(schedule.rounds)}")
    print(f"Planned requests: {len(schedule.rounds) * len(variants)}")
    print(f"Series status: {status}")
    print()
    for item in schedule.rounds:
        target_local = target_local_datetime(schedule, item)
        target_utc = target_utc_datetime(schedule, item)
        pacific = target_utc.astimezone(timezone(timedelta(hours=-7)))
        round_status = "COMPLETE" if item.round_id in completed else "PENDING"
        print(
            f"[{item.round_index:02d}/{len(schedule.rounds)}] "
            f"{item.round_id} — {round_status}"
        )
        print(
            f"  Eastern: {target_local.strftime('%A, %Y-%m-%d %H:%M:%S')} "
            f"{schedule.utc_offset}"
        )
        print(f"  Pacific: {pacific.strftime('%A, %Y-%m-%d %H:%M:%S')} -07:00")
        print(f"  UTC:     {format_utc(target_utc)}")


def run_one_invocation(
    *,
    definition_path: Path,
    output_parent: Path,
    timeout: float,
    maximum_attempts: int,
    pause_ms: int,
    run_now: bool,
    session_factory: Callable[[float], Any] | None = None,
    capture_function: Callable[..., tuple[bytes, dict[str, Any]]] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = utc_now,
    clock: Callable[[], float] = time.perf_counter,
) -> tuple[Path, dict[str, Any], dict[str, Any] | None]:
    (
        definition,
        schedule,
        source_path,
        source_definition,
        endpoint,
        variants,
    ) = load_definition(definition_path)
    series_dir, manifest = open_or_initialize_series(
        definition_path,
        output_parent,
        definition,
        schedule,
        source_path,
        source_definition,
        endpoint,
        variants,
    )

    next_round = next_incomplete_round(schedule, manifest)
    if next_round is None:
        return series_dir, manifest, None

    current = now()
    target_utc, schedule_override = schedule_decision(
        schedule, next_round, now_utc=current, run_now=run_now
    )
    target_local = target_local_datetime(schedule, next_round)
    if schedule_override == "scheduled-wait":
        wait_seconds = max(0.0, (target_utc - current).total_seconds())
        print(
            f"Next round: {next_round.round_id} on "
            f"{target_local.strftime('%A, %B %d, %Y at %H:%M:%S')} "
            f"{schedule.timezone_name} ({format_utc(target_utc)})"
        )
        print(f"Waiting {wait_seconds:.0f} seconds; keep this command window open.")
        wait_for_target(target_utc, now=now, sleep=sleep)
    elif schedule_override == "same-day-late":
        late_seconds = max(0.0, (current - target_utc).total_seconds())
        print(
            f"Scheduled time passed by {late_seconds:.0f} seconds; "
            "starting the same-day round now."
        )
    else:
        print(
            f"--run-now override: capturing {next_round.round_id} "
            f"for scheduled date {next_round.scheduled_date}."
        )

    _, round_manifest = capture_round(
        series_dir=series_dir,
        definition=definition,
        source_definition=source_definition,
        endpoint=endpoint,
        variants=variants,
        schedule=schedule,
        round_definition=next_round,
        scheduled_at_utc=target_utc,
        schedule_override=schedule_override,
        timeout=timeout,
        maximum_attempts=maximum_attempts,
        pause_ms=pause_ms,
        session_factory=session_factory,
        capture_function=capture_function,
        sleep=sleep,
        now=now,
        clock=clock,
    )
    manifest = update_series(series_dir, manifest, round_manifest)
    return series_dir, manifest, round_manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Capture one scheduled round of Study 06 Chart repeated-day stability."
        )
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-parent", type=Path, default=DEFAULT_OUTDIR)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--maximum-attempts", type=int, default=3)
    parser.add_argument("--pause-ms", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--run-now",
        action="store_true",
        help=(
            "Capture the next incomplete round immediately. Reserved for intentional "
            "late recovery or controlled testing."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")
    if args.maximum_attempts <= 0:
        parser.error("--maximum-attempts must be greater than zero")
    if args.pause_ms < 0:
        parser.error("--pause-ms must be nonnegative")
    try:
        if args.dry_run:
            definition, schedule, _, _, _, variants = load_definition(args.config)
            print_schedule(
                definition, schedule, variants, output_parent=args.output_parent
            )
            return 0

        series_dir, manifest, round_manifest = run_one_invocation(
            definition_path=args.config,
            output_parent=args.output_parent,
            timeout=args.timeout,
            maximum_attempts=args.maximum_attempts,
            pause_ms=args.pause_ms,
            run_now=args.run_now,
        )
        summary = manifest["summary"]
        print()
        print(f"Series folder: {series_dir}")
        if round_manifest is None:
            print("Study 06 is already complete; no requests were sent.")
        else:
            round_summary = round_manifest["summary"]
            print(
                f"Completed round: {round_manifest['round_id']} "
                f"({round_manifest['scheduled_date']})"
            )
            print(
                f"Round evidence records: {round_summary['evidence_record_count']}/"
                f"{round_summary['planned_request_count']}"
            )
            print(f"Round HTTP 200 responses: {round_summary['http_200_count']}")
            print(
                "Round expected Chart objects: "
                f"{round_summary['expected_top_level_found_count']}"
            )
        print(
            f"Series rounds: {summary['completed_round_count']}/"
            f"{summary['planned_round_count']}"
        )
        print(
            f"Series evidence records: {summary['evidence_record_count']}/"
            f"{summary['planned_request_count']}"
        )
        print(f"Series status: {manifest['run_status']}")
        print("Day results: comparison\\chart-day-results.csv")
        print("Day stability: comparison\\chart-day-stability.csv")
        return 0
    except StudyError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
