#!/usr/bin/env python3
r"""Run Study 04: observe Yahoo Quote marketState transitions over a rolling interval.

The study repeatedly captures the Study 03 international equity panel plus a
continuous-market cryptocurrency control. Exact marketState strings are preserved.

Run from repository root:

    py tools\market-state-study\run_market_state_transition_study.py --dry-run
    py tools\market-state-study\run_market_state_transition_study.py

A default live run lasts 26 hours and captures every 15 minutes. Keep the command
window open and the computer awake. Press Ctrl+C for a graceful partial finalization.

The script uses only the Python standard library. It reuses the already validated
Study 03 Yahoo session and one-request capture implementation. Cookie and crumb values
are never written to evidence.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

TOOL_VERSION = "0.1.1"
STUDY_SCHEMA_VERSION = "0.5.0"

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPOSITORY_ROOT / "config" / "studies" / "study-04-market-state-transition.json"
DEFAULT_OUTDIR = REPOSITORY_ROOT / "captures" / "local"
BASE_TOOL_PATH = REPOSITORY_ROOT / "tools" / "exchange-region-study" / "run_exchange_region_quote_study.py"

OBSERVATION_COLUMNS = [
    "sequence",
    "round_index",
    "round_sequence",
    "scheduled_offset_seconds",
    "scheduled_at_utc",
    "requested_at_utc",
    "response_received_at_utc",
    "symbol",
    "name",
    "country_or_market",
    "geographic_region",
    "expected_timezone_name",
    "observation_local_time",
    "observation_local_time_source",
    "observation_utc_offset_milliseconds",
    "expected_quote_type",
    "returned_quote_type",
    "quote_type_match",
    "http_status",
    "result_classification",
    "requested_symbol_returned",
    "market_state",
    "market",
    "exchange",
    "full_exchange_name",
    "currency",
    "exchange_timezone_name",
    "regular_market_price",
    "regular_market_time",
    "pre_market_fields_present",
    "post_market_fields_present",
    "pre_market_field_count",
    "post_market_field_count",
    "pre_market_fields_json",
    "post_market_fields_json",
    "field_count",
    "response_bytes",
    "attempt_count",
    "auth_refresh_performed",
    "raw_response_sha256",
    "canonical_json_sha256",
    "request_parameters_sha256",
    "raw_response_file",
    "metadata_file",
]

STATE_SUMMARY_COLUMNS = [
    "symbol",
    "name",
    "country_or_market",
    "market_state",
    "observation_count",
    "first_observed_at_utc",
    "last_observed_at_utc",
    "first_round_index",
    "last_round_index",
    "pre_market_field_observation_count",
    "post_market_field_observation_count",
]

TRANSITION_COLUMNS = [
    "symbol",
    "name",
    "country_or_market",
    "transition_index",
    "from_market_state",
    "to_market_state",
    "from_round_index",
    "to_round_index",
    "from_observed_at_utc",
    "to_observed_at_utc",
    "elapsed_seconds",
    "from_pre_market_fields_present",
    "to_pre_market_fields_present",
    "from_post_market_fields_present",
    "to_post_market_fields_present",
]

SYMBOL_SUMMARY_COLUMNS = [
    "symbol",
    "name",
    "country_or_market",
    "observation_count",
    "first_observed_at_utc",
    "last_observed_at_utc",
    "distinct_market_state_count",
    "market_states_json",
    "ordered_state_sequence_json",
    "transition_count",
    "pre_market_field_observation_count",
    "post_market_field_observation_count",
]

PRE_MARKET_FIELDS = (
    "preMarketChange",
    "preMarketChangePercent",
    "preMarketPrice",
    "preMarketTime",
)
POST_MARKET_FIELDS = (
    "postMarketChange",
    "postMarketChangePercent",
    "postMarketPrice",
    "postMarketTime",
)


class StudyError(RuntimeError):
    """Raised when the study definition or run state is invalid."""


@dataclass(frozen=True)
class Subject:
    subject_id: str
    symbol: str
    name: str
    geographic_region: str
    country_or_market: str
    yahoo_symbol_suffix: str
    expected_quote_type: str
    expected_exchange_label: str
    expected_currency: str
    expected_timezone_name: str
    selection_role: str
    source_inventory: str
    control_role: str


@dataclass(frozen=True)
class Sampling:
    duration_hours: float
    interval_minutes: float
    include_initial_round: bool


@dataclass(frozen=True)
class PlannedObservation:
    sequence: int
    round_index: int
    round_sequence: int
    scheduled_offset_seconds: int
    scheduled_at_utc: str
    subject: Subject
    base_planned: Any


def load_base_tool() -> Any:
    if not BASE_TOOL_PATH.exists():
        raise StudyError(
            "Study 04 requires the Study 03 capture tool at "
            "tools/exchange-region-study/run_exchange_region_quote_study.py"
        )
    spec = importlib.util.spec_from_file_location("study03_capture_base", BASE_TOOL_PATH)
    if spec is None or spec.loader is None:
        raise StudyError(f"Could not load Study 03 capture tool: {BASE_TOOL_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BASE = load_base_tool()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def filename_utc(value: datetime) -> str:
    return format_utc(value).replace(":", "-")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def sha256_bytes(value: bytes) -> str:
    return BASE.sha256_bytes(value)


def sha256_json(value: Any) -> str:
    return BASE.sha256_json(value)


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


def load_definition(path: Path) -> tuple[dict[str, Any], Any, list[Subject], Sampling]:
    try:
        definition = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise StudyError(f"Could not read study definition {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise StudyError(f"Study definition is not valid JSON: {exc}") from exc

    if definition.get("study_id") != "study-04-market-state-transition":
        raise StudyError("Study 04 definition has the wrong study_id.")
    if definition.get("session_mode") != "cookie-crumb":
        raise StudyError("Study 04 requires session_mode=cookie-crumb.")

    raw_endpoint = definition.get("endpoint")
    if not isinstance(raw_endpoint, dict):
        raise StudyError("Study definition must contain an endpoint object.")
    endpoint = BASE.EndpointDefinition(
        endpoint_id=str(raw_endpoint["endpoint_id"]),
        method=str(raw_endpoint.get("method") or "GET").upper(),
        base_url=str(raw_endpoint["base_url"]),
        params={str(k): str(v) for k, v in dict(raw_endpoint.get("params") or {}).items()},
        expected_top_level=str(raw_endpoint["expected_top_level"]),
    )
    if endpoint.endpoint_id != "quote" or endpoint.method != "GET":
        raise StudyError("Study 04 currently requires the Quote GET endpoint.")
    if "{symbol}" not in canonical_json(endpoint.params):
        raise StudyError("Study 04 endpoint params must contain a {symbol} placeholder.")

    raw_sampling = definition.get("sampling")
    if not isinstance(raw_sampling, dict):
        raise StudyError("Study definition must contain a sampling object.")
    sampling = Sampling(
        duration_hours=float(raw_sampling["duration_hours"]),
        interval_minutes=float(raw_sampling["interval_minutes"]),
        include_initial_round=bool(raw_sampling.get("include_initial_round", True)),
    )
    if sampling.duration_hours <= 0:
        raise StudyError("Sampling duration_hours must be greater than zero.")
    if sampling.interval_minutes <= 0:
        raise StudyError("Sampling interval_minutes must be greater than zero.")
    if not sampling.include_initial_round:
        raise StudyError("Study 04 requires an initial capture round at offset zero.")

    raw_subjects = definition.get("subjects")
    if not isinstance(raw_subjects, list) or not raw_subjects:
        raise StudyError("Study definition must contain a nonempty subjects array.")
    subjects: list[Subject] = []
    seen_symbols: set[str] = set()
    seen_ids: set[str] = set()
    for raw in raw_subjects:
        subject = Subject(
            subject_id=str(raw["subject_id"]),
            symbol=str(raw["symbol"]),
            name=str(raw.get("name") or ""),
            geographic_region=str(raw.get("geographic_region") or ""),
            country_or_market=str(raw.get("country_or_market") or ""),
            yahoo_symbol_suffix=str(raw.get("yahoo_symbol_suffix") or ""),
            expected_quote_type=str(raw["expected_quote_type"]),
            expected_exchange_label=str(raw.get("expected_exchange_label") or ""),
            expected_currency=str(raw.get("expected_currency") or ""),
            expected_timezone_name=str(raw.get("expected_timezone_name") or ""),
            selection_role=str(raw.get("selection_role") or ""),
            source_inventory=str(raw.get("source_inventory") or ""),
            control_role=str(raw.get("control_role") or ""),
        )
        if not subject.symbol.strip():
            raise StudyError("Every subject must have a nonempty symbol.")
        if subject.symbol in seen_symbols:
            raise StudyError(f"Duplicate symbol: {subject.symbol}")
        if subject.subject_id in seen_ids:
            raise StudyError(f"Duplicate subject_id: {subject.subject_id}")
        if not subject.expected_timezone_name.strip():
            raise StudyError(
                f"Missing expected_timezone_name for {subject.symbol}."
            )
        seen_symbols.add(subject.symbol)
        seen_ids.add(subject.subject_id)
        subjects.append(subject)

    if len(subjects) != 13:
        raise StudyError(f"Study 04 requires exactly thirteen subjects; found {len(subjects)}.")
    if "BTC-USD" not in seen_symbols:
        raise StudyError("Study 04 requires BTC-USD as the continuous-market control.")
    calculated_rounds = calculate_round_count(sampling.duration_hours, sampling.interval_minutes)
    configured_rounds = raw_sampling.get("expected_round_count")
    configured_requests = raw_sampling.get("expected_request_count")
    if configured_rounds is not None and int(configured_rounds) != calculated_rounds:
        raise StudyError(
            f"Configured expected_round_count={configured_rounds} does not match "
            f"calculated round count {calculated_rounds}."
        )
    calculated_requests = calculated_rounds * len(subjects)
    if configured_requests is not None and int(configured_requests) != calculated_requests:
        raise StudyError(
            f"Configured expected_request_count={configured_requests} does not match "
            f"calculated request count {calculated_requests}."
        )
    return definition, endpoint, subjects, sampling


def calculate_round_count(duration_hours: float, interval_minutes: float) -> int:
    duration_seconds = duration_hours * 3600.0
    interval_seconds = interval_minutes * 60.0
    return math.floor(duration_seconds / interval_seconds + 1e-12) + 1


def make_base_subject(subject: Subject) -> Any:
    return BASE.SubjectDefinition(
        subject_id=subject.subject_id,
        symbol=subject.symbol,
        name=subject.name,
        geographic_region=subject.geographic_region,
        country_or_market=subject.country_or_market,
        yahoo_symbol_suffix=subject.yahoo_symbol_suffix,
        expected_quote_type=subject.expected_quote_type,
        expected_exchange_label=subject.expected_exchange_label,
        expected_currency=subject.expected_currency,
        selection_role=subject.selection_role,
        source_inventory=subject.source_inventory,
    )


def build_plan(
    endpoint: Any,
    subjects: list[Subject],
    *,
    run_id: str,
    run_started: datetime,
    round_count: int,
    interval_minutes: float,
) -> list[PlannedObservation]:
    plan: list[PlannedObservation] = []
    interval_seconds = round(interval_minutes * 60)
    sequence = 0
    for round_index in range(1, round_count + 1):
        offset_seconds = (round_index - 1) * interval_seconds
        scheduled_at = run_started + timedelta(seconds=offset_seconds)
        for round_sequence, subject in enumerate(subjects, 1):
            sequence += 1
            params = {
                key: value.replace("{symbol}", subject.symbol)
                for key, value in endpoint.params.items()
            }
            fingerprint = sha256_json(
                {
                    "method": endpoint.method,
                    "base_url": endpoint.base_url,
                    "params": params,
                    "expected_top_level": endpoint.expected_top_level,
                }
            )
            base_planned = BASE.PlannedRequest(
                sequence=sequence,
                sample_id=(
                    f"{run_id}_{sequence:06d}_r{round_index:04d}_quote_"
                    f"{BASE.safe_filename(subject.symbol)}"
                ),
                subject=make_base_subject(subject),
                endpoint=endpoint,
                request_parameters=params,
                request_parameters_sha256=fingerprint,
            )
            plan.append(
                PlannedObservation(
                    sequence=sequence,
                    round_index=round_index,
                    round_sequence=round_sequence,
                    scheduled_offset_seconds=offset_seconds,
                    scheduled_at_utc=format_utc(scheduled_at),
                    subject=subject,
                    base_planned=base_planned,
                )
            )
    return plan


def present_fields(record: dict[str, Any] | None, fields: tuple[str, ...]) -> list[str]:
    record = record or {}
    return [field for field in fields if field in record and record.get(field) is not None]


def enrich_metadata(
    metadata: dict[str, Any],
    planned: PlannedObservation,
    *,
    raw_record: dict[str, Any] | None,
    round_started_at_utc: str,
) -> dict[str, Any]:
    selected = metadata.get("selected_quote_fields") or {}
    pre_fields = present_fields(raw_record, PRE_MARKET_FIELDS)
    post_fields = present_fields(raw_record, POST_MARKET_FIELDS)
    observation_local_time = None
    observation_local_time_source = None
    observation_utc_offset_milliseconds = None
    try:
        requested = datetime.fromisoformat(metadata["requested_at_utc"].replace("Z", "+00:00"))
        offset_value = (raw_record or {}).get("gmtOffSetMilliseconds")
        if isinstance(offset_value, (int, float)) and not isinstance(offset_value, bool):
            offset_milliseconds = int(offset_value)
            local_timezone = timezone(timedelta(milliseconds=offset_milliseconds))
            observation_local_time = requested.astimezone(local_timezone).isoformat(
                timespec="milliseconds"
            )
            observation_local_time_source = "yahoo_gmtOffSetMilliseconds"
            observation_utc_offset_milliseconds = offset_milliseconds
    except (OverflowError, TypeError, ValueError):
        observation_local_time = None
        observation_local_time_source = None
        observation_utc_offset_milliseconds = None

    metadata.update(
        {
            "study_id": "study-04-market-state-transition",
            "study_version": "0.1.0",
            "study_variable": "market_state_over_time",
            "study_condition": f"round-{planned.round_index:04d}",
            "sequence": planned.sequence,
            "round_index": planned.round_index,
            "round_sequence": planned.round_sequence,
            "scheduled_offset_seconds": planned.scheduled_offset_seconds,
            "scheduled_at_utc": planned.scheduled_at_utc,
            "round_started_at_utc": round_started_at_utc,
            "expected_timezone_name": planned.subject.expected_timezone_name,
            "observation_local_time": observation_local_time,
            "observation_local_time_source": observation_local_time_source,
            "observation_utc_offset_milliseconds": observation_utc_offset_milliseconds,
            "control_role": planned.subject.control_role,
            "market_state_exact": selected.get("marketState"),
            "pre_market_fields_present": bool(pre_fields),
            "post_market_fields_present": bool(post_fields),
            "pre_market_fields": pre_fields,
            "post_market_fields": post_fields,
            "pre_market_field_count": len(pre_fields),
            "post_market_field_count": len(post_fields),
            "sensitive_values_persisted": False,
        }
    )
    return metadata


def parse_exact_record(body: bytes, symbol: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(body)
    except Exception:
        return None
    try:
        results = parsed["quoteResponse"]["result"]
    except Exception:
        return None
    if not isinstance(results, list):
        return None
    return next(
        (
            item
            for item in results
            if isinstance(item, dict) and str(item.get("symbol") or "") == symbol
        ),
        None,
    )


def observation_row(metadata: dict[str, Any]) -> dict[str, Any]:
    selected = metadata.get("selected_quote_fields") or {}
    return {
        "sequence": metadata["sequence"],
        "round_index": metadata["round_index"],
        "round_sequence": metadata["round_sequence"],
        "scheduled_offset_seconds": metadata["scheduled_offset_seconds"],
        "scheduled_at_utc": metadata["scheduled_at_utc"],
        "requested_at_utc": metadata["requested_at_utc"],
        "response_received_at_utc": metadata["response_received_at_utc"],
        "symbol": metadata["requested_symbol"],
        "name": metadata["subject_name"],
        "country_or_market": metadata["country_or_market"],
        "geographic_region": metadata["geographic_region"],
        "expected_timezone_name": metadata["expected_timezone_name"],
        "observation_local_time": metadata.get("observation_local_time"),
        "observation_local_time_source": metadata.get("observation_local_time_source"),
        "observation_utc_offset_milliseconds": metadata.get(
            "observation_utc_offset_milliseconds"
        ),
        "expected_quote_type": metadata["expected_quote_type"],
        "returned_quote_type": metadata.get("returned_quote_type"),
        "quote_type_match": metadata.get("quote_type_match"),
        "http_status": metadata.get("http_status"),
        "result_classification": metadata.get("result_classification"),
        "requested_symbol_returned": metadata.get("requested_symbol_returned"),
        "market_state": selected.get("marketState"),
        "market": selected.get("market"),
        "exchange": selected.get("exchange"),
        "full_exchange_name": selected.get("fullExchangeName"),
        "currency": selected.get("currency"),
        "exchange_timezone_name": selected.get("exchangeTimezoneName"),
        "regular_market_price": selected.get("regularMarketPrice"),
        "regular_market_time": selected.get("regularMarketTime"),
        "pre_market_fields_present": metadata.get("pre_market_fields_present"),
        "post_market_fields_present": metadata.get("post_market_fields_present"),
        "pre_market_field_count": metadata.get("pre_market_field_count"),
        "post_market_field_count": metadata.get("post_market_field_count"),
        "pre_market_fields_json": canonical_json(metadata.get("pre_market_fields") or []),
        "post_market_fields_json": canonical_json(metadata.get("post_market_fields") or []),
        "field_count": metadata.get("returned_field_count"),
        "response_bytes": metadata.get("response_bytes"),
        "attempt_count": metadata.get("attempt_count"),
        "auth_refresh_performed": metadata.get("auth_refresh_performed"),
        "raw_response_sha256": metadata.get("raw_response_sha256"),
        "canonical_json_sha256": metadata.get("canonical_json_sha256"),
        "request_parameters_sha256": metadata.get("request_parameters_sha256"),
        "raw_response_file": metadata.get("raw_response_file"),
        "metadata_file": metadata.get("metadata_file"),
    }


def build_state_summary(metadata_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in metadata_rows:
        state = (row.get("selected_quote_fields") or {}).get("marketState")
        grouped[(row["requested_symbol"], "" if state is None else str(state))].append(row)
    output: list[dict[str, Any]] = []
    for (symbol, state), rows in sorted(grouped.items()):
        rows = sorted(rows, key=lambda item: item["sequence"])
        first, last = rows[0], rows[-1]
        output.append(
            {
                "symbol": symbol,
                "name": first["subject_name"],
                "country_or_market": first["country_or_market"],
                "market_state": state,
                "observation_count": len(rows),
                "first_observed_at_utc": first["requested_at_utc"],
                "last_observed_at_utc": last["requested_at_utc"],
                "first_round_index": first["round_index"],
                "last_round_index": last["round_index"],
                "pre_market_field_observation_count": sum(
                    bool(row.get("pre_market_fields_present")) for row in rows
                ),
                "post_market_field_observation_count": sum(
                    bool(row.get("post_market_fields_present")) for row in rows
                ),
            }
        )
    return output


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def build_transitions(metadata_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in metadata_rows:
        grouped[row["requested_symbol"]].append(row)
    output: list[dict[str, Any]] = []
    for symbol, rows in sorted(grouped.items()):
        rows = sorted(rows, key=lambda item: (item["round_index"], item["sequence"]))
        transition_index = 0
        previous = None
        for row in rows:
            if previous is None:
                previous = row
                continue
            from_state = (previous.get("selected_quote_fields") or {}).get("marketState")
            to_state = (row.get("selected_quote_fields") or {}).get("marketState")
            if from_state == to_state:
                previous = row
                continue
            transition_index += 1
            elapsed = round(
                (_parse_utc(row["requested_at_utc"]) - _parse_utc(previous["requested_at_utc"])).total_seconds()
            )
            output.append(
                {
                    "symbol": symbol,
                    "name": row["subject_name"],
                    "country_or_market": row["country_or_market"],
                    "transition_index": transition_index,
                    "from_market_state": from_state,
                    "to_market_state": to_state,
                    "from_round_index": previous["round_index"],
                    "to_round_index": row["round_index"],
                    "from_observed_at_utc": previous["requested_at_utc"],
                    "to_observed_at_utc": row["requested_at_utc"],
                    "elapsed_seconds": elapsed,
                    "from_pre_market_fields_present": previous.get("pre_market_fields_present"),
                    "to_pre_market_fields_present": row.get("pre_market_fields_present"),
                    "from_post_market_fields_present": previous.get("post_market_fields_present"),
                    "to_post_market_fields_present": row.get("post_market_fields_present"),
                }
            )
            previous = row
    return output


def build_symbol_summary(metadata_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in metadata_rows:
        grouped[row["requested_symbol"]].append(row)
    transitions = build_transitions(metadata_rows)
    transition_counts: dict[str, int] = defaultdict(int)
    for row in transitions:
        transition_counts[row["symbol"]] += 1
    output: list[dict[str, Any]] = []
    for symbol, rows in sorted(grouped.items()):
        rows = sorted(rows, key=lambda item: (item["round_index"], item["sequence"]))
        states = [
            (row.get("selected_quote_fields") or {}).get("marketState")
            for row in rows
        ]
        ordered: list[Any] = []
        for state in states:
            if not ordered or ordered[-1] != state:
                ordered.append(state)
        distinct = sorted({"" if state is None else str(state) for state in states})
        first, last = rows[0], rows[-1]
        output.append(
            {
                "symbol": symbol,
                "name": first["subject_name"],
                "country_or_market": first["country_or_market"],
                "observation_count": len(rows),
                "first_observed_at_utc": first["requested_at_utc"],
                "last_observed_at_utc": last["requested_at_utc"],
                "distinct_market_state_count": len(distinct),
                "market_states_json": canonical_json(distinct),
                "ordered_state_sequence_json": canonical_json(ordered),
                "transition_count": transition_counts[symbol],
                "pre_market_field_observation_count": sum(
                    bool(row.get("pre_market_fields_present")) for row in rows
                ),
                "post_market_field_observation_count": sum(
                    bool(row.get("post_market_fields_present")) for row in rows
                ),
            }
        )
    return output


def comparison_paths() -> dict[str, str]:
    return {
        "market_state_observations": "comparison/market-state-observations.csv",
        "market_state_summary": "comparison/market-state-summary.csv",
        "market_state_transitions": "comparison/market-state-transitions.csv",
        "symbol_transition_summary": "comparison/symbol-transition-summary.csv",
    }


def write_comparisons(run_dir: Path, metadata_rows: list[dict[str, Any]]) -> None:
    paths = comparison_paths()
    write_csv(
        run_dir / paths["market_state_observations"],
        OBSERVATION_COLUMNS,
        map(observation_row, metadata_rows),
    )
    write_csv(
        run_dir / paths["market_state_summary"],
        STATE_SUMMARY_COLUMNS,
        build_state_summary(metadata_rows),
    )
    write_csv(
        run_dir / paths["market_state_transitions"],
        TRANSITION_COLUMNS,
        build_transitions(metadata_rows),
    )
    write_csv(
        run_dir / paths["symbol_transition_summary"],
        SYMBOL_SUMMARY_COLUMNS,
        build_symbol_summary(metadata_rows),
    )


def build_resolved_definition(
    definition: dict[str, Any],
    endpoint: Any,
    subjects: list[Subject],
    *,
    run_started_at_utc: str,
    duration_hours: float,
    interval_minutes: float,
    round_count: int,
) -> dict[str, Any]:
    return {
        "study_schema_version": STUDY_SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "study_id": definition["study_id"],
        "study_version": definition["study_version"],
        "study_title": definition["study_title"],
        "study_variable": definition["study_variable"],
        "session_mode": definition["session_mode"],
        "resolved_at_utc": run_started_at_utc,
        "request_order": definition.get("request_order"),
        "sampling": {
            "duration_hours": duration_hours,
            "interval_minutes": interval_minutes,
            "round_count": round_count,
            "planned_request_count": round_count * len(subjects),
        },
        "transition_controls": definition.get("transition_controls"),
        "anchor_session_references": definition.get("anchor_session_references"),
        "endpoint": asdict(endpoint),
        "subjects": [asdict(subject) for subject in subjects],
        "base_capture_tool": BASE_TOOL_PATH.relative_to(REPOSITORY_ROOT).as_posix(),
        "base_capture_tool_version": getattr(BASE, "TOOL_VERSION", None),
        "sensitive_values_persisted": False,
    }


def build_manifest(
    *,
    run_id: str,
    run_status: str,
    definition: dict[str, Any],
    definition_path: Path,
    resolved_relative: str,
    resolved_bytes: bytes,
    run_started: datetime,
    run_completed: datetime,
    duration_hours: float,
    interval_minutes: float,
    round_count: int,
    subjects: list[Subject],
    metadata_rows: list[dict[str, Any]],
    session: Any,
    timeout: float,
    maximum_attempts: int,
    pause_ms: int,
) -> dict[str, Any]:
    observed_states = sorted(
        {
            str((row.get("selected_quote_fields") or {}).get("marketState"))
            for row in metadata_rows
            if (row.get("selected_quote_fields") or {}).get("marketState") is not None
        }
    )
    completed_rounds = len({row["round_index"] for row in metadata_rows})
    expected_per_round = len(subjects)
    fully_completed_rounds = sum(
        sum(row["round_index"] == round_index for row in metadata_rows) == expected_per_round
        for round_index in {row["round_index"] for row in metadata_rows}
    )
    source_bytes = definition_path.read_bytes()
    return {
        "study_schema_version": STUDY_SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "run_id": run_id,
        "run_status": run_status,
        "study_id": definition["study_id"],
        "study_version": definition["study_version"],
        "study_title": definition["study_title"],
        "study_variable": definition["study_variable"],
        "session_mode": definition["session_mode"],
        "study_definition_file": resolved_relative,
        "study_definition_sha256": sha256_bytes(resolved_bytes),
        "study_definition_source_file": BASE.portable_source_path(definition_path),
        "study_definition_source_sha256": sha256_bytes(source_bytes),
        "run_started_at_utc": format_utc(run_started),
        "run_completed_at_utc": format_utc(run_completed),
        "duration_hours": duration_hours,
        "interval_minutes": interval_minutes,
        "planned_round_count": round_count,
        "planned_request_count": round_count * len(subjects),
        "default_pause_ms": pause_ms,
        "timeout_seconds": timeout,
        "maximum_attempts": maximum_attempts,
        "request_order": "round-major configured subject order",
        "authentication": session.public_summary(),
        "comparison_files": comparison_paths(),
        "requests": metadata_rows,
        "summary": {
            "subject_count": len(subjects),
            "completed_round_count": completed_rounds,
            "fully_completed_round_count": fully_completed_rounds,
            "evidence_record_count": len(metadata_rows),
            "http_response_count": sum(row.get("http_status") is not None for row in metadata_rows),
            "expected_symbol_returned_count": sum(bool(row.get("requested_symbol_returned")) for row in metadata_rows),
            "quote_type_match_count": sum(row.get("quote_type_match") is True for row in metadata_rows),
            "observed_market_state_values": observed_states,
            "market_state_transition_count": len(build_transitions(metadata_rows)),
            "pre_market_field_observation_count": sum(bool(row.get("pre_market_fields_present")) for row in metadata_rows),
            "post_market_field_observation_count": sum(bool(row.get("post_market_fields_present")) for row in metadata_rows),
            "all_planned_evidence_records_written": len(metadata_rows) == round_count * len(subjects),
            "sensitive_values_persisted": False,
        },
    }


def run_study(
    *,
    definition_path: Path,
    output_parent: Path,
    timeout: float,
    maximum_attempts: int,
    pause_ms: int,
    duration_hours: float | None = None,
    interval_minutes: float | None = None,
    maximum_rounds: int | None = None,
    session_factory: Callable[[float], Any] | None = None,
    capture_function: Callable[..., tuple[bytes, dict[str, Any]]] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = utc_now,
    monotonic: Callable[[], float] = time.monotonic,
) -> tuple[Path, dict[str, Any]]:
    run_started = now()
    definition, endpoint, subjects, sampling = load_definition(definition_path)
    effective_duration = sampling.duration_hours if duration_hours is None else duration_hours
    effective_interval = sampling.interval_minutes if interval_minutes is None else interval_minutes
    if effective_duration <= 0 or effective_interval <= 0:
        raise StudyError("Effective duration and interval must be greater than zero.")
    round_count = calculate_round_count(effective_duration, effective_interval)
    if maximum_rounds is not None:
        if maximum_rounds <= 0:
            raise StudyError("maximum_rounds must be greater than zero.")
        round_count = min(round_count, maximum_rounds)

    run_id = f"{filename_utc(run_started)}_study-04-market-state-transition"
    run_dir = output_parent / run_id
    if run_dir.exists():
        raise StudyError(f"Run directory already exists: {run_dir}")
    for relative in ("raw", "metadata", "errors", "comparison"):
        (run_dir / relative).mkdir(parents=True, exist_ok=True)

    resolved = build_resolved_definition(
        definition,
        endpoint,
        subjects,
        run_started_at_utc=format_utc(run_started),
        duration_hours=effective_duration,
        interval_minutes=effective_interval,
        round_count=round_count,
    )
    resolved_relative = "study-definition.resolved.json"
    resolved_path = run_dir / resolved_relative
    write_json(resolved_path, resolved)
    resolved_bytes = resolved_path.read_bytes()

    plan = build_plan(
        endpoint,
        subjects,
        run_id=run_id,
        run_started=run_started,
        round_count=round_count,
        interval_minutes=effective_interval,
    )
    plan_by_round: dict[int, list[PlannedObservation]] = defaultdict(list)
    for item in plan:
        plan_by_round[item.round_index].append(item)

    session = session_factory(timeout) if session_factory else BASE.PreparedYahooSession(timeout=timeout)
    capture = capture_function or BASE.capture_one
    metadata_rows: list[dict[str, Any]] = []
    interval_seconds = effective_interval * 60.0
    start_monotonic = monotonic()
    interrupted = False

    try:
        for round_index in range(1, round_count + 1):
            round_started = now()
            print(
                f"Round {round_index}/{round_count} started {format_utc(round_started)} "
                f"({len(subjects)} subjects)",
                flush=True,
            )
            round_plan = plan_by_round[round_index]
            for index, planned in enumerate(round_plan):
                print(
                    f"  [{planned.round_sequence:02d}/{len(round_plan)}] "
                    f"{planned.subject.symbol} ... ",
                    end="",
                    flush=True,
                )
                body, metadata = capture(
                    planned.base_planned,
                    session,
                    maximum_attempts=maximum_attempts,
                    sleep=sleep,
                    now=now,
                    clock=monotonic,
                )
                raw_record = parse_exact_record(body, planned.subject.symbol)
                metadata = enrich_metadata(
                    metadata,
                    planned,
                    raw_record=raw_record,
                    round_started_at_utc=format_utc(round_started),
                )
                base_name = (
                    f"{planned.sequence:06d}_r{planned.round_index:04d}_"
                    f"{BASE.safe_filename(planned.subject.symbol)}_quote"
                )
                raw_relative = f"raw/{base_name}.raw.json"
                metadata_relative = f"metadata/{base_name}.meta.json"
                error_relative = f"errors/{base_name}.error.txt"
                (run_dir / raw_relative).write_bytes(body)
                metadata.update(
                    {
                        "raw_response_file": raw_relative,
                        "metadata_file": metadata_relative,
                        "error_file": error_relative if metadata.get("error") else None,
                    }
                )
                write_json(run_dir / metadata_relative, metadata)
                if metadata.get("error"):
                    (run_dir / error_relative).write_text(
                        str(metadata["error"]) + "\n",
                        encoding="utf-8",
                        newline="\n",
                    )
                metadata_rows.append(metadata)
                status_display = metadata["http_status"] if metadata["http_status"] is not None else "NO_HTTP"
                state = (metadata.get("selected_quote_fields") or {}).get("marketState")
                print(f"HTTP {status_display} state={state!r} {metadata['result_classification']}")
                if pause_ms > 0 and index + 1 < len(round_plan):
                    sleep(pause_ms / 1000.0)

            write_comparisons(run_dir, metadata_rows)
            checkpoint = build_manifest(
                run_id=run_id,
                run_status="running" if round_index < round_count else "completed",
                definition=definition,
                definition_path=definition_path,
                resolved_relative=resolved_relative,
                resolved_bytes=resolved_bytes,
                run_started=run_started,
                run_completed=now(),
                duration_hours=effective_duration,
                interval_minutes=effective_interval,
                round_count=round_count,
                subjects=subjects,
                metadata_rows=metadata_rows,
                session=session,
                timeout=timeout,
                maximum_attempts=maximum_attempts,
                pause_ms=pause_ms,
            )
            write_json(run_dir / "run-manifest.json", checkpoint)

            if round_index < round_count:
                target = start_monotonic + round_index * interval_seconds
                remaining = max(0.0, target - monotonic())
                next_utc = run_started + timedelta(seconds=round_index * interval_seconds)
                print(
                    f"Round {round_index} complete; next scheduled {format_utc(next_utc)} "
                    f"(sleep {remaining:.1f} seconds).",
                    flush=True,
                )
                sleep(remaining)
    except KeyboardInterrupt:
        interrupted = True
        print("\nCtrl+C received; finalizing partial evidence...", flush=True)

    write_comparisons(run_dir, metadata_rows)
    run_completed = now()
    final_status = "interrupted" if interrupted else "completed"
    manifest = build_manifest(
        run_id=run_id,
        run_status=final_status,
        definition=definition,
        definition_path=definition_path,
        resolved_relative=resolved_relative,
        resolved_bytes=resolved_bytes,
        run_started=run_started,
        run_completed=run_completed,
        duration_hours=effective_duration,
        interval_minutes=effective_interval,
        round_count=round_count,
        subjects=subjects,
        metadata_rows=metadata_rows,
        session=session,
        timeout=timeout,
        maximum_attempts=maximum_attempts,
        pause_ms=pause_ms,
    )

    verified_requests: list[dict[str, Any]] = []
    for metadata in metadata_rows:
        sidecar = json.loads((run_dir / metadata["metadata_file"]).read_text(encoding="utf-8"))
        if sidecar != metadata:
            raise StudyError(f"Metadata write verification failed: {metadata['metadata_file']}")
        raw = (run_dir / metadata["raw_response_file"]).read_bytes()
        if sha256_bytes(raw) != metadata["raw_response_sha256"]:
            raise StudyError(f"Raw response hash verification failed: {metadata['raw_response_file']}")
        verified_requests.append(metadata)
    manifest["requests"] = verified_requests
    write_json(run_dir / "run-manifest.json", manifest)
    return run_dir, manifest


def print_dry_run(
    definition_path: Path,
    *,
    duration_hours: float | None = None,
    interval_minutes: float | None = None,
    maximum_rounds: int | None = None,
    now: datetime | None = None,
) -> None:
    run_time = now or utc_now()
    definition, endpoint, subjects, sampling = load_definition(definition_path)
    effective_duration = sampling.duration_hours if duration_hours is None else duration_hours
    effective_interval = sampling.interval_minutes if interval_minutes is None else interval_minutes
    round_count = calculate_round_count(effective_duration, effective_interval)
    if maximum_rounds is not None:
        round_count = min(round_count, maximum_rounds)
    planned_requests = round_count * len(subjects)
    end_time = run_time + timedelta(minutes=(round_count - 1) * effective_interval)

    print("Study 04 market-state transition dry run")
    print(f"Study: {definition['study_id']} v{definition['study_version']}")
    print(f"Session mode: {definition['session_mode']}")
    print(f"Subjects: {len(subjects)}")
    print(f"Interval minutes: {effective_interval:g}")
    print(f"Configured duration hours: {effective_duration:g}")
    print(f"Planned rounds: {round_count}")
    print(f"Planned requests: {planned_requests}")
    print(f"Estimated first round: {format_utc(run_time)}")
    print(f"Estimated final round: {format_utc(end_time)}")
    for index, subject in enumerate(subjects, 1):
        params = {k: v.replace("{symbol}", subject.symbol) for k, v in endpoint.params.items()}
        url = BASE.redact_url(BASE.build_url(endpoint.base_url, params, "REDACTED"))
        print(f"[{index:02d}/{len(subjects)}] {subject.symbol} / {subject.country_or_market}")
        print(f"  {url}")
        print(f"  expected quoteType: {subject.expected_quote_type}")
        print(f"  expected timezone: {subject.expected_timezone_name}")
        print(f"  control role: {subject.control_role}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run Study 04 repeated Quote captures to observe exact marketState transitions."
    )
    parser.add_argument(
        "--definition",
        type=Path,
        default=DEFAULT_CONFIG,
        help="Study definition JSON. Default: config/studies/study-04-market-state-transition.json",
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        default=DEFAULT_OUTDIR,
        help="Parent output directory. Default: captures/local",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the plan without contacting Yahoo.")
    parser.add_argument("--duration-hours", type=float, help="Override configured duration hours.")
    parser.add_argument("--interval-minutes", type=float, help="Override configured interval minutes.")
    parser.add_argument(
        "--rounds",
        type=int,
        help="Cap the number of rounds. Useful for a short pilot; default uses the full duration.",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="Per-attempt timeout seconds.")
    parser.add_argument("--maximum-attempts", type=int, default=3, help="Maximum attempts per request.")
    parser.add_argument("--pause-ms", type=int, default=0, help="Optional pause between subjects in a round.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.maximum_attempts <= 0:
            raise StudyError("--maximum-attempts must be greater than zero.")
        if args.pause_ms < 0:
            raise StudyError("--pause-ms cannot be negative.")
        if args.rounds is not None and args.rounds <= 0:
            raise StudyError("--rounds must be greater than zero.")
        if args.dry_run:
            print_dry_run(
                args.definition,
                duration_hours=args.duration_hours,
                interval_minutes=args.interval_minutes,
                maximum_rounds=args.rounds,
            )
            return 0

        run_dir, manifest = run_study(
            definition_path=args.definition,
            output_parent=args.outdir,
            timeout=args.timeout,
            maximum_attempts=args.maximum_attempts,
            pause_ms=args.pause_ms,
            duration_hours=args.duration_hours,
            interval_minutes=args.interval_minutes,
            maximum_rounds=args.rounds,
        )
        summary = manifest["summary"]
        print()
        print(f"Run directory: {run_dir}")
        print(f"Run status: {manifest['run_status']}")
        print(
            f"Evidence records: {summary['evidence_record_count']}/"
            f"{manifest['planned_request_count']}"
        )
        print(
            f"Completed rounds: {summary['fully_completed_round_count']}/"
            f"{manifest['planned_round_count']}"
        )
        print(f"HTTP responses: {summary['http_response_count']}")
        print(f"Expected symbols returned: {summary['expected_symbol_returned_count']}")
        print(f"quoteType matches: {summary['quote_type_match_count']}")
        print(
            "Observed marketState values: "
            + ", ".join(summary["observed_market_state_values"])
        )
        print(f"Transitions recorded: {summary['market_state_transition_count']}")
        print("Resolved definition: study-definition.resolved.json")
        return 0 if manifest["run_status"] == "completed" else 130
    except StudyError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
