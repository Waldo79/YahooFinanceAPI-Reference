#!/usr/bin/env python3
r"""Run Study 07: validate Yahoo regular-market Day percent change.

Run from repository root:

    py tools\day-change-study\run_day_change_validity_study.py --dry-run
    py tools\day-change-study\run_day_change_validity_study.py --label weekend-baseline

The study reuses the validated Study 02B subject panel, Quote capture implementation,
and Study 05 Chart capture implementation. Raw response bytes are never modified and
cookie or crumb values are never written to evidence.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import sys
import time
from collections import Counter
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

TOOL_VERSION = "0.2.0"
STUDY_SCHEMA_VERSION = "0.5.0"

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPOSITORY_ROOT / "config" / "studies" / "study-07-day-change-validity.json"
DEFAULT_OUTDIR = REPOSITORY_ROOT / "captures" / "local"
QUOTE_TOOL_PATH = REPOSITORY_ROOT / "tools" / "security-type-study" / "run_security_type_replication_study.py"
CHART_TOOL_PATH = REPOSITORY_ROOT / "tools" / "chart-parameter-study" / "run_chart_parameter_variation_study.py"

DAY_CHANGE_FIELDS = (
    "symbol",
    "quoteType",
    "typeDisp",
    "exchange",
    "fullExchangeName",
    "currency",
    "exchangeTimezoneName",
    "gmtOffSetMilliseconds",
    "marketState",
    "market",
    "regularMarketPrice",
    "regularMarketPreviousClose",
    "regularMarketChange",
    "regularMarketChangePercent",
    "regularMarketTime",
    "regularMarketVolume",
    "preMarketPrice",
    "preMarketChange",
    "preMarketChangePercent",
    "preMarketTime",
    "postMarketPrice",
    "postMarketChange",
    "postMarketChangePercent",
    "postMarketTime",
)

VALIDITY_COLUMNS = [
    "subject_sequence",
    "symbol",
    "name",
    "pair_id",
    "representative_role",
    "project_security_type",
    "expected_quote_type",
    "quote_http_status",
    "chart_http_status",
    "quote_result_classification",
    "chart_result_classification",
    "market_state",
    "exchange",
    "exchange_timezone_name",
    "capture_at_utc",
    "capture_local_date",
    "regular_market_time",
    "regular_market_time_utc",
    "regular_market_local_date",
    "current_session_date_match",
    "regular_market_price",
    "regular_market_previous_close",
    "regular_market_change",
    "regular_market_change_percent",
    "near_zero_percent_lower",
    "near_zero_percent_upper",
    "reported_percent_in_near_zero_band",
    "calculated_percent_in_near_zero_band",
    "current_price_equals_previous_close",
    "near_zero_exception",
    "near_zero_exception_cause",
    "regular_market_volume",
    "volume_required_during_regular_session",
    "volume_gate_applicable",
    "volume_positive",
    "chart_matching_session_date",
    "chart_matching_session_close",
    "chart_previous_session_date",
    "chart_previous_session_close",
    "chart_reference_available",
    "previous_close_matches_chart",
    "calculated_change",
    "calculated_percent",
    "reported_change_matches",
    "reported_percent_matches",
    "display_percent_decimals",
    "calculated_display_percent",
    "reported_display_percent",
    "pre_market_fields_present",
    "post_market_fields_present",
    "extended_hours_price_source",
    "validity_classification",
    "display_recommendation",
    "failure_reasons",
    "quote_raw_response_file",
    "chart_raw_response_file",
    "quote_metadata_file",
    "chart_metadata_file",
]

SUMMARY_COLUMNS = [
    "validity_classification",
    "subject_count",
    "symbols_json",
    "project_security_types_json",
]


class StudyError(RuntimeError):
    """Raised when the Study 07 definition or run state is invalid."""


def load_module(name: str, path: Path) -> Any:
    if not path.is_file():
        raise StudyError(f"Required tool is missing: {path}")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise StudyError(f"Could not load tool: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


QUOTE = load_module("study02b_day_change_base", QUOTE_TOOL_PATH)
CHART = load_module("study05_day_change_base", CHART_TOOL_PATH)
BASE = CHART.BASE


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def filename_utc(value: datetime) -> str:
    return format_utc(value).replace(":", "-")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


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


def safe_label(value: str) -> str:
    return QUOTE.safe_filename(value.lower().replace(" ", "-"))


def portable_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPOSITORY_ROOT.resolve()).as_posix()
    except ValueError:
        return path.name


def numeric(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def close_enough(left: float | None, right: float | None, tolerance: float) -> bool | None:
    if left is None or right is None:
        return None
    return abs(left - right) <= tolerance


def parse_json_bytes(body: bytes) -> Any:
    try:
        return json.loads(body)
    except Exception:
        return None


def exact_quote_record(body: bytes, symbol: str) -> dict[str, Any] | None:
    parsed = parse_json_bytes(body)
    if not isinstance(parsed, dict):
        return None
    response = parsed.get("quoteResponse")
    if not isinstance(response, dict):
        return None
    results = response.get("result")
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


def chart_result(body: bytes) -> dict[str, Any] | None:
    parsed = parse_json_bytes(body)
    if not isinstance(parsed, dict):
        return None
    chart = parsed.get("chart")
    if not isinstance(chart, dict):
        return None
    results = chart.get("result")
    if not isinstance(results, list) or not results or not isinstance(results[0], dict):
        return None
    return results[0]


def offset_seconds(quote: dict[str, Any] | None, chart: dict[str, Any] | None) -> int:
    quote = quote or {}
    chart = chart or {}
    milliseconds = numeric(quote.get("gmtOffSetMilliseconds"))
    if milliseconds is not None and abs(milliseconds) <= 24 * 60 * 60 * 1000:
        return int(milliseconds / 1000)
    meta = chart.get("meta") if isinstance(chart.get("meta"), dict) else {}
    seconds = numeric(meta.get("gmtoffset"))
    if seconds is not None and abs(seconds) <= 24 * 60 * 60:
        return int(seconds)
    return 0


def local_date_from_epoch(epoch: Any, offset: int) -> date | None:
    value = numeric(epoch)
    if value is None:
        return None
    try:
        zone = timezone(timedelta(seconds=offset))
        return datetime.fromtimestamp(value, tz=timezone.utc).astimezone(zone).date()
    except (OSError, OverflowError, ValueError):
        return None


def utc_text_from_epoch(epoch: Any) -> str | None:
    value = numeric(epoch)
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
    except (OSError, OverflowError, ValueError):
        return None


def daily_close_reference(
    body: bytes,
    *,
    target_date: date | None,
    fallback_offset_seconds: int,
) -> dict[str, Any]:
    output = {
        "matching_session_date": None,
        "matching_session_close": None,
        "previous_session_date": None,
        "previous_session_close": None,
        "reference_available": False,
        "daily_bar_count": 0,
    }
    result = chart_result(body)
    if result is None or target_date is None:
        return output
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    meta_offset = numeric(meta.get("gmtoffset"))
    offset = (
        int(meta_offset)
        if meta_offset is not None and abs(meta_offset) <= 24 * 60 * 60
        else fallback_offset_seconds
    )
    timestamps = result.get("timestamp")
    indicators = result.get("indicators")
    if not isinstance(timestamps, list) or not isinstance(indicators, dict):
        return output
    quote_blocks = indicators.get("quote")
    if not isinstance(quote_blocks, list) or not quote_blocks or not isinstance(quote_blocks[0], dict):
        return output
    closes = quote_blocks[0].get("close")
    if not isinstance(closes, list):
        return output

    bars: list[tuple[date, float]] = []
    for timestamp, close_value in zip(timestamps, closes):
        local_day = local_date_from_epoch(timestamp, offset)
        close_number = numeric(close_value)
        if local_day is not None and close_number is not None:
            bars.append((local_day, close_number))
    output["daily_bar_count"] = len(bars)
    matches = [index for index, item in enumerate(bars) if item[0] == target_date]
    if not matches:
        return output
    index = matches[-1]
    output["matching_session_date"] = bars[index][0].isoformat()
    output["matching_session_close"] = bars[index][1]
    if index <= 0:
        return output
    output["previous_session_date"] = bars[index - 1][0].isoformat()
    output["previous_session_close"] = bars[index - 1][1]
    output["reference_available"] = True
    return output


def extended_hours_source(record: dict[str, Any]) -> str:
    state = str(record.get("marketState") or "")
    if state in {"PRE", "PREPRE"} and numeric(record.get("preMarketPrice")) is not None:
        return "preMarketPrice"
    if state in {"POST", "POSTPOST"} and numeric(record.get("postMarketPrice")) is not None:
        return "postMarketPrice"
    return ""


def evaluate_day_change(
    *,
    quote_record: dict[str, Any] | None,
    chart_body: bytes,
    capture_at: datetime,
    project_security_type: str,
    tolerances: dict[str, Any],
    required_volume_types: set[str],
) -> dict[str, Any]:
    record = quote_record or {}
    chart = chart_result(chart_body)
    offset = offset_seconds(record, chart)
    local_zone = timezone(timedelta(seconds=offset))
    capture_local_date = capture_at.astimezone(local_zone).date()
    regular_time = record.get("regularMarketTime")
    regular_date = local_date_from_epoch(regular_time, offset)

    price = numeric(record.get("regularMarketPrice"))
    previous = numeric(record.get("regularMarketPreviousClose"))
    reported_change = numeric(record.get("regularMarketChange"))
    reported_percent = numeric(record.get("regularMarketChangePercent"))
    volume = numeric(record.get("regularMarketVolume"))
    market_state = str(record.get("marketState") or "")

    price_tolerance = float(tolerances["price_absolute"])
    change_tolerance = float(tolerances["change_absolute"])
    percent_tolerance = float(tolerances["percent_absolute"])
    near_zero_lower = float(tolerances["near_zero_percent_lower"])
    near_zero_upper = float(tolerances["near_zero_percent_upper"])
    display_decimals = int(tolerances["display_percent_decimals"])

    current_session = regular_date == capture_local_date if regular_date else False
    reference = daily_close_reference(
        chart_body,
        target_date=regular_date,
        fallback_offset_seconds=offset,
    )
    previous_matches = close_enough(
        previous,
        numeric(reference["previous_session_close"]),
        price_tolerance,
    )

    calculated_change = None
    calculated_percent = None
    if price is not None and previous is not None and previous > 0:
        calculated_change = price - previous
        calculated_percent = calculated_change / previous * 100.0

    change_matches = close_enough(calculated_change, reported_change, change_tolerance)
    percent_matches = close_enough(calculated_percent, reported_percent, percent_tolerance)
    price_equals_previous = close_enough(price, previous, price_tolerance)
    reported_in_near_zero_band = (
        reported_percent is not None
        and near_zero_lower <= reported_percent <= near_zero_upper
    )
    calculated_in_near_zero_band = (
        calculated_percent is not None
        and near_zero_lower <= calculated_percent <= near_zero_upper
    )

    volume_required = project_security_type in required_volume_types
    volume_gate_applicable = volume_required and market_state == "REGULAR"
    volume_positive = volume is not None and volume > 0

    required_numeric = (
        price is not None
        and previous is not None
        and previous > 0
        and reported_change is not None
        and reported_percent is not None
        and numeric(regular_time) is not None
    )
    reasons: list[str] = []
    if not required_numeric:
        classification = "INSUFFICIENT_DATA"
        reasons.append("required regular-market fields are missing or unusable")
    elif not current_session:
        classification = "NOT_CURRENT_SESSION"
        reasons.append("regularMarketTime is not on the capture's exchange-local date")
    elif not reference["reference_available"]:
        classification = "CHART_REFERENCE_UNAVAILABLE"
        reasons.append("the preceding daily Chart close could not be resolved")
    elif previous_matches is not True:
        classification = "PREVIOUS_CLOSE_MISMATCH"
        reasons.append("regularMarketPreviousClose differs from the preceding daily Chart close")
    elif change_matches is not True:
        classification = "REPORTED_CHANGE_MISMATCH"
        reasons.append("regularMarketChange does not reconcile with price minus previous close")
    elif percent_matches is not True:
        classification = "REPORTED_PERCENT_MISMATCH"
        reasons.append("regularMarketChangePercent does not reconcile with the calculated percent")
    elif volume_gate_applicable and not volume_positive:
        classification = "VOLUME_NOT_CONFIRMED"
        reasons.append("positive volume was not present during REGULAR marketState")
    elif not reported_in_near_zero_band:
        classification = "OUTSIDE_NEAR_ZERO_BAND"
    elif price_equals_previous is True:
        classification = "NEAR_ZERO_PRICE_EQUAL"
    else:
        classification = "NEAR_ZERO_PRICE_UNEQUAL_EXCEPTION"
        reasons.append(
            "reported Day percent is within the configured near-zero band but "
            "regularMarketPrice does not equal regularMarketPreviousClose"
        )

    near_zero_exception = classification == "NEAR_ZERO_PRICE_UNEQUAL_EXCEPTION"
    near_zero_exception_cause = ""
    if near_zero_exception:
        if calculated_in_near_zero_band:
            near_zero_exception_cause = "SMALL_NONZERO_PRICE_MOVE"
        elif percent_matches is not True:
            near_zero_exception_cause = "REPORTED_PERCENT_DOES_NOT_RECONCILE"
        else:
            near_zero_exception_cause = "UNRESOLVED"

    return {
        "capture_local_date": capture_local_date.isoformat(),
        "regular_market_time": regular_time,
        "regular_market_time_utc": utc_text_from_epoch(regular_time),
        "regular_market_local_date": regular_date.isoformat() if regular_date else None,
        "current_session_date_match": current_session,
        "market_state": market_state,
        "exchange": record.get("exchange"),
        "exchange_timezone_name": record.get("exchangeTimezoneName"),
        "regular_market_price": price,
        "regular_market_previous_close": previous,
        "regular_market_change": reported_change,
        "regular_market_change_percent": reported_percent,
        "near_zero_percent_lower": near_zero_lower,
        "near_zero_percent_upper": near_zero_upper,
        "reported_percent_in_near_zero_band": reported_in_near_zero_band,
        "calculated_percent_in_near_zero_band": calculated_in_near_zero_band,
        "current_price_equals_previous_close": price_equals_previous,
        "near_zero_exception": near_zero_exception,
        "near_zero_exception_cause": near_zero_exception_cause,
        "regular_market_volume": volume,
        "volume_required_during_regular_session": volume_required,
        "volume_gate_applicable": volume_gate_applicable,
        "volume_positive": volume_positive,
        "chart_matching_session_date": reference["matching_session_date"],
        "chart_matching_session_close": reference["matching_session_close"],
        "chart_previous_session_date": reference["previous_session_date"],
        "chart_previous_session_close": reference["previous_session_close"],
        "chart_reference_available": reference["reference_available"],
        "previous_close_matches_chart": previous_matches,
        "calculated_change": calculated_change,
        "calculated_percent": calculated_percent,
        "reported_change_matches": change_matches,
        "reported_percent_matches": percent_matches,
        "display_percent_decimals": display_decimals,
        "calculated_display_percent": (
            round(calculated_percent, display_decimals)
            if calculated_percent is not None
            else None
        ),
        "reported_display_percent": (
            round(reported_percent, display_decimals)
            if reported_percent is not None
            else None
        ),
        "pre_market_fields_present": any(
            record.get(field) is not None
            for field in ("preMarketPrice", "preMarketChange", "preMarketChangePercent", "preMarketTime")
        ),
        "post_market_fields_present": any(
            record.get(field) is not None
            for field in ("postMarketPrice", "postMarketChange", "postMarketChangePercent", "postMarketTime")
        ),
        "extended_hours_price_source": extended_hours_source(record),
        "validity_classification": classification,
        "display_recommendation": (
            "REVIEW"
            if classification == "NEAR_ZERO_PRICE_UNEQUAL_EXCEPTION"
            else "PERCENT"
            if classification in {"NEAR_ZERO_PRICE_EQUAL", "OUTSIDE_NEAR_ZERO_BAND"}
            else "N/A"
        ),
        "failure_reasons": "; ".join(reasons),
    }


def load_definition(
    path: Path,
) -> tuple[dict[str, Any], Path, dict[str, Any], Any, list[Any], dict[str, Any]]:
    try:
        definition = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise StudyError(f"Could not read study definition {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise StudyError(f"Study definition is not valid JSON: {exc}") from exc

    if definition.get("study_id") != "study-07-day-change-validity":
        raise StudyError("Study 07 definition has the wrong study_id.")
    source_path = REPOSITORY_ROOT / str(definition.get("source_subject_definition") or "")
    source_definition, quote_endpoint, subjects = QUOTE.load_study_definition(source_path)
    if len(subjects) != int(definition.get("expected_subject_count") or 0):
        raise StudyError("Study 07 expected_subject_count does not match the source panel.")
    if int(definition.get("expected_request_count") or 0) != len(subjects) * 2:
        raise StudyError("Study 07 expected_request_count must be twice the subject count.")

    chart_endpoint = definition.get("chart_endpoint")
    if not isinstance(chart_endpoint, dict):
        raise StudyError("Study 07 requires a chart_endpoint object.")
    if chart_endpoint.get("endpoint_id") != "chart":
        raise StudyError("Study 07 chart endpoint must use endpoint_id=chart.")
    if "{symbol}" not in str(chart_endpoint.get("base_url") or ""):
        raise StudyError("Study 07 Chart base_url must contain {symbol}.")
    params = chart_endpoint.get("params")
    if not isinstance(params, dict) or params.get("interval") != "1d":
        raise StudyError("Study 07 requires a one-day Chart interval.")

    tolerances = definition.get("numeric_tolerances")
    if not isinstance(tolerances, dict):
        raise StudyError("Study 07 requires numeric_tolerances.")
    for key in ("price_absolute", "change_absolute", "percent_absolute"):
        if float(tolerances.get(key) or 0) <= 0:
            raise StudyError(f"numeric_tolerances.{key} must be positive.")
    near_zero_lower = float(tolerances.get("near_zero_percent_lower"))
    near_zero_upper = float(tolerances.get("near_zero_percent_upper"))
    if near_zero_lower != -0.001 or near_zero_upper != 0.001:
        raise StudyError(
            "Study 07 near-zero band must be exactly -0.001% through +0.001%."
        )
    if int(tolerances.get("display_percent_decimals", -1)) < 0:
        raise StudyError("display_percent_decimals must be nonnegative.")
    return definition, source_path, source_definition, quote_endpoint, subjects, chart_endpoint


def build_quote_plan(
    quote_endpoint: Any,
    subject: Any,
    *,
    sequence: int,
    run_id: str,
) -> Any:
    params = {
        key: value.replace("{symbol}", subject.symbol)
        for key, value in quote_endpoint.params.items()
    }
    fingerprint = QUOTE.sha256_json(
        {
            "method": quote_endpoint.method,
            "base_url": quote_endpoint.base_url,
            "params": params,
            "expected_top_level": quote_endpoint.expected_top_level,
        }
    )
    return QUOTE.PlannedRequest(
        sequence=sequence,
        sample_id=f"{run_id}_{sequence:06d}_quote_{QUOTE.safe_filename(subject.symbol)}",
        subject=subject,
        endpoint=quote_endpoint,
        request_parameters=params,
        request_parameters_sha256=fingerprint,
    )


def build_chart_plan(
    chart_endpoint: dict[str, Any],
    subject: Any,
    *,
    sequence: int,
    run_id: str,
) -> Any:
    params = {str(key): str(value) for key, value in dict(chart_endpoint["params"]).items()}
    base_url = str(chart_endpoint["base_url"]).replace("{symbol}", subject.symbol)
    request = BASE.RequestDefinition(
        request_id=f"chart-{subject.subject_id}",
        endpoint_id="chart",
        method=str(chart_endpoint.get("method") or "GET").upper(),
        base_url=base_url,
        params=params,
        expected_top_level=str(chart_endpoint.get("expected_top_level") or "chart"),
    )
    fingerprint = BASE.sha256_json(
        {
            "method": request.method,
            "base_url": request.base_url,
            "params": request.params,
            "expected_top_level": request.expected_top_level,
        }
    )
    return BASE.PlannedRequest(
        sequence=sequence,
        sample_id=f"{run_id}_{sequence:06d}_chart_{QUOTE.safe_filename(subject.symbol)}",
        request=request,
        mode=CHART.session_mode(),
        request_parameters_sha256=fingerprint,
    )


def summarize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["validity_classification"]), []).append(row)
    output: list[dict[str, Any]] = []
    for classification in sorted(grouped):
        members = grouped[classification]
        output.append(
            {
                "validity_classification": classification,
                "subject_count": len(members),
                "symbols_json": json.dumps(
                    [row["symbol"] for row in members], separators=(",", ":")
                ),
                "project_security_types_json": json.dumps(
                    sorted({row["project_security_type"] for row in members}),
                    separators=(",", ":"),
                ),
            }
        )
    return output


def run_study(
    *,
    definition_path: Path,
    output_parent: Path,
    label: str,
    timeout: float,
    maximum_attempts: int,
    pause_ms: int,
    quote_session_factory: Callable[[float], Any] | None = None,
    chart_session_factory: Callable[[float], Any] | None = None,
    quote_capture: Callable[..., tuple[bytes, dict[str, Any]]] | None = None,
    chart_capture: Callable[..., tuple[bytes, dict[str, Any]]] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = utc_now,
    clock: Callable[[], float] = time.perf_counter,
) -> tuple[Path, dict[str, Any]]:
    run_started = now()
    (
        definition,
        source_path,
        source_definition,
        quote_endpoint,
        subjects,
        chart_endpoint,
    ) = load_definition(definition_path)
    suffix = f"_{safe_label(label)}" if label.strip() else ""
    run_id = f"{filename_utc(run_started)}_study-07-day-change-validity{suffix}"
    run_dir = output_parent / run_id
    if run_dir.exists():
        raise StudyError(f"Run directory already exists: {run_dir}")
    for relative in (
        "raw/quote",
        "raw/chart",
        "metadata/quote",
        "metadata/chart",
        "errors/quote",
        "errors/chart",
        "comparison",
    ):
        (run_dir / relative).mkdir(parents=True, exist_ok=True)

    resolved = {
        "study_schema_version": STUDY_SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        **definition,
        "source_subject_definition": portable_path(source_path),
        "source_subject_definition_sha256": QUOTE.sha256_bytes(source_path.read_bytes()),
        "subject_count": len(subjects),
        "subjects": [asdict(subject) for subject in subjects],
        "resolved_at_utc": format_utc(run_started),
        "sensitive_values_persisted": False,
    }
    resolved_path = run_dir / "study-definition.resolved.json"
    write_json(resolved_path, resolved)

    quote_session = (
        quote_session_factory or (lambda value: QUOTE.PreparedYahooSession(timeout=value))
    )(timeout)
    chart_session = (
        chart_session_factory or (lambda value: BASE.PreparedYahooSession(timeout=value))
    )(timeout)
    capture_quote = quote_capture or QUOTE.capture_one
    capture_chart = chart_capture or BASE.capture_one

    request_records: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    required_volume_types = set(
        definition["volume_policy"]["required_during_regular_session_for"]
    )
    total = len(subjects) * 2

    for subject_index, subject in enumerate(subjects, 1):
        quote_sequence = (subject_index - 1) * 2 + 1
        chart_sequence = quote_sequence + 1
        safe_symbol = QUOTE.safe_filename(subject.symbol)

        quote_plan = build_quote_plan(
            quote_endpoint, subject, sequence=quote_sequence, run_id=run_id
        )
        print(
            f"[{quote_sequence:02d}/{total}] quote / {subject.symbol} ... ",
            end="",
            flush=True,
        )
        quote_body, quote_meta = capture_quote(
            quote_plan,
            quote_session,
            maximum_attempts=maximum_attempts,
            sleep=sleep,
            now=now,
            clock=clock,
        )
        quote_record = exact_quote_record(quote_body, subject.symbol)
        quote_raw_relative = f"raw/quote/{safe_symbol}.raw.json"
        quote_meta_relative = f"metadata/quote/{safe_symbol}.meta.json"
        quote_error_relative = f"errors/quote/{safe_symbol}.error.txt"
        (run_dir / quote_raw_relative).write_bytes(quote_body)
        quote_meta.update(
            {
                "study_id": definition["study_id"],
                "study_version": definition["study_version"],
                "study_variable": definition["study_variable"],
                "study_condition": subject.project_security_type,
                "run_label": label or None,
                "day_change_fields": {
                    field: (quote_record or {}).get(field) for field in DAY_CHANGE_FIELDS
                },
                "raw_response_file": quote_raw_relative,
                "metadata_file": quote_meta_relative,
                "error_file": quote_error_relative if quote_meta.get("error") else None,
                "sensitive_values_persisted": False,
            }
        )
        write_json(run_dir / quote_meta_relative, quote_meta)
        if quote_meta.get("error"):
            (run_dir / quote_error_relative).write_text(
                str(quote_meta["error"]) + "\n", encoding="utf-8", newline="\n"
            )
        request_records.append(quote_meta)
        print(
            f"HTTP {quote_meta.get('http_status') if quote_meta.get('http_status') is not None else 'NO_HTTP'} "
            f"{quote_meta.get('result_classification')}"
        )
        if pause_ms > 0:
            sleep(pause_ms / 1000.0)

        chart_plan = build_chart_plan(
            chart_endpoint, subject, sequence=chart_sequence, run_id=run_id
        )
        print(
            f"[{chart_sequence:02d}/{total}] chart / {subject.symbol} ... ",
            end="",
            flush=True,
        )
        chart_body, chart_meta = capture_chart(
            chart_plan,
            chart_session,
            maximum_attempts=maximum_attempts,
            sleep=sleep,
            now=now,
            clock=clock,
        )
        chart_metrics = CHART.extract_chart_metrics(chart_body)
        chart_public_metrics = {
            key: value for key, value in chart_metrics.items() if not key.startswith("_")
        }
        chart_raw_relative = f"raw/chart/{safe_symbol}.raw.json"
        chart_meta_relative = f"metadata/chart/{safe_symbol}.meta.json"
        chart_error_relative = f"errors/chart/{safe_symbol}.error.txt"
        (run_dir / chart_raw_relative).write_bytes(chart_body)
        chart_meta.update(
            {
                "study_id": definition["study_id"],
                "study_version": definition["study_version"],
                "study_variable": definition["study_variable"],
                "study_condition": subject.project_security_type,
                "run_label": label or None,
                "request_subject": subject.symbol,
                "requested_symbol": subject.symbol,
                "requested_symbols": [subject.symbol],
                "returned_symbols": [subject.symbol]
                if int(chart_public_metrics.get("chart_result_count") or 0) > 0
                else [],
                "project_security_type": subject.project_security_type,
                "expected_quote_type": subject.expected_quote_type,
                "expected_exchange": subject.expected_exchange,
                "subject_name": subject.name,
                "subject_id": subject.subject_id,
                "pair_id": subject.pair_id,
                "representative_role": subject.representative_role,
                "raw_response_file": chart_raw_relative,
                "metadata_file": chart_meta_relative,
                "error_file": chart_error_relative if chart_meta.get("error") else None,
                "sensitive_values_persisted": False,
                **chart_public_metrics,
            }
        )
        write_json(run_dir / chart_meta_relative, chart_meta)
        if chart_meta.get("error"):
            (run_dir / chart_error_relative).write_text(
                str(chart_meta["error"]) + "\n", encoding="utf-8", newline="\n"
            )
        request_records.append(chart_meta)
        print(
            f"HTTP {chart_meta.get('http_status') if chart_meta.get('http_status') is not None else 'NO_HTTP'} "
            f"{chart_meta.get('result_classification')}"
        )

        evaluated = evaluate_day_change(
            quote_record=quote_record,
            chart_body=chart_body,
            capture_at=run_started,
            project_security_type=subject.project_security_type,
            tolerances=definition["numeric_tolerances"],
            required_volume_types=required_volume_types,
        )
        rows.append(
            {
                "subject_sequence": subject_index,
                "symbol": subject.symbol,
                "name": subject.name,
                "pair_id": subject.pair_id,
                "representative_role": subject.representative_role,
                "project_security_type": subject.project_security_type,
                "expected_quote_type": subject.expected_quote_type,
                "quote_http_status": quote_meta.get("http_status"),
                "chart_http_status": chart_meta.get("http_status"),
                "quote_result_classification": quote_meta.get("result_classification"),
                "chart_result_classification": chart_meta.get("result_classification"),
                "capture_at_utc": format_utc(run_started),
                **evaluated,
                "quote_raw_response_file": quote_raw_relative,
                "chart_raw_response_file": chart_raw_relative,
                "quote_metadata_file": quote_meta_relative,
                "chart_metadata_file": chart_meta_relative,
            }
        )
        if pause_ms > 0 and subject_index < len(subjects):
            sleep(pause_ms / 1000.0)

    write_csv(
        run_dir / "comparison/day-change-validity.csv",
        VALIDITY_COLUMNS,
        rows,
    )
    summary_rows = summarize_rows(rows)
    write_csv(
        run_dir / "comparison/day-change-summary.csv",
        SUMMARY_COLUMNS,
        summary_rows,
    )

    run_completed = now()
    for metadata in request_records:
        sidecar = json.loads(
            (run_dir / str(metadata["metadata_file"])).read_text(encoding="utf-8")
        )
        if sidecar != metadata:
            raise StudyError(
                f"Metadata write verification failed: {metadata['metadata_file']}"
            )

    counts = Counter(row["validity_classification"] for row in rows)
    manifest = {
        "study_schema_version": STUDY_SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "run_id": run_id,
        "run_status": "completed",
        "study_id": definition["study_id"],
        "study_version": definition["study_version"],
        "study_title": definition["study_title"],
        "study_variable": definition["study_variable"],
        "run_label": label or None,
        "session_mode": definition["session_mode"],
        "study_definition_file": "study-definition.resolved.json",
        "study_definition_sha256": QUOTE.sha256_bytes(resolved_path.read_bytes()),
        "study_definition_source_file": portable_path(definition_path),
        "study_definition_source_sha256": QUOTE.sha256_bytes(definition_path.read_bytes()),
        "source_subject_definition_file": portable_path(source_path),
        "source_subject_definition_sha256": QUOTE.sha256_bytes(source_path.read_bytes()),
        "run_started_at_utc": format_utc(run_started),
        "run_completed_at_utc": format_utc(run_completed),
        "default_pause_ms": pause_ms,
        "timeout_seconds": timeout,
        "maximum_attempts": maximum_attempts,
        "authentication": {
            "quote": quote_session.public_summary(),
            "chart": chart_session.public_summary(),
        },
        "comparison_files": {
            "day_change_validity": "comparison/day-change-validity.csv",
            "day_change_summary": "comparison/day-change-summary.csv",
        },
        "requests": sorted(request_records, key=lambda item: int(item["sequence"])),
        "summary": {
            "subject_count": len(subjects),
            "planned_request_count": len(subjects) * 2,
            "evidence_record_count": len(request_records),
            "http_200_count": sum(item.get("http_status") == 200 for item in request_records),
            "valid_json_count": sum(item.get("parse_status") == "VALID_JSON" for item in request_records),
            "expected_top_level_found_count": sum(
                bool(item.get("expected_top_level_found")) for item in request_records
            ),
            "classification_counts": dict(sorted(counts.items())),
            "publishable_percent_count": sum(
                row["display_recommendation"] == "PERCENT" for row in rows
            ),
            "review_recommendation_count": sum(
                row["display_recommendation"] == "REVIEW" for row in rows
            ),
            "na_recommendation_count": sum(
                row["display_recommendation"] == "N/A" for row in rows
            ),
            "all_evidence_records_written": len(request_records) == len(subjects) * 2,
            "sensitive_values_persisted": False,
        },
    }
    write_json(run_dir / "run-manifest.json", manifest)
    return run_dir, manifest


def print_dry_run(definition_path: Path, *, label: str = "") -> None:
    (
        definition,
        source_path,
        source_definition,
        quote_endpoint,
        subjects,
        chart_endpoint,
    ) = load_definition(definition_path)
    del source_definition
    print("Study 07 Day percent-change validity dry run")
    print(f"Study: {definition['study_id']} v{definition['study_version']}")
    print(f"Source panel: {portable_path(source_path)}")
    print(f"Subjects: {len(subjects)}")
    print(f"Requests per subject: 2 (Quote + Chart)")
    print(f"Planned requests: {len(subjects) * 2}")
    print(f"Run label: {label or '(none)'}")
    print(
        "Chart request: "
        f"range={chart_endpoint['params']['range']} "
        f"interval={chart_endpoint['params']['interval']} "
        f"includePrePost={chart_endpoint['params']['includePrePost']}"
    )
    for index, subject in enumerate(subjects, 1):
        quote_params = {
            key: value.replace("{symbol}", subject.symbol)
            for key, value in quote_endpoint.params.items()
        }
        chart_url = str(chart_endpoint["base_url"]).replace("{symbol}", subject.symbol)
        print(
            f"[{index:02d}/{len(subjects)}] {subject.symbol} | "
            f"{subject.project_security_type} | Quote {quote_params['symbols']} | Chart {chart_url}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate Yahoo regular-market Day percent change across security types."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-parent", type=Path, default=DEFAULT_OUTDIR)
    parser.add_argument("--label", default="")
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
            print_dry_run(args.config, label=args.label)
            return 0
        run_dir, manifest = run_study(
            definition_path=args.config,
            output_parent=args.output_parent,
            label=args.label,
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
        print(f"Publishable percent values: {summary['publishable_percent_count']}")
        print(f"Near-zero exceptions for review: {summary['review_recommendation_count']}")
        print(f"N/A recommendations: {summary['na_recommendation_count']}")
        print("Validity table: comparison\\day-change-validity.csv")
        print("Summary table: comparison\\day-change-summary.csv")
        return 0
    except (StudyError, QUOTE.StudyError, CHART.StudyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
