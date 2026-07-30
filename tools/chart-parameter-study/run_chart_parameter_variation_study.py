#!/usr/bin/env python3
r"""Run Study 05: controlled Yahoo Chart request-parameter variations.

Run from repository root:

    py tools\chart-parameter-study\run_chart_parameter_variation_study.py --dry-run
    py tools\chart-parameter-study\run_chart_parameter_variation_study.py

The script uses the validated Study 01 session/capture implementation. Raw response
bytes are never modified, and cookie or crumb values are never written to evidence.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

TOOL_VERSION = "0.1.0"
STUDY_SCHEMA_VERSION = "0.5.0"

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPOSITORY_ROOT / "config" / "studies" / "study-05-chart-parameter-variation.json"
DEFAULT_OUTDIR = REPOSITORY_ROOT / "captures" / "local"
BASE_TOOL_PATH = REPOSITORY_ROOT / "tools" / "session-mode-study" / "run_session_mode_study.py"

RESULT_COLUMNS = [
    "sequence",
    "sample_id",
    "variant_id",
    "label",
    "variation_group",
    "changed_parameter",
    "range",
    "interval",
    "period1",
    "period2",
    "include_prepost",
    "events",
    "http_status",
    "parse_status",
    "result_classification",
    "expected_top_level_found",
    "chart_error_present",
    "chart_error_code",
    "chart_result_count",
    "timestamp_count",
    "first_timestamp",
    "last_timestamp",
    "timestamp_sequence_sha256",
    "quote_bar_count",
    "adjclose_count",
    "null_indicator_value_count",
    "meta_field_count",
    "meta_keys_sha256",
    "indicator_field_count",
    "indicator_keys_sha256",
    "event_type_count",
    "event_count",
    "event_identity_sha256",
    "trading_period_group_count",
    "trading_period_count",
    "returned_range",
    "returned_data_granularity",
    "returned_exchange_name",
    "returned_instrument_type",
    "returned_currency",
    "returned_timezone",
    "response_bytes",
    "elapsed_ms",
    "attempt_count",
    "auth_refresh_performed",
    "raw_response_sha256",
    "canonical_json_sha256",
    "request_parameters_sha256",
    "raw_response_file",
    "metadata_file",
]

CONTROL_COMPARISON_COLUMNS = [
    "variant_id",
    "control_variant_id",
    "is_control_identity",
    "timestamp_sequence_equal",
    "timestamp_overlap_count",
    "timestamps_only_in_variant",
    "timestamps_only_in_baseline",
    "meta_key_set_equal",
    "indicator_key_set_equal",
    "event_identity_set_equal",
    "canonical_json_equal",
    "response_bytes_delta",
    "timestamp_count_delta",
    "quote_bar_count_delta",
    "adjclose_count_delta",
    "event_count_delta",
]


class StudyError(RuntimeError):
    """Raised when the study definition or run state is invalid."""


@dataclass(frozen=True)
class VariantDefinition:
    variant_id: str
    label: str
    variation_group: str
    changed_parameter: str
    params: dict[str, str]
    dynamic_period_days: int | None
    control_variant_id: str


@dataclass(frozen=True)
class PlannedVariant:
    sequence: int
    sample_id: str
    variant: VariantDefinition
    base_planned: Any


def load_base_tool() -> Any:
    if not BASE_TOOL_PATH.exists():
        raise StudyError(
            "Study 05 requires tools/session-mode-study/run_session_mode_study.py"
        )
    spec = importlib.util.spec_from_file_location("study01_capture_base", BASE_TOOL_PATH)
    if spec is None or spec.loader is None:
        raise StudyError(f"Could not load Study 01 capture tool: {BASE_TOOL_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BASE = load_base_tool()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def filename_utc(value: datetime) -> str:
    return format_utc(value).replace(":", "-")


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


def load_definition(
    path: Path,
) -> tuple[dict[str, Any], dict[str, str], list[VariantDefinition]]:
    try:
        definition = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise StudyError(f"Could not read study definition {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise StudyError(f"Study definition is not valid JSON: {exc}") from exc

    if definition.get("study_id") != "study-05-chart-parameter-variation":
        raise StudyError("Study 05 definition has the wrong study_id.")
    if definition.get("session_mode") != "cookie-crumb":
        raise StudyError("Study 05 requires session_mode=cookie-crumb.")
    subject = str(definition.get("subject") or "").strip()
    if not subject:
        raise StudyError("Study 05 requires a nonempty subject.")

    raw_endpoint = definition.get("endpoint")
    if not isinstance(raw_endpoint, dict):
        raise StudyError("Study definition must contain an endpoint object.")
    endpoint = {
        "endpoint_id": str(raw_endpoint.get("endpoint_id") or ""),
        "method": str(raw_endpoint.get("method") or "GET").upper(),
        "base_url": str(raw_endpoint.get("base_url") or ""),
        "expected_top_level": str(raw_endpoint.get("expected_top_level") or ""),
    }
    if endpoint["endpoint_id"] != "chart" or endpoint["method"] != "GET":
        raise StudyError("Study 05 requires the Chart GET endpoint.")
    if "{symbol}" not in endpoint["base_url"]:
        raise StudyError("Chart base_url must contain a {symbol} placeholder.")
    if endpoint["expected_top_level"] != "chart":
        raise StudyError("Study 05 expected_top_level must be chart.")

    raw_variants = definition.get("variants")
    order = definition.get("variant_order")
    if not isinstance(raw_variants, list) or not raw_variants:
        raise StudyError("Study definition must contain a nonempty variants array.")
    if not isinstance(order, list) or not order:
        raise StudyError("Study definition must contain variant_order.")

    by_id: dict[str, VariantDefinition] = {}
    for raw in raw_variants:
        variant_id = str(raw.get("variant_id") or "").strip()
        if not variant_id:
            raise StudyError("Every variant must have a nonempty variant_id.")
        if variant_id in by_id:
            raise StudyError(f"Duplicate variant_id: {variant_id}")
        params = {str(k): str(v) for k, v in dict(raw.get("params") or {}).items()}
        dynamic_value = raw.get("dynamic_period_days")
        dynamic_days = None if dynamic_value is None else int(dynamic_value)
        if dynamic_days is not None and dynamic_days <= 0:
            raise StudyError(f"{variant_id}: dynamic_period_days must be positive.")
        if "interval" not in params or not params["interval"].strip():
            raise StudyError(f"{variant_id}: interval is required.")
        if dynamic_days is None and not params.get("range"):
            raise StudyError(f"{variant_id}: range or dynamic_period_days is required.")
        if dynamic_days is not None and "range" in params:
            raise StudyError(
                f"{variant_id}: dynamic period variants must omit the range parameter."
            )
        if params.get("includePrePost") not in {"true", "false"}:
            raise StudyError(
                f"{variant_id}: includePrePost must be the exact string true or false."
            )
        by_id[variant_id] = VariantDefinition(
            variant_id=variant_id,
            label=str(raw.get("label") or ""),
            variation_group=str(raw.get("variation_group") or ""),
            changed_parameter=str(raw.get("changed_parameter") or ""),
            params=params,
            dynamic_period_days=dynamic_days,
            control_variant_id=str(raw.get("control_variant_id") or ""),
        )

    if len(order) != len(set(order)):
        raise StudyError("variant_order contains duplicates.")
    if set(map(str, order)) != set(by_id):
        raise StudyError("variant_order must contain every configured variant exactly once.")
    variants = [by_id[str(variant_id)] for variant_id in order]
    for variant in variants:
        if not variant.control_variant_id or variant.control_variant_id not in by_id:
            raise StudyError(
                f"{variant.variant_id}: control_variant_id must identify a configured variant."
            )

    baseline_id = str((definition.get("controls") or {}).get("baseline_variant_id") or "")
    if not baseline_id or baseline_id not in by_id:
        raise StudyError("controls.baseline_variant_id must identify a configured variant.")
    if variants[0].variant_id != baseline_id:
        raise StudyError("The baseline variant must be first in variant_order.")
    expected_count = int(definition.get("expected_request_count") or len(variants))
    if expected_count != len(variants):
        raise StudyError(
            f"expected_request_count={expected_count} does not match {len(variants)} variants."
        )
    return definition, endpoint, variants


def session_mode() -> Any:
    return BASE.ModeDefinition(
        session_mode="cookie-crumb",
        description=(
            "Prepare an anonymous Yahoo cookie-and-crumb session and add the crumb "
            "query parameter."
        ),
        prepare_cookie=True,
        retrieve_crumb=True,
        send_crumb=True,
    )


def build_plan(
    definition: dict[str, Any],
    endpoint: dict[str, str],
    variants: list[VariantDefinition],
    *,
    run_id: str,
    run_started: datetime,
) -> list[PlannedVariant]:
    mode = session_mode()
    subject = str(definition["subject"])
    base_url = endpoint["base_url"].replace("{symbol}", subject)
    period2 = int(run_started.timestamp())
    plan: list[PlannedVariant] = []

    for sequence, variant in enumerate(variants, 1):
        params = dict(variant.params)
        if variant.dynamic_period_days is not None:
            params["period1"] = str(
                int((run_started - timedelta(days=variant.dynamic_period_days)).timestamp())
            )
            params["period2"] = str(period2)
        request = BASE.RequestDefinition(
            request_id=variant.variant_id,
            endpoint_id="chart",
            method=endpoint["method"],
            base_url=base_url,
            params=params,
            expected_top_level=endpoint["expected_top_level"],
        )
        fingerprint = BASE.sha256_json(
            {
                "method": request.method,
                "base_url": request.base_url,
                "params": request.params,
                "expected_top_level": request.expected_top_level,
            }
        )
        sample_id = f"{run_id}_{sequence:06d}_chart_{variant.variant_id}"
        base_planned = BASE.PlannedRequest(
            sequence=sequence,
            sample_id=sample_id,
            request=request,
            mode=mode,
            request_parameters_sha256=fingerprint,
        )
        plan.append(
            PlannedVariant(
                sequence=sequence,
                sample_id=sample_id,
                variant=variant,
                base_planned=base_planned,
            )
        )
    return plan


def _list_or_empty(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _count_nulls(value: Any) -> int:
    if value is None:
        return 1
    if isinstance(value, list):
        return sum(_count_nulls(item) for item in value)
    if isinstance(value, dict):
        return sum(_count_nulls(item) for item in value.values())
    return 0


def _count_periods(value: Any) -> tuple[int, int]:
    groups = 0
    periods = 0
    if isinstance(value, dict):
        groups += len(value)
        periods += sum(isinstance(item, dict) for item in value.values())
    elif isinstance(value, list):
        groups += len(value)
        for group in value:
            if isinstance(group, list):
                periods += sum(isinstance(item, dict) for item in group)
            elif isinstance(group, dict):
                periods += 1
    return groups, periods


def extract_chart_metrics(body: bytes) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "chart_error_present": False,
        "chart_error_code": None,
        "chart_result_count": 0,
        "timestamp_count": 0,
        "first_timestamp": None,
        "last_timestamp": None,
        "timestamp_sequence_sha256": None,
        "quote_bar_count": 0,
        "adjclose_count": 0,
        "null_indicator_value_count": 0,
        "meta_field_count": 0,
        "meta_keys_sha256": None,
        "indicator_field_count": 0,
        "indicator_keys_sha256": None,
        "event_type_count": 0,
        "event_count": 0,
        "event_identity_sha256": None,
        "trading_period_group_count": 0,
        "trading_period_count": 0,
        "returned_range": None,
        "returned_data_granularity": None,
        "returned_exchange_name": None,
        "returned_instrument_type": None,
        "returned_currency": None,
        "returned_timezone": None,
        "_timestamps": [],
        "_meta_keys": [],
        "_indicator_keys": [],
        "_event_identities": [],
    }
    try:
        parsed = json.loads(body)
    except Exception:
        return metrics
    chart = _dict_or_empty(parsed.get("chart")) if isinstance(parsed, dict) else {}
    error = chart.get("error")
    if error is not None:
        metrics["chart_error_present"] = True
        if isinstance(error, dict):
            metrics["chart_error_code"] = error.get("code")
        else:
            metrics["chart_error_code"] = str(error)
    results = _list_or_empty(chart.get("result"))
    metrics["chart_result_count"] = len(results)
    if not results or not isinstance(results[0], dict):
        return metrics

    result = results[0]
    meta = _dict_or_empty(result.get("meta"))
    timestamps = [value for value in _list_or_empty(result.get("timestamp")) if isinstance(value, int)]
    indicators = _dict_or_empty(result.get("indicators"))
    quote_blocks = _list_or_empty(indicators.get("quote"))
    quote = _dict_or_empty(quote_blocks[0]) if quote_blocks else {}
    adjclose_blocks = _list_or_empty(indicators.get("adjclose"))
    adjclose = _dict_or_empty(adjclose_blocks[0]) if adjclose_blocks else {}

    meta_keys = sorted(map(str, meta.keys()))
    indicator_keys = sorted(
        [f"quote.{key}" for key in quote] + [f"adjclose.{key}" for key in adjclose]
    )
    quote_lengths = [len(value) for value in quote.values() if isinstance(value, list)]
    adjclose_lengths = [len(value) for value in adjclose.values() if isinstance(value, list)]

    events = _dict_or_empty(result.get("events"))
    event_identities: list[list[Any]] = []
    for event_type in sorted(events):
        event_group = _dict_or_empty(events.get(event_type))
        for event_key in sorted(event_group):
            event = _dict_or_empty(event_group.get(event_key))
            event_identities.append(
                [
                    event_type,
                    event_key,
                    event.get("date"),
                    event.get("amount"),
                    event.get("numerator"),
                    event.get("denominator"),
                    event.get("splitRatio"),
                ]
            )

    period_groups, period_count = _count_periods(meta.get("currentTradingPeriod"))
    more_groups, more_periods = _count_periods(result.get("tradingPeriods"))

    metrics.update(
        {
            "timestamp_count": len(timestamps),
            "first_timestamp": timestamps[0] if timestamps else None,
            "last_timestamp": timestamps[-1] if timestamps else None,
            "timestamp_sequence_sha256": BASE.sha256_json(timestamps),
            "quote_bar_count": max(quote_lengths, default=0),
            "adjclose_count": max(adjclose_lengths, default=0),
            "null_indicator_value_count": _count_nulls(quote) + _count_nulls(adjclose),
            "meta_field_count": len(meta_keys),
            "meta_keys_sha256": BASE.sha256_json(meta_keys),
            "indicator_field_count": len(indicator_keys),
            "indicator_keys_sha256": BASE.sha256_json(indicator_keys),
            "event_type_count": len(events),
            "event_count": len(event_identities),
            "event_identity_sha256": BASE.sha256_json(event_identities),
            "trading_period_group_count": period_groups + more_groups,
            "trading_period_count": period_count + more_periods,
            "returned_range": meta.get("range"),
            "returned_data_granularity": meta.get("dataGranularity"),
            "returned_exchange_name": meta.get("exchangeName"),
            "returned_instrument_type": meta.get("instrumentType"),
            "returned_currency": meta.get("currency"),
            "returned_timezone": meta.get("exchangeTimezoneName") or meta.get("timezone"),
            "_timestamps": timestamps,
            "_meta_keys": meta_keys,
            "_indicator_keys": indicator_keys,
            "_event_identities": event_identities,
        }
    )
    return metrics


def result_row(metadata: dict[str, Any]) -> dict[str, Any]:
    params = metadata.get("request_parameters") or {}
    return {
        "sequence": metadata.get("sequence"),
        "sample_id": metadata.get("sample_id"),
        "variant_id": metadata.get("variant_id"),
        "label": metadata.get("variant_label"),
        "variation_group": metadata.get("variation_group"),
        "changed_parameter": metadata.get("changed_parameter"),
        "range": params.get("range"),
        "interval": params.get("interval"),
        "period1": params.get("period1"),
        "period2": params.get("period2"),
        "include_prepost": params.get("includePrePost"),
        "events": params.get("events"),
        **{column: metadata.get(column) for column in RESULT_COLUMNS if column not in {
            "sequence", "sample_id", "variant_id", "label", "variation_group",
            "changed_parameter", "range", "interval", "period1", "period2",
            "include_prepost", "events"
        }},
    }


def build_controlled_comparisons(
    records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_id = {record["metadata"]["variant_id"]: record for record in records}
    output: list[dict[str, Any]] = []

    for record in records:
        meta = record["metadata"]
        metrics = record["metrics"]
        control_variant_id = meta["control_variant_id"]
        if control_variant_id not in by_id:
            raise StudyError(f"Control record not found: {control_variant_id}")
        control = by_id[control_variant_id]
        base_meta = control["metadata"]
        base_metrics = control["metrics"]
        base_timestamps = set(base_metrics["_timestamps"])
        base_events = {
            canonical_json(item) for item in base_metrics["_event_identities"]
        }
        timestamps = set(metrics["_timestamps"])
        events = {canonical_json(item) for item in metrics["_event_identities"]}
        output.append(
            {
                "variant_id": meta["variant_id"],
                "control_variant_id": control_variant_id,
                "is_control_identity": meta["variant_id"] == control_variant_id,
                "timestamp_sequence_equal": (
                    metrics["timestamp_sequence_sha256"]
                    == base_metrics["timestamp_sequence_sha256"]
                ),
                "timestamp_overlap_count": len(timestamps & base_timestamps),
                "timestamps_only_in_variant": len(timestamps - base_timestamps),
                "timestamps_only_in_baseline": len(base_timestamps - timestamps),
                "meta_key_set_equal": metrics["_meta_keys"] == base_metrics["_meta_keys"],
                "indicator_key_set_equal": (
                    metrics["_indicator_keys"] == base_metrics["_indicator_keys"]
                ),
                "event_identity_set_equal": events == base_events,
                "canonical_json_equal": (
                    meta.get("canonical_json_sha256")
                    == base_meta.get("canonical_json_sha256")
                ),
                "response_bytes_delta": int(meta.get("response_bytes") or 0)
                - int(base_meta.get("response_bytes") or 0),
                "timestamp_count_delta": int(metrics.get("timestamp_count") or 0)
                - int(base_metrics.get("timestamp_count") or 0),
                "quote_bar_count_delta": int(metrics.get("quote_bar_count") or 0)
                - int(base_metrics.get("quote_bar_count") or 0),
                "adjclose_count_delta": int(metrics.get("adjclose_count") or 0)
                - int(base_metrics.get("adjclose_count") or 0),
                "event_count_delta": int(metrics.get("event_count") or 0)
                - int(base_metrics.get("event_count") or 0),
            }
        )
    return output


def comparison_paths() -> dict[str, str]:
    return {
        "chart_parameter_results": "comparison/chart-parameter-results.csv",
        "chart_controlled_comparison": "comparison/chart-controlled-comparison.csv",
    }


def build_resolved_definition(
    definition: dict[str, Any],
    endpoint: dict[str, str],
    plan: list[PlannedVariant],
    *,
    run_started_at_utc: str,
) -> dict[str, Any]:
    return {
        "study_schema_version": STUDY_SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "study_id": definition["study_id"],
        "study_version": definition["study_version"],
        "study_title": definition["study_title"],
        "study_variable": definition["study_variable"],
        "subject": definition["subject"],
        "session_mode": definition["session_mode"],
        "resolved_at_utc": run_started_at_utc,
        "request_order": definition.get("request_order"),
        "controls": definition.get("controls"),
        "endpoint": endpoint,
        "variants": [
            {
                **asdict(item.variant),
                "resolved_params": dict(item.base_planned.request.params),
                "request_parameters_sha256": item.base_planned.request_parameters_sha256,
            }
            for item in plan
        ],
        "base_capture_tool": BASE_TOOL_PATH.relative_to(REPOSITORY_ROOT).as_posix(),
        "base_capture_tool_version": getattr(BASE, "TOOL_VERSION", None),
        "sensitive_values_persisted": False,
    }


def run_study(
    *,
    definition_path: Path,
    output_parent: Path,
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
    definition, endpoint, variants = load_definition(definition_path)
    run_id = f"{filename_utc(run_started)}_study-05-chart-parameter-variation"
    run_dir = output_parent / run_id
    if run_dir.exists():
        raise StudyError(f"Run directory already exists: {run_dir}")
    for relative in ("raw/chart", "metadata/chart", "errors/chart", "comparison"):
        (run_dir / relative).mkdir(parents=True, exist_ok=True)

    plan = build_plan(
        definition, endpoint, variants, run_id=run_id, run_started=run_started
    )
    resolved = build_resolved_definition(
        definition, endpoint, plan, run_started_at_utc=format_utc(run_started)
    )
    resolved_relative = "study-definition.resolved.json"
    resolved_path = run_dir / resolved_relative
    write_json(resolved_path, resolved)
    resolved_bytes = resolved_path.read_bytes()

    session = (session_factory or (lambda value: BASE.PreparedYahooSession(timeout=value)))(
        timeout
    )
    capture = capture_function or BASE.capture_one
    records: list[dict[str, Any]] = []

    for index, planned in enumerate(plan):
        print(
            f"[{planned.sequence:02d}/{len(plan)}] chart / {planned.variant.variant_id} ... ",
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
        metrics = extract_chart_metrics(body)
        public_metrics = {
            key: value for key, value in metrics.items() if not key.startswith("_")
        }
        raw_relative = f"raw/chart/{planned.variant.variant_id}.raw.json"
        metadata_relative = f"metadata/chart/{planned.variant.variant_id}.meta.json"
        error_relative = f"errors/chart/{planned.variant.variant_id}.error.txt"
        (run_dir / raw_relative).write_bytes(body)

        metadata.update(
            {
                "study_id": definition["study_id"],
                "study_version": definition["study_version"],
                "study_variable": definition["study_variable"],
                "study_condition": planned.variant.variant_id,
                "variant_id": planned.variant.variant_id,
                "variant_label": planned.variant.label,
                "variation_group": planned.variant.variation_group,
                "changed_parameter": planned.variant.changed_parameter,
                "control_variant_id": planned.variant.control_variant_id,
                "raw_response_file": raw_relative,
                "metadata_file": metadata_relative,
                "error_file": error_relative if metadata.get("error") else None,
                "sensitive_values_persisted": False,
                **public_metrics,
            }
        )
        write_json(run_dir / metadata_relative, metadata)
        if metadata.get("error"):
            (run_dir / error_relative).write_text(
                str(metadata["error"]) + "\n", encoding="utf-8", newline="\n"
            )
        records.append({"metadata": metadata, "metrics": metrics})
        status = metadata.get("http_status")
        status_display = status if status is not None else "NO_HTTP"
        print(f"HTTP {status_display} {metadata.get('result_classification')}")
        if pause_ms > 0 and index + 1 < len(plan):
            sleep(pause_ms / 1000.0)

    metadata_rows = [record["metadata"] for record in records]
    baseline_id = str(definition["controls"]["baseline_variant_id"])
    controlled_rows = build_controlled_comparisons(records)
    paths = comparison_paths()
    write_csv(
        run_dir / paths["chart_parameter_results"],
        RESULT_COLUMNS,
        map(result_row, metadata_rows),
    )
    write_csv(
        run_dir / paths["chart_controlled_comparison"],
        CONTROL_COMPARISON_COLUMNS,
        controlled_rows,
    )

    run_completed = now()
    source_bytes = definition_path.read_bytes()
    for metadata in metadata_rows:
        sidecar = json.loads(
            (run_dir / metadata["metadata_file"]).read_text(encoding="utf-8")
        )
        if sidecar != metadata:
            raise StudyError(
                f"Metadata write verification failed: {metadata['metadata_file']}"
            )

    manifest = {
        "study_schema_version": STUDY_SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "run_id": run_id,
        "run_status": "completed",
        "study_id": definition["study_id"],
        "study_version": definition["study_version"],
        "study_title": definition["study_title"],
        "study_variable": definition["study_variable"],
        "subject": definition["subject"],
        "session_mode": definition["session_mode"],
        "study_definition_file": resolved_relative,
        "study_definition_sha256": BASE.sha256_bytes(resolved_bytes),
        "study_definition_source_file": BASE.portable_source_path(definition_path),
        "study_definition_source_sha256": BASE.sha256_bytes(source_bytes),
        "run_started_at_utc": format_utc(run_started),
        "run_completed_at_utc": format_utc(run_completed),
        "default_pause_ms": pause_ms,
        "timeout_seconds": timeout,
        "maximum_attempts": maximum_attempts,
        "request_order": definition.get("request_order"),
        "authentication": session.public_summary(),
        "comparison_files": paths,
        "requests": metadata_rows,
        "summary": {
            "planned_request_count": len(plan),
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
            "baseline_variant_id": baseline_id,
            "all_evidence_records_written": len(metadata_rows) == len(plan),
            "sensitive_values_persisted": False,
        },
    }
    write_json(run_dir / "run-manifest.json", manifest)
    return run_dir, manifest


def print_dry_run(
    definition_path: Path, *, now: datetime | None = None
) -> None:
    run_started = now or utc_now()
    definition, endpoint, variants = load_definition(definition_path)
    run_id = f"{filename_utc(run_started)}_study-05-chart-parameter-variation"
    plan = build_plan(
        definition, endpoint, variants, run_id=run_id, run_started=run_started
    )
    print("Study 05 Chart parameter-variation dry run")
    print(f"Study: {definition['study_id']} v{definition['study_version']}")
    print(f"Subject: {definition['subject']}")
    print(f"Planned requests: {len(plan)}")
    for planned in plan:
        url = BASE.build_url(
            planned.base_planned.request.base_url,
            planned.base_planned.request.params,
            "REDACTED",
        )
        print(f"[{planned.sequence:02d}/{len(plan)}] {planned.variant.variant_id}")
        print(f"  {BASE.redact_url(url)}")
        print(
            f"  parameter fingerprint: "
            f"{planned.base_planned.request_parameters_sha256}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the controlled Study 05 Yahoo Chart parameter variations."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-parent", type=Path, default=DEFAULT_OUTDIR)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--maximum-attempts", type=int, default=3)
    parser.add_argument("--pause-ms", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
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
            print_dry_run(args.config)
            return 0
        run_dir, manifest = run_study(
            definition_path=args.config,
            output_parent=args.output_parent,
            timeout=args.timeout,
            maximum_attempts=args.maximum_attempts,
            pause_ms=args.pause_ms,
        )
        summary = manifest["summary"]
        print()
        print(f"Study folder: {run_dir}")
        print(
            f"Evidence records: {summary['evidence_record_count']}/"
            f"{summary['planned_request_count']}"
        )
        print(f"HTTP 200 responses: {summary['http_200_count']}")
        print(
            f"Expected Chart objects: "
            f"{summary['expected_top_level_found_count']}"
        )
        print(
            "Parameter results: comparison\\chart-parameter-results.csv"
        )
        print(
            "Controlled comparison: comparison\\chart-controlled-comparison.csv"
        )
        return 0
    except StudyError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
