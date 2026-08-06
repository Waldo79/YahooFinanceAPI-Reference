#!/usr/bin/env python3
"""Resumable Yahoo Finance long-history baseline and incremental synchronizer.

Version 0.1.0-candidate.10 adds a browser-confirmed exclusion policy for
symbols that provide no downloadable long history while retaining Fast-mode capture. It stores compressed raw Chart JSON outside the synchronized repository
and maintains a local SQLite archive with revision history.

Supported intervals in this candidate: 1d, 1wk, and 1mo. Intraday history is
intentionally excluded because Yahoo retention limits and data volume require a
separate design.

Yahoo Finance endpoints are unofficial and may change without notice.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import importlib.util
import json
import math
import os
import socket
import sqlite3
import sys
import threading
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


# Reuse the tested anonymous Yahoo cookie/crumb session and common safeguards
# from the adjacent Fast-mode utility without making the directory importable as
# a Python package (the repository folder name contains a hyphen).
_FAST_PATH = Path(__file__).with_name("yahoo_fast_capture.py")
_FAST_SPEC = importlib.util.spec_from_file_location("_yahoo_fast_capture_dependency", _FAST_PATH)
if _FAST_SPEC is None or _FAST_SPEC.loader is None:  # pragma: no cover - installation failure
    raise RuntimeError(f"Cannot load Fast-mode dependency: {_FAST_PATH}")
fast = importlib.util.module_from_spec(_FAST_SPEC)
sys.modules[_FAST_SPEC.name] = fast
_FAST_SPEC.loader.exec_module(fast)

UTILITY_VERSION = "0.1.0-candidate.10"
CAPTURE_SCHEMA_VERSION = 1
DATABASE_SCHEMA_VERSION = 1
BASELINE_START_EPOCH = -2208988800  # 1900-01-01T00:00:00Z

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_FILE = REPOSITORY_ROOT / "data" / "high-volume" / "fast_mode_request_list_1547.csv"
DEFAULT_HISTORY_EXCLUSION_FILE = REPOSITORY_ROOT / "data" / "high-volume" / "long_history_exclusions_v0_1.csv"
LONG_HISTORY_EXCLUSION_POLICY = "EXCLUDE_LONG_HISTORY_REQUESTS"
LOCAL_CONFIG_FILE = REPOSITORY_ROOT / "config" / "local" / "history_capture_local.json"
_DEFAULT_ARCHIVE_PARENT = (
    REPOSITORY_ROOT.parent.parent
    if REPOSITORY_ROOT.parent.name.casefold() == "code"
    else REPOSITORY_ROOT.parent
)
DEFAULT_EXTERNAL_ROOT = _DEFAULT_ARCHIVE_PARENT / "Captures" / "long-history"
OUTPUT_ROOT_ENVIRONMENT_VARIABLE = "YAHOO_HISTORY_CAPTURE_ROOT"
DATABASE_FILENAME = "history.sqlite"
CHART_BASE_URL = "https://query2.finance.yahoo.com/v8/finance/chart"
VALID_INTERVALS = frozenset({"1d", "1wk", "1mo"})
VALID_MODES = frozenset({"baseline", "sync", "refresh-flagged"})
RETRYABLE_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})
AUTH_REFRESH_HTTP_STATUSES = frozenset({401, 403})
DEFAULT_USER_AGENT = fast.DEFAULT_USER_AGENT

HistoryInputError = fast.FastCaptureInputError
YahooSessionError = fast.YahooSessionError


@dataclass(frozen=True)
class HistoryExclusion:
    symbol: str
    policy: str
    keep_fast_mode: bool
    category: str
    browser_evidence: str
    api_evidence: str
    evidence_date: str
    reason: str
    notes: str


def load_history_exclusions(path: Path) -> dict[str, HistoryExclusion]:
    path = Path(path)
    if not path.is_file():
        raise HistoryInputError(f"Long-history exclusion file not found: {path}")
    required = {
        "symbol", "policy", "keep_fast_mode", "category", "browser_evidence",
        "api_evidence", "evidence_date", "reason", "notes",
    }
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            missing = required.difference(reader.fieldnames or ())
            if missing:
                raise HistoryInputError(
                    "Long-history exclusion file is missing columns: " + ", ".join(sorted(missing))
                )
            exclusions: dict[str, HistoryExclusion] = {}
            for line_number, row in enumerate(reader, start=2):
                symbol = str(row.get("symbol") or "").strip()
                if not symbol:
                    raise HistoryInputError(f"Blank exclusion symbol at line {line_number}: {path}")
                key = symbol.casefold()
                if key in exclusions:
                    raise HistoryInputError(f"Duplicate exclusion symbol {symbol!r} at line {line_number}: {path}")
                keep_text = str(row.get("keep_fast_mode") or "").strip().casefold()
                if keep_text not in {"true", "false"}:
                    raise HistoryInputError(
                        f"keep_fast_mode must be true or false for {symbol!r} at line {line_number}."
                    )
                exclusions[key] = HistoryExclusion(
                    symbol=symbol,
                    policy=str(row.get("policy") or "").strip(),
                    keep_fast_mode=keep_text == "true",
                    category=str(row.get("category") or "").strip(),
                    browser_evidence=str(row.get("browser_evidence") or "").strip(),
                    api_evidence=str(row.get("api_evidence") or "").strip(),
                    evidence_date=str(row.get("evidence_date") or "").strip(),
                    reason=str(row.get("reason") or "").strip(),
                    notes=str(row.get("notes") or "").strip(),
                )
    except OSError as exc:
        raise HistoryInputError(f"Cannot read long-history exclusion file {path}: {exc}") from exc
    invalid = sorted(
        item.symbol for item in exclusions.values()
        if item.policy != LONG_HISTORY_EXCLUSION_POLICY
    )
    if invalid:
        raise HistoryInputError(
            "Unsupported long-history exclusion policy for: " + ", ".join(invalid)
        )
    return exclusions


def partition_history_symbols(
    symbols: Sequence[str],
    exclusions: Mapping[str, HistoryExclusion],
    *,
    include_excluded: bool = False,
) -> tuple[list[str], list[HistoryExclusion]]:
    included: list[str] = []
    skipped: list[HistoryExclusion] = []
    for symbol in symbols:
        exclusion = exclusions.get(symbol.casefold())
        if exclusion is not None and not include_excluded:
            skipped.append(exclusion)
        else:
            included.append(symbol)
    return included, skipped


def history_exclusion_manifest(
    exclusions: Sequence[HistoryExclusion],
    *,
    exclusion_file: Path,
    override_used: bool,
) -> dict[str, Any]:
    return {
        "policy": LONG_HISTORY_EXCLUSION_POLICY,
        "exclusion_file_name": Path(exclusion_file).name,
        "override_used": override_used,
        "requests_skipped": len(exclusions),
        "symbols": [item.symbol for item in exclusions],
        "browser_evidence": sorted({item.browser_evidence for item in exclusions}),
        "api_evidence": sorted({item.api_evidence for item in exclusions}),
        "existing_database_rows_deleted": False,
        "fast_mode_unchanged": all(item.keep_fast_mode for item in exclusions),
    }


def write_history_exclusion_outputs(
    run_dir: Path,
    exclusions: Sequence[HistoryExclusion],
    *,
    exclusion_file: Path,
    override_used: bool,
    manifest_file: str,
    report_file: str,
) -> dict[str, Any]:
    run_dir = Path(run_dir)
    output_csv = run_dir / "excluded-history-symbols.csv"
    fields = [
        "symbol", "policy", "keep_fast_mode", "category", "browser_evidence",
        "api_evidence", "evidence_date", "reason", "notes",
    ]
    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in exclusions:
            writer.writerow({
                "symbol": item.symbol, "policy": item.policy,
                "keep_fast_mode": str(item.keep_fast_mode).lower(), "category": item.category,
                "browser_evidence": item.browser_evidence, "api_evidence": item.api_evidence,
                "evidence_date": item.evidence_date, "reason": item.reason, "notes": item.notes,
            })
    manifest_path = run_dir / manifest_file
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["history_exclusions"] = history_exclusion_manifest(
        exclusions, exclusion_file=exclusion_file, override_used=override_used
    )
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_path = run_dir / report_file
    with report_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("\nLong-history request exclusions\n")
        handle.write(f"- Policy: {LONG_HISTORY_EXCLUSION_POLICY}\n")
        handle.write(f"- Requests skipped: {len(exclusions)}\n")
        handle.write(f"- Symbols: {','.join(item.symbol for item in exclusions) or 'none'}\n")
        handle.write(f"- Fast-mode capture unchanged: {all(item.keep_fast_mode for item in exclusions)}\n")
        handle.write("- Existing database rows deleted: False\n")
        handle.write("- Details: excluded-history-symbols.csv\n")
    return manifest


@dataclass(frozen=True)
class HistoryTask:
    task_key: str
    task_sequence: int
    symbol: str
    interval: str
    mode: str
    full_range: bool
    request_start_epoch: int | None
    request_end_epoch: int
    prior_latest_epoch: int | None
    prior_full_refresh_required: bool = False


@dataclass
class HistoryHttpResult:
    body: bytes | None
    http_status: int | None
    content_type: str
    final_url_redacted: str
    requested_at_utc: str
    response_received_at_utc: str
    elapsed_ms: int
    attempts: list[dict[str, Any]]
    error_message: str | None
    session_generation: int | None


@dataclass(frozen=True)
class BarRecord:
    timestamp_utc: int
    datetime_utc: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    adjclose: float | None
    volume: int | None


@dataclass(frozen=True)
class EventRecord:
    event_type: str
    event_timestamp_utc: int
    event_key: str
    event_json: str


@dataclass
class ParsedHistory:
    classification: str
    returned_symbol: str | None
    bars: list[BarRecord]
    events: list[EventRecord]
    meta: dict[str, Any]
    error_code: str | None = None
    error_description: str | None = None


@dataclass
class ApplyStats:
    bars_returned: int = 0
    new_bars: int = 0
    revised_bars: int = 0
    unchanged_bars: int = 0
    missing_bars: int = 0
    events_returned: int = 0
    new_events: int = 0
    revised_events: int = 0
    unchanged_events: int = 0
    full_refresh_required: bool = False
    full_refresh_reason: str = ""


@dataclass
class SymbolResult:
    task_key: str
    task_sequence: int
    symbol: str
    interval: str
    mode: str
    full_range: bool
    request_start_epoch: int | None
    request_end_epoch: int
    classification: str
    http_status: int | None
    returned_symbol: str | None
    bars_returned: int
    new_bars: int
    revised_bars: int
    unchanged_bars: int
    missing_bars: int
    events_returned: int
    new_events: int
    revised_events: int
    unchanged_events: int
    full_refresh_required: bool
    full_refresh_reason: str
    raw_file: str | None
    raw_uncompressed_sha256: str | None
    raw_compressed_sha256: str | None
    raw_uncompressed_bytes: int
    raw_compressed_bytes: int
    metadata_file: str
    elapsed_ms: int
    attempts: int
    error_description: str | None = None


@dataclass
class RunState:
    output_root: Path
    run_dir: Path
    database_path: Path
    checkpoint_path: Path
    checkpoint_lock: threading.Lock = field(default_factory=threading.Lock)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def filename_utc(value: datetime) -> str:
    return format_utc(value).replace(":", "-")


def normalize_path(path: Path, *, relative_to: Path | None = None) -> Path:
    expanded = Path(os.path.expandvars(str(path))).expanduser()
    if not expanded.is_absolute() and relative_to is not None:
        expanded = relative_to / expanded
    return expanded.resolve(strict=False)


def path_is_within(path: Path, parent: Path) -> bool:
    try:
        normalize_path(path).relative_to(normalize_path(parent))
        return True
    except ValueError:
        return False


def validate_external_root(path: Path) -> Path:
    resolved = normalize_path(path)
    if path_is_within(resolved, REPOSITORY_ROOT):
        raise HistoryInputError(
            "Long-history raw captures and SQLite archives must be stored outside "
            f"the synchronized repository. Choose an external root instead of: {resolved}"
        )
    return resolved


def prepare_output_root(path: Path) -> Path:
    resolved = validate_external_root(path)
    try:
        resolved.mkdir(parents=True, exist_ok=True)
        (resolved / "runs").mkdir(parents=True, exist_ok=True)
        probe = resolved / f".history-write-test-{os.getpid()}-{threading.get_ident()}"
        probe.write_text("write-test\n", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise HistoryInputError(f"Long-history output root is not writable: {resolved}: {exc}") from exc
    return resolved


def load_local_output_root(config_path: Path) -> Path | None:
    resolved = normalize_path(config_path)
    if not resolved.exists():
        return None
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HistoryInputError(f"Cannot read local history config {resolved}: {exc}") from exc
    value = payload.get("output_root") if isinstance(payload, Mapping) else None
    if not isinstance(value, str) or not value.strip():
        raise HistoryInputError(f"Local history config is missing a non-empty output_root: {resolved}")
    return normalize_path(Path(value.strip()), relative_to=resolved.parent)


def resolve_output_root(
    cli_output_root: Path | None,
    *,
    config_path: Path = LOCAL_CONFIG_FILE,
    environment: Mapping[str, str] | None = None,
) -> tuple[Path, str]:
    if cli_output_root is not None:
        return validate_external_root(cli_output_root), "command_line"
    env = os.environ if environment is None else environment
    environment_value = env.get(OUTPUT_ROOT_ENVIRONMENT_VARIABLE, "").strip()
    if environment_value:
        return validate_external_root(Path(environment_value)), "environment"
    configured = load_local_output_root(config_path)
    if configured is not None:
        return validate_external_root(configured), "local_config"
    return validate_external_root(DEFAULT_EXTERNAL_ROOT), "safe_default"


def write_local_output_config(config_path: Path, output_root: Path) -> Path:
    resolved_config = normalize_path(config_path)
    resolved_output = validate_external_root(output_root)
    resolved_config.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "output_root": str(resolved_output),
        "purpose": "Machine-local long-history archive storage; do not commit this file.",
    }
    resolved_config.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return resolved_config


def unique_symbols_from_input(path: Path) -> list[str]:
    rows = fast.load_input_rows(path)
    seen: set[str] = set()
    symbols: list[str] = []
    for row in rows:
        if row.symbol not in seen:
            seen.add(row.symbol)
            symbols.append(row.symbol)
    if not symbols:
        raise HistoryInputError("Input CSV contains no unique symbols.")
    return symbols


def safe_filename(value: str) -> str:
    return fast.safe_filename(value)


def redact_url(url: str) -> str:
    return fast.redact_url(url)


def epoch_to_utc_text(value: int) -> str:
    """Convert Unix seconds without relying on the Windows C runtime.

    ``datetime.fromtimestamp`` can raise ``OSError(22)`` on Windows for
    legitimate pre-1970 market timestamps.  Epoch arithmetic is portable for
    the historical range this archive supports.
    """
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    try:
        return (epoch + timedelta(seconds=int(value))).isoformat().replace("+00:00", "Z")
    except (OverflowError, ValueError) as exc:
        raise HistoryInputError(f"History timestamp is outside the supported range: {value}") from exc


def parse_through_date(value: str | None, *, now: Callable[[], datetime] = utc_now) -> int:
    """Return an exclusive UTC period2 epoch.

    A date argument means midnight immediately after that date. Without an
    argument, use tomorrow UTC so the latest daily bar can be returned.
    """
    if value:
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise HistoryInputError("--through-date must use YYYY-MM-DD.") from exc
        return int(datetime.combine(parsed + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc).timestamp())
    current = now().astimezone(timezone.utc)
    tomorrow = (current + timedelta(days=1)).date()
    return int(datetime.combine(tomorrow, datetime.min.time(), tzinfo=timezone.utc).timestamp())


def build_chart_url(task: HistoryTask, snapshot: Any) -> str:
    query: dict[str, str] = {
        "interval": task.interval,
        "includePrePost": "false",
        "events": "div,splits,capitalGains",
        "includeAdjustedClose": "true",
        "crumb": snapshot.crumb,
    }
    if task.full_range:
        # ``range=max`` can cause Yahoo to silently return coarser bars even
        # when ``interval=1d`` is requested.  Explicit bounds preserve the
        # requested granularity and also include pre-1970 history.
        query["period1"] = str(BASELINE_START_EPOCH)
        query["period2"] = str(task.request_end_epoch)
    else:
        if task.request_start_epoch is None:
            raise HistoryInputError("Incremental history task is missing request_start_epoch.")
        query["period1"] = str(task.request_start_epoch)
        query["period2"] = str(task.request_end_epoch)
    return f"{CHART_BASE_URL}/{quote(task.symbol, safe='')}?{urlencode(query)}"


def _content_type(headers: Any) -> str:
    try:
        return headers.get("Content-Type", "") or "" if headers is not None else ""
    except Exception:
        return ""


def request_history_with_retry(
    task: HistoryTask,
    *,
    session: Any,
    timeout_seconds: float,
    maximum_attempts: int,
    backoff_seconds: Sequence[float],
    user_agent: str,
    gate: Any,
    opener: Callable[..., Any] = urlopen,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = utc_now,
) -> HistoryHttpResult:
    attempts: list[dict[str, Any]] = []
    final_body: bytes | None = None
    final_status: int | None = None
    final_content_type = ""
    final_url_redacted = ""
    final_error: str | None = None
    final_generation: int | None = None
    overall_started = clock()
    auth_refresh_attempted = False

    for attempt_number in range(1, maximum_attempts + 1):
        gate.wait()
        snapshot = session.snapshot()
        final_generation = snapshot.generation
        url = build_chart_url(task, snapshot)
        final_url_redacted = redact_url(url)
        headers = {
            "User-Agent": user_agent,
            "Accept": "application/json,text/plain,*/*",
            "Connection": "keep-alive",
        }
        if snapshot.cookie_header:
            headers["Cookie"] = snapshot.cookie_header
        request = Request(url, headers=headers)
        started_at = now()
        started_clock = clock()
        body: bytes | None = None
        status: int | None = None
        content_type = ""
        error: str | None = None
        try:
            with opener(request, timeout=timeout_seconds) as response:
                status = int(getattr(response, "status", response.getcode()))
                body = response.read()
                content_type = _content_type(getattr(response, "headers", None))
        except HTTPError as exc:
            status = int(exc.code)
            body = exc.read() if hasattr(exc, "read") else b""
            content_type = _content_type(getattr(exc, "headers", None))
            error = f"HTTP {status}: {exc.reason}"
        except (URLError, TimeoutError, socket.timeout, OSError) as exc:
            error = f"{type(exc).__name__}: {exc}"
        ended_at = now()
        elapsed_ms = max(0, int(round((clock() - started_clock) * 1000)))
        attempts.append(
            {
                "attempt": attempt_number,
                "requested_at_utc": format_utc(started_at),
                "response_received_at_utc": format_utc(ended_at),
                "elapsed_ms": elapsed_ms,
                "http_status": status,
                "error": error,
            }
        )
        final_body = body
        final_status = status
        final_content_type = content_type
        final_error = error

        if status is not None and 200 <= status < 300:
            gate.on_success()
            break
        if status in AUTH_REFRESH_HTTP_STATUSES and not auth_refresh_attempted:
            auth_refresh_attempted = True
            session.refresh_after_auth_error(snapshot.generation)
            if attempt_number < maximum_attempts:
                continue
        retryable = status in RETRYABLE_HTTP_STATUSES or status is None
        if status == 429:
            gate.on_throttle()
        if retryable and attempt_number < maximum_attempts:
            index = min(attempt_number - 1, len(backoff_seconds) - 1)
            delay = float(backoff_seconds[index]) if backoff_seconds else 0.0
            if delay > 0:
                sleep(delay)
            continue
        break

    elapsed_total = max(0, int(round((clock() - overall_started) * 1000)))
    return HistoryHttpResult(
        body=final_body,
        http_status=final_status,
        content_type=final_content_type,
        final_url_redacted=final_url_redacted,
        requested_at_utc=attempts[0]["requested_at_utc"] if attempts else format_utc(now()),
        response_received_at_utc=attempts[-1]["response_received_at_utc"] if attempts else format_utc(now()),
        elapsed_ms=elapsed_total,
        attempts=attempts,
        error_message=final_error,
        session_generation=final_generation,
    )


def _value_at(values: Any, index: int) -> Any:
    return values[index] if isinstance(values, list) and index < len(values) else None


def _float_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _error_parts(error_obj: Any) -> tuple[str | None, str | None]:
    if not isinstance(error_obj, Mapping):
        return None, None
    code = error_obj.get("code")
    description = error_obj.get("description")
    return (str(code) if code is not None else None, str(description) if description is not None else None)


def parse_chart_response(
    body: bytes | None,
    *,
    requested_symbol: str,
    requested_interval: str | None = None,
) -> ParsedHistory:
    if body is None:
        return ParsedHistory("NO_RESPONSE_BODY", None, [], [], {}, error_description="No response body captured.")
    try:
        payload = json.loads(body.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return ParsedHistory("JSON_PARSE_ERROR", None, [], [], {}, error_description=f"{type(exc).__name__}: {exc}")
    chart = payload.get("chart") if isinstance(payload, Mapping) else None
    if not isinstance(chart, Mapping):
        return ParsedHistory("UNEXPECTED_RESPONSE_SHAPE", None, [], [], {}, error_description="Missing chart object.")
    result = chart.get("result")
    code, description = _error_parts(chart.get("error"))
    if not isinstance(result, list) or not result:
        combined = " ".join(part for part in (code, description) if part)
        classification = "SYMBOL_NOT_AVAILABLE" if fast.NOT_FOUND_RE.search(combined) else "NO_HISTORY_DATA"
        return ParsedHistory(classification, None, [], [], {}, code, description)
    first = result[0]
    if not isinstance(first, Mapping):
        return ParsedHistory("UNEXPECTED_RESPONSE_SHAPE", None, [], [], {}, error_description="Chart result is not an object.")
    meta = dict(first.get("meta")) if isinstance(first.get("meta"), Mapping) else {}
    returned_symbol = str(meta.get("symbol")) if meta.get("symbol") is not None else requested_symbol
    returned_interval = meta.get("dataGranularity")
    if requested_interval is not None:
        if not isinstance(returned_interval, str) or returned_interval != requested_interval:
            return ParsedHistory(
                "UNEXPECTED_DATA_GRANULARITY",
                returned_symbol,
                [],
                [],
                meta,
                code,
                (
                    f"Requested interval {requested_interval!r} but Yahoo returned "
                    f"dataGranularity {returned_interval!r}."
                ),
            )
    timestamps = first.get("timestamp")
    timestamps = timestamps if isinstance(timestamps, list) else []
    indicators = first.get("indicators") if isinstance(first.get("indicators"), Mapping) else {}
    quote_sets = indicators.get("quote") if isinstance(indicators, Mapping) else None
    quote_set = quote_sets[0] if isinstance(quote_sets, list) and quote_sets and isinstance(quote_sets[0], Mapping) else {}
    adj_sets = indicators.get("adjclose") if isinstance(indicators, Mapping) else None
    adj_set = adj_sets[0] if isinstance(adj_sets, list) and adj_sets and isinstance(adj_sets[0], Mapping) else {}

    bars: list[BarRecord] = []
    seen_timestamps: set[int] = set()
    for index, raw_timestamp in enumerate(timestamps):
        timestamp = _int_or_none(raw_timestamp)
        if timestamp is None or timestamp in seen_timestamps:
            continue
        seen_timestamps.add(timestamp)
        bars.append(
            BarRecord(
                timestamp_utc=timestamp,
                datetime_utc=epoch_to_utc_text(timestamp),
                open=_float_or_none(_value_at(quote_set.get("open"), index)),
                high=_float_or_none(_value_at(quote_set.get("high"), index)),
                low=_float_or_none(_value_at(quote_set.get("low"), index)),
                close=_float_or_none(_value_at(quote_set.get("close"), index)),
                adjclose=_float_or_none(_value_at(adj_set.get("adjclose"), index)),
                volume=_int_or_none(_value_at(quote_set.get("volume"), index)),
            )
        )

    events: list[EventRecord] = []
    events_obj = first.get("events") if isinstance(first.get("events"), Mapping) else {}
    for yahoo_name, event_type in (("dividends", "DIVIDEND"), ("splits", "SPLIT"), ("capitalGains", "CAPITAL_GAIN")):
        container = events_obj.get(yahoo_name) if isinstance(events_obj, Mapping) else None
        if not isinstance(container, Mapping):
            continue
        for raw_key, event_payload in container.items():
            if not isinstance(event_payload, Mapping):
                continue
            event_timestamp = _int_or_none(event_payload.get("date"))
            if event_timestamp is None:
                event_timestamp = _int_or_none(raw_key)
            if event_timestamp is None:
                continue
            canonical = json.dumps(event_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            key_text = str(raw_key)
            event_key = f"{event_timestamp}:{key_text}:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:16]}"
            events.append(EventRecord(event_type, event_timestamp, event_key, canonical))

    classification = "SUCCESS_HISTORY_RETURNED" if bars or events else "NO_HISTORY_DATA"
    return ParsedHistory(classification, returned_symbol, bars, events, meta, code, description)


def connect_database(path: Path, *, create: bool = True) -> sqlite3.Connection:
    path = normalize_path(path)
    if not create and not path.exists():
        raise FileNotFoundError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


def initialize_database(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS archive_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            mode TEXT NOT NULL,
            interval TEXT NOT NULL,
            overlap_days INTEGER NOT NULL,
            started_at_utc TEXT NOT NULL,
            completed_at_utc TEXT,
            status TEXT NOT NULL,
            input_file_name TEXT NOT NULL,
            requested_symbols INTEGER NOT NULL,
            completed_symbols INTEGER NOT NULL DEFAULT 0,
            run_folder_name TEXT NOT NULL,
            utility_version TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS symbol_state (
            symbol TEXT NOT NULL,
            interval TEXT NOT NULL,
            last_bar_timestamp INTEGER,
            last_checked_at_utc TEXT,
            last_success_run_id TEXT,
            baseline_run_id TEXT,
            full_refresh_required INTEGER NOT NULL DEFAULT 0,
            full_refresh_reason TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (symbol, interval)
        );
        CREATE TABLE IF NOT EXISTS bars (
            symbol TEXT NOT NULL,
            interval TEXT NOT NULL,
            timestamp_utc INTEGER NOT NULL,
            datetime_utc TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            adjclose REAL,
            volume INTEGER,
            first_seen_run_id TEXT NOT NULL,
            last_seen_run_id TEXT NOT NULL,
            source_file TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            PRIMARY KEY (symbol, interval, timestamp_utc)
        );
        CREATE INDEX IF NOT EXISTS idx_bars_interval_timestamp
            ON bars(interval, timestamp_utc);
        CREATE TABLE IF NOT EXISTS bar_revisions (
            revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            interval TEXT NOT NULL,
            timestamp_utc INTEGER NOT NULL,
            detected_at_utc TEXT NOT NULL,
            action TEXT NOT NULL,
            changed_fields_json TEXT NOT NULL,
            old_values_json TEXT,
            new_values_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_bar_revisions_run
            ON bar_revisions(run_id, symbol);
        CREATE TABLE IF NOT EXISTS events (
            symbol TEXT NOT NULL,
            interval TEXT NOT NULL,
            event_type TEXT NOT NULL,
            event_timestamp_utc INTEGER NOT NULL,
            event_key TEXT NOT NULL,
            event_json TEXT NOT NULL,
            first_seen_run_id TEXT NOT NULL,
            last_seen_run_id TEXT NOT NULL,
            source_file TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            PRIMARY KEY (symbol, interval, event_type, event_key)
        );
        CREATE INDEX IF NOT EXISTS idx_events_symbol_timestamp
            ON events(symbol, interval, event_timestamp_utc);
        CREATE TABLE IF NOT EXISTS event_revisions (
            revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            interval TEXT NOT NULL,
            event_type TEXT NOT NULL,
            event_timestamp_utc INTEGER NOT NULL,
            detected_at_utc TEXT NOT NULL,
            action TEXT NOT NULL,
            old_event_json TEXT,
            new_event_json TEXT
        );
        CREATE TABLE IF NOT EXISTS symbol_runs (
            run_id TEXT NOT NULL,
            task_key TEXT NOT NULL,
            task_sequence INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            interval TEXT NOT NULL,
            mode TEXT NOT NULL,
            full_range INTEGER NOT NULL,
            request_start_epoch INTEGER,
            request_end_epoch INTEGER NOT NULL,
            classification TEXT NOT NULL,
            http_status INTEGER,
            bars_returned INTEGER NOT NULL,
            new_bars INTEGER NOT NULL,
            revised_bars INTEGER NOT NULL,
            unchanged_bars INTEGER NOT NULL,
            missing_bars INTEGER NOT NULL,
            events_returned INTEGER NOT NULL,
            new_events INTEGER NOT NULL,
            revised_events INTEGER NOT NULL,
            unchanged_events INTEGER NOT NULL,
            full_refresh_required INTEGER NOT NULL,
            full_refresh_reason TEXT NOT NULL,
            raw_file TEXT,
            raw_sha256 TEXT,
            elapsed_ms INTEGER NOT NULL,
            attempts INTEGER NOT NULL,
            error_description TEXT,
            PRIMARY KEY (run_id, task_key)
        );
        """
    )
    connection.execute(
        "INSERT OR REPLACE INTO archive_meta(key, value) VALUES('database_schema_version', ?)",
        (str(DATABASE_SCHEMA_VERSION),),
    )
    connection.commit()


def _row_values(bar: BarRecord | sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
    keys = ("open", "high", "low", "close", "adjclose", "volume")
    if isinstance(bar, BarRecord):
        return {key: getattr(bar, key) for key in keys}
    return {key: bar[key] for key in keys}


def _numbers_equal(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(float(left), float(right), rel_tol=1e-12, abs_tol=1e-12)
    return left == right


def _changed_fields(old_values: Mapping[str, Any], new_values: Mapping[str, Any]) -> list[str]:
    return [key for key in old_values if not _numbers_equal(old_values[key], new_values[key])]


def _record_bar_revision(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    symbol: str,
    interval: str,
    timestamp: int,
    action: str,
    changed_fields: Sequence[str],
    old_values: Mapping[str, Any] | None,
    new_values: Mapping[str, Any] | None,
    detected_at_utc: str,
) -> None:
    connection.execute(
        """
        INSERT INTO bar_revisions(
            run_id, symbol, interval, timestamp_utc, detected_at_utc, action,
            changed_fields_json, old_values_json, new_values_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            symbol,
            interval,
            timestamp,
            detected_at_utc,
            action,
            json.dumps(list(changed_fields), separators=(",", ":")),
            json.dumps(dict(old_values), sort_keys=True, separators=(",", ":")) if old_values is not None else None,
            json.dumps(dict(new_values), sort_keys=True, separators=(",", ":")) if new_values is not None else None,
        ),
    )


def apply_parsed_history(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    task: HistoryTask,
    parsed: ParsedHistory,
    source_file: str,
    source_sha256: str,
    detected_at_utc: str,
) -> ApplyStats:
    stats = ApplyStats(bars_returned=len(parsed.bars), events_returned=len(parsed.events))
    if parsed.classification != "SUCCESS_HISTORY_RETURNED":
        return stats

    existing_rows = {
        int(row["timestamp_utc"]): row
        for row in connection.execute(
            "SELECT * FROM bars WHERE symbol=? AND interval=?",
            (task.symbol, task.interval),
        )
    }
    returned_timestamps: set[int] = set()
    adjustment_revision = False

    for bar in parsed.bars:
        returned_timestamps.add(bar.timestamp_utc)
        existing = existing_rows.get(bar.timestamp_utc)
        new_values = _row_values(bar)
        if existing is None:
            connection.execute(
                """
                INSERT INTO bars(
                    symbol, interval, timestamp_utc, datetime_utc, open, high, low,
                    close, adjclose, volume, first_seen_run_id, last_seen_run_id,
                    source_file, source_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.symbol,
                    task.interval,
                    bar.timestamp_utc,
                    bar.datetime_utc,
                    bar.open,
                    bar.high,
                    bar.low,
                    bar.close,
                    bar.adjclose,
                    bar.volume,
                    run_id,
                    run_id,
                    source_file,
                    source_sha256,
                ),
            )
            stats.new_bars += 1
            continue
        old_values = _row_values(existing)
        changed = _changed_fields(old_values, new_values)
        if changed:
            _record_bar_revision(
                connection,
                run_id=run_id,
                symbol=task.symbol,
                interval=task.interval,
                timestamp=bar.timestamp_utc,
                action="REVISED",
                changed_fields=changed,
                old_values=old_values,
                new_values=new_values,
                detected_at_utc=detected_at_utc,
            )
            connection.execute(
                """
                UPDATE bars
                   SET datetime_utc=?, open=?, high=?, low=?, close=?, adjclose=?,
                       volume=?, last_seen_run_id=?, source_file=?, source_sha256=?
                 WHERE symbol=? AND interval=? AND timestamp_utc=?
                """,
                (
                    bar.datetime_utc,
                    bar.open,
                    bar.high,
                    bar.low,
                    bar.close,
                    bar.adjclose,
                    bar.volume,
                    run_id,
                    source_file,
                    source_sha256,
                    task.symbol,
                    task.interval,
                    bar.timestamp_utc,
                ),
            )
            stats.revised_bars += 1
            adjustment_revision = adjustment_revision or "adjclose" in changed
        else:
            connection.execute(
                """
                UPDATE bars SET last_seen_run_id=?, source_file=?, source_sha256=?
                 WHERE symbol=? AND interval=? AND timestamp_utc=?
                """,
                (run_id, source_file, source_sha256, task.symbol, task.interval, bar.timestamp_utc),
            )
            stats.unchanged_bars += 1

    if parsed.bars:
        coverage_start = min(bar.timestamp_utc for bar in parsed.bars)
        coverage_end = max(bar.timestamp_utc for bar in parsed.bars)
        if task.full_range:
            missing_candidates = [
                timestamp for timestamp in existing_rows if timestamp not in returned_timestamps
            ]
        else:
            missing_candidates = [
                timestamp
                for timestamp in existing_rows
                if coverage_start <= timestamp <= coverage_end and timestamp not in returned_timestamps
            ]
        for timestamp in sorted(missing_candidates):
            old_values = _row_values(existing_rows[timestamp])
            _record_bar_revision(
                connection,
                run_id=run_id,
                symbol=task.symbol,
                interval=task.interval,
                timestamp=timestamp,
                action="MISSING_FROM_REFRESH",
                changed_fields=(),
                old_values=old_values,
                new_values=None,
                detected_at_utc=detected_at_utc,
            )
        stats.missing_bars = len(missing_candidates)

    event_change = False
    for event in parsed.events:
        existing = connection.execute(
            """
            SELECT * FROM events
             WHERE symbol=? AND interval=? AND event_type=? AND event_key=?
            """,
            (task.symbol, task.interval, event.event_type, event.event_key),
        ).fetchone()
        if existing is None:
            # A Yahoo event key can change while the event timestamp/type remains
            # the same. Treat that as a revision rather than a second event.
            same_slot = connection.execute(
                """
                SELECT * FROM events
                 WHERE symbol=? AND interval=? AND event_type=? AND event_timestamp_utc=?
                 ORDER BY event_key LIMIT 1
                """,
                (task.symbol, task.interval, event.event_type, event.event_timestamp_utc),
            ).fetchone()
            if same_slot is not None and same_slot["event_json"] != event.event_json:
                connection.execute(
                    """
                    INSERT INTO event_revisions(
                        run_id, symbol, interval, event_type, event_timestamp_utc,
                        detected_at_utc, action, old_event_json, new_event_json
                    ) VALUES (?, ?, ?, ?, ?, ?, 'REVISED', ?, ?)
                    """,
                    (
                        run_id,
                        task.symbol,
                        task.interval,
                        event.event_type,
                        event.event_timestamp_utc,
                        detected_at_utc,
                        same_slot["event_json"],
                        event.event_json,
                    ),
                )
                connection.execute(
                    """
                    DELETE FROM events
                     WHERE symbol=? AND interval=? AND event_type=? AND event_key=?
                    """,
                    (task.symbol, task.interval, event.event_type, same_slot["event_key"]),
                )
                connection.execute(
                    """
                    INSERT INTO events(
                        symbol, interval, event_type, event_timestamp_utc, event_key,
                        event_json, first_seen_run_id, last_seen_run_id, source_file,
                        source_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task.symbol,
                        task.interval,
                        event.event_type,
                        event.event_timestamp_utc,
                        event.event_key,
                        event.event_json,
                        same_slot["first_seen_run_id"],
                        run_id,
                        source_file,
                        source_sha256,
                    ),
                )
                stats.revised_events += 1
            else:
                connection.execute(
                    """
                    INSERT INTO events(
                        symbol, interval, event_type, event_timestamp_utc, event_key,
                        event_json, first_seen_run_id, last_seen_run_id, source_file,
                        source_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task.symbol,
                        task.interval,
                        event.event_type,
                        event.event_timestamp_utc,
                        event.event_key,
                        event.event_json,
                        run_id,
                        run_id,
                        source_file,
                        source_sha256,
                    ),
                )
                stats.new_events += 1
            event_change = True
        elif existing["event_json"] != event.event_json:
            connection.execute(
                """
                INSERT INTO event_revisions(
                    run_id, symbol, interval, event_type, event_timestamp_utc,
                    detected_at_utc, action, old_event_json, new_event_json
                ) VALUES (?, ?, ?, ?, ?, ?, 'REVISED', ?, ?)
                """,
                (
                    run_id,
                    task.symbol,
                    task.interval,
                    event.event_type,
                    event.event_timestamp_utc,
                    detected_at_utc,
                    existing["event_json"],
                    event.event_json,
                ),
            )
            connection.execute(
                """
                UPDATE events SET event_json=?, last_seen_run_id=?, source_file=?, source_sha256=?
                 WHERE symbol=? AND interval=? AND event_type=? AND event_key=?
                """,
                (
                    event.event_json,
                    run_id,
                    source_file,
                    source_sha256,
                    task.symbol,
                    task.interval,
                    event.event_type,
                    event.event_key,
                ),
            )
            stats.revised_events += 1
            event_change = True
        else:
            connection.execute(
                """
                UPDATE events SET last_seen_run_id=?, source_file=?, source_sha256=?
                 WHERE symbol=? AND interval=? AND event_type=? AND event_key=?
                """,
                (run_id, source_file, source_sha256, task.symbol, task.interval, event.event_type, event.event_key),
            )
            stats.unchanged_events += 1

    reasons: list[str] = []
    if task.mode == "sync" and event_change:
        reasons.append("CORPORATE_ACTION_CHANGE")
    if task.mode == "sync" and adjustment_revision:
        reasons.append("ADJUSTED_HISTORY_CHANGE")
    if stats.missing_bars:
        reasons.append("BAR_MISSING_FROM_REFRESH")
    stats.full_refresh_required = bool(reasons)
    stats.full_refresh_reason = ",".join(reasons)
    return stats


def create_run_state(output_root: Path, *, started_at: datetime | None = None, resume_run: Path | None = None) -> RunState:
    output_root = prepare_output_root(output_root)
    database_path = output_root / DATABASE_FILENAME
    if resume_run is not None:
        run_dir = validate_resume_run(resume_run)
    else:
        started_at = started_at or utc_now()
        run_dir = output_root / "runs" / f"{filename_utc(started_at)}_history-run"
        suffix = 1
        candidate = run_dir
        while candidate.exists():
            candidate = Path(f"{run_dir}-{suffix}")
            suffix += 1
        run_dir = candidate
        run_dir.mkdir(parents=True, exist_ok=False)
    checkpoint = run_dir / "checkpoint.jsonl"
    return RunState(output_root, run_dir, database_path, checkpoint)


def validate_resume_run(path: Path) -> Path:
    resolved = validate_external_root(path)
    if not resolved.is_dir():
        raise HistoryInputError(f"Resume run folder does not exist: {resolved}")
    if not (resolved / "checkpoint.jsonl").is_file():
        raise HistoryInputError(f"Resume run folder has no checkpoint.jsonl: {resolved}")
    return resolved


def load_completed_task_keys(checkpoint_path: Path) -> set[str]:
    if not checkpoint_path.exists():
        return set()
    completed: set[str] = set()
    for line_number, line in enumerate(checkpoint_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise HistoryInputError(f"Invalid checkpoint JSON at line {line_number}: {exc}") from exc
        task_key = payload.get("task_key") if isinstance(payload, Mapping) else None
        if isinstance(task_key, str):
            completed.add(task_key)
    return completed


def append_checkpoint(run_state: RunState, result: SymbolResult) -> None:
    payload = {
        "task_key": result.task_key,
        "symbol": result.symbol,
        "classification": result.classification,
        "http_status": result.http_status,
        "raw_file": result.raw_file,
        "completed_at_utc": format_utc(utc_now()),
    }
    with run_state.checkpoint_lock:
        with run_state.checkpoint_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")


def task_plan_sha256(tasks: Sequence[HistoryTask]) -> str:
    payload = [asdict(task) for task in tasks]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def write_or_validate_run_plan(
    run_state: RunState,
    *,
    tasks: Sequence[HistoryTask],
    input_file: Path,
    mode: str,
    interval: str,
    overlap_days: int,
    started_at: datetime,
    resumed: bool,
) -> dict[str, Any]:
    plan_path = run_state.run_dir / "run-plan.json"
    expected = {
        "schema_version": CAPTURE_SCHEMA_VERSION,
        "utility_version": UTILITY_VERSION,
        "input_file_name": input_file.name,
        "mode": mode,
        "interval": interval,
        "overlap_days": overlap_days,
        "task_count": len(tasks),
        "task_plan_sha256": task_plan_sha256(tasks),
        "run_folder_name": run_state.run_dir.name,
        "started_at_utc": format_utc(started_at),
        "absolute_local_path_persisted": False,
    }
    if resumed:
        if not plan_path.is_file():
            raise HistoryInputError(f"Resume run folder has no run-plan.json: {run_state.run_dir}")
        try:
            existing = json.loads(plan_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HistoryInputError(f"Cannot read resume run plan {plan_path}: {exc}") from exc
        comparison_keys = (
            "input_file_name", "mode", "interval", "overlap_days",
            "task_count", "task_plan_sha256",
        )
        mismatches = [key for key in comparison_keys if existing.get(key) != expected.get(key)]
        if mismatches:
            raise HistoryInputError(
                "Resume settings do not match the original run plan: " + ", ".join(mismatches)
            )
        return existing
    plan_path.write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return expected


def state_for_symbols(database_path: Path, symbols: Sequence[str], interval: str) -> dict[str, sqlite3.Row]:
    if not database_path.exists() or not symbols:
        return {}
    connection = connect_database(database_path, create=False)
    try:
        placeholders = ",".join("?" for _ in symbols)
        rows = connection.execute(
            f"SELECT * FROM symbol_state WHERE interval=? AND symbol IN ({placeholders})",
            (interval, *symbols),
        )
        return {str(row["symbol"]): row for row in rows}
    finally:
        connection.close()


def build_tasks(
    symbols: Sequence[str],
    *,
    mode: str,
    interval: str,
    overlap_days: int,
    request_end_epoch: int,
    database_path: Path,
) -> list[HistoryTask]:
    if mode not in VALID_MODES:
        raise HistoryInputError(f"Unsupported history mode: {mode}")
    if interval not in VALID_INTERVALS:
        raise HistoryInputError(f"Unsupported history interval: {interval}")
    if overlap_days < 1:
        raise HistoryInputError("overlap_days must be at least 1.")
    states = state_for_symbols(database_path, symbols, interval)
    selected_symbols = list(symbols)
    if mode == "refresh-flagged":
        selected_symbols = [
            symbol for symbol in symbols
            if symbol in states and bool(states[symbol]["full_refresh_required"])
        ]
    tasks: list[HistoryTask] = []
    for sequence, symbol in enumerate(selected_symbols, start=1):
        state = states.get(symbol)
        prior_latest = int(state["last_bar_timestamp"]) if state and state["last_bar_timestamp"] is not None else None
        prior_flag = bool(state["full_refresh_required"]) if state else False
        full_range = mode in {"baseline", "refresh-flagged"} or prior_latest is None
        start_epoch = None if full_range else max(0, prior_latest - overlap_days * 86400)
        effective_mode = "baseline-fallback" if mode == "sync" and prior_latest is None else mode
        tasks.append(
            HistoryTask(
                task_key=f"history-{sequence:06d}-{safe_filename(symbol)}",
                task_sequence=sequence,
                symbol=symbol,
                interval=interval,
                mode=effective_mode,
                full_range=full_range,
                request_start_epoch=start_epoch,
                request_end_epoch=request_end_epoch,
                prior_latest_epoch=prior_latest,
                prior_full_refresh_required=prior_flag,
            )
        )
    return tasks


def write_raw_and_metadata(
    run_state: RunState,
    task: HistoryTask,
    http: HistoryHttpResult,
) -> tuple[str | None, str | None, str | None, int, int, str]:
    raw_relative: str | None = None
    raw_sha: str | None = None
    compressed_sha: str | None = None
    raw_size = 0
    compressed_size = 0
    if http.body is not None:
        raw_relative = f"raw/chart/{task.task_sequence:06d}_{safe_filename(task.symbol)}.json.gz"
        raw_path = run_state.run_dir / raw_relative
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        compressed = gzip.compress(http.body, compresslevel=9, mtime=0)
        raw_path.write_bytes(compressed)
        raw_sha = hashlib.sha256(http.body).hexdigest()
        compressed_sha = hashlib.sha256(compressed).hexdigest()
        raw_size = len(http.body)
        compressed_size = len(compressed)

    metadata_relative = f"metadata/chart/{task.task_sequence:06d}_{safe_filename(task.symbol)}.metadata.json"
    metadata_path = run_state.run_dir / metadata_relative
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "schema_version": CAPTURE_SCHEMA_VERSION,
        "utility_version": UTILITY_VERSION,
        "task": asdict(task),
        "http": {
            "http_status": http.http_status,
            "content_type": http.content_type,
            "final_url_redacted": http.final_url_redacted,
            "requested_at_utc": http.requested_at_utc,
            "response_received_at_utc": http.response_received_at_utc,
            "elapsed_ms": http.elapsed_ms,
            "attempts": http.attempts,
            "error_message": http.error_message,
            "session_generation": http.session_generation,
        },
        "raw": {
            "file": raw_relative,
            "uncompressed_sha256": raw_sha,
            "compressed_sha256": compressed_sha,
            "uncompressed_bytes": raw_size,
            "compressed_bytes": compressed_size,
        },
        "sensitive_values_persisted": False,
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return raw_relative, raw_sha, compressed_sha, raw_size, compressed_size, metadata_relative


def process_symbol_result(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    run_state: RunState,
    task: HistoryTask,
    http: HistoryHttpResult,
) -> SymbolResult:
    raw_file, raw_sha, compressed_sha, raw_bytes, compressed_bytes, metadata_file = write_raw_and_metadata(
        run_state, task, http
    )
    if http.http_status is None:
        classification = "NETWORK_OR_TIMEOUT_ERROR"
        parsed = ParsedHistory(classification, None, [], [], {}, error_description=http.error_message)
    elif not 200 <= http.http_status < 300:
        classification = f"HTTP_ERROR_{http.http_status}"
        parsed = ParsedHistory(classification, None, [], [], {}, error_description=http.error_message)
    else:
        parsed = parse_chart_response(http.body, requested_symbol=task.symbol, requested_interval=task.interval)
        classification = parsed.classification

    detected_at = format_utc(utc_now())
    stats = ApplyStats()
    with connection:
        if raw_file and raw_sha:
            stats = apply_parsed_history(
                connection,
                run_id=run_id,
                task=task,
                parsed=parsed,
                source_file=raw_file,
                source_sha256=raw_sha,
                detected_at_utc=detected_at,
            )
        previous_state = connection.execute(
            "SELECT * FROM symbol_state WHERE symbol=? AND interval=?",
            (task.symbol, task.interval),
        ).fetchone()
        current_latest = connection.execute(
            "SELECT MAX(timestamp_utc) AS latest FROM bars WHERE symbol=? AND interval=?",
            (task.symbol, task.interval),
        ).fetchone()["latest"]
        success = classification in {"SUCCESS_HISTORY_RETURNED", "NO_HISTORY_DATA", "SYMBOL_NOT_AVAILABLE"}
        existing_flag = bool(previous_state["full_refresh_required"]) if previous_state else False
        existing_reason = str(previous_state["full_refresh_reason"]) if previous_state else ""
        if task.mode == "refresh-flagged" and success:
            final_flag = stats.full_refresh_required
            final_reason = stats.full_refresh_reason
        else:
            final_flag = existing_flag or stats.full_refresh_required
            reasons = [part for part in (existing_reason, stats.full_refresh_reason) if part]
            final_reason = ",".join(dict.fromkeys(",".join(reasons).split(","))) if reasons else ""
        baseline_run_id = previous_state["baseline_run_id"] if previous_state else None
        if baseline_run_id is None and task.full_range and success:
            baseline_run_id = run_id
        connection.execute(
            """
            INSERT INTO symbol_state(
                symbol, interval, last_bar_timestamp, last_checked_at_utc,
                last_success_run_id, baseline_run_id, full_refresh_required,
                full_refresh_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, interval) DO UPDATE SET
                last_bar_timestamp=excluded.last_bar_timestamp,
                last_checked_at_utc=excluded.last_checked_at_utc,
                last_success_run_id=CASE WHEN ? THEN excluded.last_success_run_id ELSE symbol_state.last_success_run_id END,
                baseline_run_id=COALESCE(symbol_state.baseline_run_id, excluded.baseline_run_id),
                full_refresh_required=excluded.full_refresh_required,
                full_refresh_reason=excluded.full_refresh_reason
            """,
            (
                task.symbol,
                task.interval,
                current_latest,
                detected_at,
                run_id if success else None,
                baseline_run_id,
                int(final_flag),
                final_reason,
                int(success),
            ),
        )
        connection.execute(
            """
            INSERT OR REPLACE INTO symbol_runs(
                run_id, task_key, task_sequence, symbol, interval, mode, full_range,
                request_start_epoch, request_end_epoch, classification, http_status,
                bars_returned, new_bars, revised_bars, unchanged_bars, missing_bars,
                events_returned, new_events, revised_events, unchanged_events,
                full_refresh_required, full_refresh_reason, raw_file, raw_sha256,
                elapsed_ms, attempts, error_description
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                task.task_key,
                task.task_sequence,
                task.symbol,
                task.interval,
                task.mode,
                int(task.full_range),
                task.request_start_epoch,
                task.request_end_epoch,
                classification,
                http.http_status,
                stats.bars_returned,
                stats.new_bars,
                stats.revised_bars,
                stats.unchanged_bars,
                stats.missing_bars,
                stats.events_returned,
                stats.new_events,
                stats.revised_events,
                stats.unchanged_events,
                int(final_flag),
                final_reason,
                raw_file,
                raw_sha,
                http.elapsed_ms,
                len(http.attempts),
                parsed.error_description or http.error_message,
            ),
        )

    return SymbolResult(
        task_key=task.task_key,
        task_sequence=task.task_sequence,
        symbol=task.symbol,
        interval=task.interval,
        mode=task.mode,
        full_range=task.full_range,
        request_start_epoch=task.request_start_epoch,
        request_end_epoch=task.request_end_epoch,
        classification=classification,
        http_status=http.http_status,
        returned_symbol=parsed.returned_symbol,
        bars_returned=stats.bars_returned,
        new_bars=stats.new_bars,
        revised_bars=stats.revised_bars,
        unchanged_bars=stats.unchanged_bars,
        missing_bars=stats.missing_bars,
        events_returned=stats.events_returned,
        new_events=stats.new_events,
        revised_events=stats.revised_events,
        unchanged_events=stats.unchanged_events,
        full_refresh_required=bool(final_flag),
        full_refresh_reason=final_reason,
        raw_file=raw_file,
        raw_uncompressed_sha256=raw_sha,
        raw_compressed_sha256=compressed_sha,
        raw_uncompressed_bytes=raw_bytes,
        raw_compressed_bytes=compressed_bytes,
        metadata_file=metadata_file,
        elapsed_ms=http.elapsed_ms,
        attempts=len(http.attempts),
        error_description=parsed.error_description or http.error_message,
    )


def write_symbol_results_csv(run_dir: Path, results: Sequence[SymbolResult]) -> Path:
    path = run_dir / "summary" / "symbol-results.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(asdict(results[0]).keys()) if results else [
        "task_key", "task_sequence", "symbol", "interval", "mode", "classification"
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for result in sorted(results, key=lambda item: item.task_sequence):
            row = asdict(result)
            writer.writerow(row)
    return path


def write_revision_exports(connection: sqlite3.Connection, run_dir: Path, run_id: str) -> tuple[Path, Path]:
    summary_dir = run_dir / "summary"
    summary_dir.mkdir(parents=True, exist_ok=True)
    bar_path = summary_dir / "bar-revisions.csv"
    event_path = summary_dir / "event-revisions.csv"
    bar_rows = connection.execute(
        "SELECT * FROM bar_revisions WHERE run_id=? ORDER BY symbol, timestamp_utc, revision_id",
        (run_id,),
    ).fetchall()
    event_rows = connection.execute(
        "SELECT * FROM event_revisions WHERE run_id=? ORDER BY symbol, event_timestamp_utc, revision_id",
        (run_id,),
    ).fetchall()
    for path, rows in ((bar_path, bar_rows), (event_path, event_rows)):
        fieldnames = list(rows[0].keys()) if rows else ["run_id"]
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(dict(row))
    return bar_path, event_path


def write_manifest_and_summary(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    run_state: RunState,
    results: Sequence[SymbolResult],
    input_file: Path,
    mode: str,
    interval: str,
    overlap_days: int,
    started_at: datetime,
    completed_at: datetime,
    elapsed_seconds: float,
    output_root_source: str,
    session_summary: Mapping[str, Any],
    resumed: bool,
) -> dict[str, Any]:
    classifications = Counter(result.classification for result in results)
    totals = {
        "symbols_completed": len(results),
        "bars_returned": sum(result.bars_returned for result in results),
        "new_bars": sum(result.new_bars for result in results),
        "revised_bars": sum(result.revised_bars for result in results),
        "unchanged_bars": sum(result.unchanged_bars for result in results),
        "missing_bars": sum(result.missing_bars for result in results),
        "events_returned": sum(result.events_returned for result in results),
        "new_events": sum(result.new_events for result in results),
        "revised_events": sum(result.revised_events for result in results),
        "unchanged_events": sum(result.unchanged_events for result in results),
        "symbols_flagged_for_full_refresh": sum(result.full_refresh_required for result in results),
        "raw_uncompressed_bytes": sum(result.raw_uncompressed_bytes for result in results),
        "raw_compressed_bytes": sum(result.raw_compressed_bytes for result in results),
        "http_attempts": sum(result.attempts for result in results),
    }
    database_counts = {
        "bars": connection.execute("SELECT COUNT(*) FROM bars").fetchone()[0],
        "events": connection.execute("SELECT COUNT(*) FROM events").fetchone()[0],
        "bar_revisions": connection.execute("SELECT COUNT(*) FROM bar_revisions").fetchone()[0],
        "event_revisions": connection.execute("SELECT COUNT(*) FROM event_revisions").fetchone()[0],
        "symbols": connection.execute("SELECT COUNT(*) FROM symbol_state").fetchone()[0],
        "symbols_flagged": connection.execute(
            "SELECT COUNT(*) FROM symbol_state WHERE full_refresh_required=1"
        ).fetchone()[0],
    }
    manifest = {
        "schema_version": CAPTURE_SCHEMA_VERSION,
        "database_schema_version": DATABASE_SCHEMA_VERSION,
        "utility_version": UTILITY_VERSION,
        "run_id": run_id,
        "mode": mode,
        "interval": interval,
        "overlap_days": overlap_days,
        "started_at_utc": format_utc(started_at),
        "completed_at_utc": format_utc(completed_at),
        "elapsed_seconds": round(elapsed_seconds, 3),
        "input_file_name": input_file.name,
        "resumed": resumed,
        "storage": {
            "policy": "external_compressed_raw_and_sqlite",
            "output_root_source": output_root_source,
            "run_folder_name": run_state.run_dir.name,
            "database_file_name": run_state.database_path.name,
            "repository_output_allowed": False,
            "absolute_local_path_persisted": False,
        },
        "classifications": dict(sorted(classifications.items())),
        "totals": totals,
        "database_counts": database_counts,
        "session": dict(session_summary),
        "sensitive_values_persisted": False,
    }
    (run_state.run_dir / "run-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = [
        "Yahoo Finance Long-History Run Summary",
        f"Utility version              : {UTILITY_VERSION}",
        f"Run ID                       : {run_id}",
        f"Mode                         : {mode}",
        f"Interval                     : {interval}",
        f"Symbols completed            : {totals['symbols_completed']}",
        f"Bars returned                : {totals['bars_returned']}",
        f"New bars                     : {totals['new_bars']}",
        f"Revised bars                 : {totals['revised_bars']}",
        f"Unchanged bars               : {totals['unchanged_bars']}",
        f"Missing-from-refresh bars    : {totals['missing_bars']}",
        f"New events                   : {totals['new_events']}",
        f"Revised events               : {totals['revised_events']}",
        f"Symbols flagged full refresh : {totals['symbols_flagged_for_full_refresh']}",
        f"Raw bytes                    : {totals['raw_uncompressed_bytes']}",
        f"Compressed bytes             : {totals['raw_compressed_bytes']}",
        f"Elapsed seconds              : {elapsed_seconds:.3f}",
        "",
        "Classifications:",
    ]
    lines.extend(f"  {key}: {value}" for key, value in sorted(classifications.items()))
    (run_state.run_dir / "run-summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest


def _submit_bounded(
    executor: ThreadPoolExecutor,
    task_iter: Iterable[HistoryTask],
    pending: dict[Future[HistoryHttpResult], HistoryTask],
    *,
    maximum_pending: int,
    request_function: Callable[[HistoryTask], HistoryHttpResult],
) -> None:
    while len(pending) < maximum_pending:
        try:
            task = next(task_iter)  # type: ignore[arg-type]
        except StopIteration:
            return
        pending[executor.submit(request_function, task)] = task


def run_history_capture(
    tasks: Sequence[HistoryTask],
    *,
    input_file: Path,
    output_root: Path,
    output_root_source: str,
    mode: str,
    interval: str,
    overlap_days: int,
    concurrency: int,
    timeout_seconds: float,
    maximum_attempts: int,
    backoff_seconds: Sequence[float],
    user_agent: str,
    resume_run: Path | None = None,
    session: Any | None = None,
    opener: Callable[..., Any] = urlopen,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = utc_now,
    progress: Callable[[str], None] = print,
) -> tuple[Path, dict[str, Any]]:
    if concurrency < 1:
        raise HistoryInputError("concurrency must be at least 1.")
    if maximum_attempts < 1:
        raise HistoryInputError("maximum_attempts must be at least 1.")
    started_at = now()
    started_clock = clock()
    run_state = create_run_state(output_root, started_at=started_at, resume_run=resume_run)
    run_id = run_state.run_dir.name
    resumed = resume_run is not None
    run_plan = write_or_validate_run_plan(
        run_state,
        tasks=tasks,
        input_file=input_file,
        mode=mode,
        interval=interval,
        overlap_days=overlap_days,
        started_at=started_at,
        resumed=resumed,
    )
    if resumed:
        started_at = datetime.fromisoformat(str(run_plan["started_at_utc"]).replace("Z", "+00:00"))
    completed_keys = load_completed_task_keys(run_state.checkpoint_path)
    remaining_tasks = [task for task in tasks if task.task_key not in completed_keys]

    connection = connect_database(run_state.database_path)
    initialize_database(connection)
    connection.execute(
        """
        INSERT INTO runs(
            run_id, mode, interval, overlap_days, started_at_utc, completed_at_utc,
            status, input_file_name, requested_symbols, completed_symbols,
            run_folder_name, utility_version
        ) VALUES (?, ?, ?, ?, ?, NULL, 'RUNNING', ?, ?, ?, ?, ?)
        ON CONFLICT(run_id) DO UPDATE SET
            status='RUNNING',
            requested_symbols=excluded.requested_symbols,
            completed_symbols=excluded.completed_symbols,
            utility_version=excluded.utility_version
        """,
        (
            run_id,
            mode,
            interval,
            overlap_days,
            format_utc(started_at),
            input_file.name,
            len(tasks),
            len(completed_keys),
            run_state.run_dir.name,
            UTILITY_VERSION,
        ),
    )
    connection.commit()

    request_session = session or fast.YahooAnonymousSession(
        user_agent=user_agent,
        timeout_seconds=timeout_seconds,
    )
    gate = fast.SharedBackoffGate(clock=clock, sleep=sleep)
    results: list[SymbolResult] = []
    # Reconstruct prior results for resumed tasks from the database.
    if completed_keys:
        placeholders = ",".join("?" for _ in completed_keys)
        rows = connection.execute(
            f"SELECT * FROM symbol_runs WHERE run_id=? AND task_key IN ({placeholders})",
            (run_id, *sorted(completed_keys)),
        ).fetchall()
        for row in rows:
            results.append(
                SymbolResult(
                    task_key=row["task_key"], task_sequence=row["task_sequence"], symbol=row["symbol"],
                    interval=row["interval"], mode=row["mode"], full_range=bool(row["full_range"]),
                    request_start_epoch=row["request_start_epoch"], request_end_epoch=row["request_end_epoch"],
                    classification=row["classification"], http_status=row["http_status"], returned_symbol=None,
                    bars_returned=row["bars_returned"], new_bars=row["new_bars"], revised_bars=row["revised_bars"],
                    unchanged_bars=row["unchanged_bars"], missing_bars=row["missing_bars"],
                    events_returned=row["events_returned"], new_events=row["new_events"],
                    revised_events=row["revised_events"], unchanged_events=row["unchanged_events"],
                    full_refresh_required=bool(row["full_refresh_required"]), full_refresh_reason=row["full_refresh_reason"],
                    raw_file=row["raw_file"], raw_uncompressed_sha256=row["raw_sha256"], raw_compressed_sha256=None,
                    raw_uncompressed_bytes=0, raw_compressed_bytes=0, metadata_file="", elapsed_ms=row["elapsed_ms"],
                    attempts=row["attempts"], error_description=row["error_description"],
                )
            )

    def request_function(task: HistoryTask) -> HistoryHttpResult:
        return request_history_with_retry(
            task,
            session=request_session,
            timeout_seconds=timeout_seconds,
            maximum_attempts=maximum_attempts,
            backoff_seconds=backoff_seconds,
            user_agent=user_agent,
            gate=gate,
            opener=opener,
            sleep=sleep,
            clock=clock,
            now=now,
        )

    progress(f"Starting long history: {len(remaining_tasks)} task(s), concurrency {concurrency}")
    task_iterator = iter(remaining_tasks)
    pending: dict[Future[HistoryHttpResult], HistoryTask] = {}
    completed_count = len(completed_keys)
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        _submit_bounded(
            executor,
            task_iterator,
            pending,
            maximum_pending=max(concurrency * 2, 1),
            request_function=request_function,
        )
        while pending:
            done, _ = wait(set(pending), return_when=FIRST_COMPLETED)
            for future in done:
                task = pending.pop(future)
                http = future.result()
                result = process_symbol_result(
                    connection,
                    run_id=run_id,
                    run_state=run_state,
                    task=task,
                    http=http,
                )
                results.append(result)
                append_checkpoint(run_state, result)
                completed_count += 1
                connection.execute(
                    "UPDATE runs SET completed_symbols=? WHERE run_id=?",
                    (completed_count, run_id),
                )
                connection.commit()
                progress(
                    f"[{completed_count:05d}/{len(tasks):05d}] {task.symbol:<18} "
                    f"{result.classification} new={result.new_bars} revised={result.revised_bars}"
                )
            _submit_bounded(
                executor,
                task_iterator,
                pending,
                maximum_pending=max(concurrency * 2, 1),
                request_function=request_function,
            )

    results.sort(key=lambda item: item.task_sequence)
    write_symbol_results_csv(run_state.run_dir, results)
    write_revision_exports(connection, run_state.run_dir, run_id)
    completed_at = now()
    elapsed_seconds = max(0.0, clock() - started_clock)
    manifest = write_manifest_and_summary(
        connection,
        run_id=run_id,
        run_state=run_state,
        results=results,
        input_file=input_file,
        mode=mode,
        interval=interval,
        overlap_days=overlap_days,
        started_at=started_at,
        completed_at=completed_at,
        elapsed_seconds=elapsed_seconds,
        output_root_source=output_root_source,
        session_summary=request_session.public_summary(),
        resumed=resumed,
    )
    connection.execute(
        "UPDATE runs SET completed_at_utc=?, status='COMPLETED', completed_symbols=? WHERE run_id=?",
        (format_utc(completed_at), len(results), run_id),
    )
    connection.commit()
    connection.close()
    return run_state.run_dir, manifest


def parse_backoff(text: str) -> tuple[float, ...]:
    if not text.strip():
        return ()
    try:
        values = tuple(float(part.strip()) for part in text.split(",") if part.strip())
    except ValueError as exc:
        raise HistoryInputError("backoff values must be numbers.") from exc
    if any(value < 0 for value in values):
        raise HistoryInputError("backoff values cannot be negative.")
    return values


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Yahoo Finance long-history baseline and incremental synchronizer."
    )
    parser.add_argument("--mode", choices=sorted(VALID_MODES), help="baseline, sync, or refresh-flagged")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_FILE, help="CSV containing a symbol column.")
    parser.add_argument("--history-exclusions", type=Path, default=DEFAULT_HISTORY_EXCLUSION_FILE)
    parser.add_argument(
        "--include-history-excluded", action="store_true",
        help="Diagnostic override: include browser-confirmed no-history symbols in Long-history requests.",
    )
    parser.add_argument("--interval", choices=sorted(VALID_INTERVALS), default="1d")
    parser.add_argument("--overlap-days", type=int, default=30)
    parser.add_argument("--through-date", help="Optional inclusive YYYY-MM-DD end date; default is current UTC date.")
    parser.add_argument("--smoke", action="store_true", help="Use the first 5 unique symbols.")
    parser.add_argument("--limit", type=int, help="Use only the first N unique symbols.")
    parser.add_argument("--output-root", type=Path, help="External long-history archive root.")
    parser.add_argument("--local-config", type=Path, default=LOCAL_CONFIG_FILE)
    parser.add_argument("--configure-output-root", type=Path, metavar="PATH")
    parser.add_argument("--show-output-root", action="store_true")
    parser.add_argument("--resume-run", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--backoff-seconds", default="2,10")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.configure_output_root is not None:
            config_path = write_local_output_config(args.local_config, args.configure_output_root)
            configured = load_local_output_root(config_path)
            assert configured is not None
            print(f"Local history config: {config_path}")
            print(f"Long-history output root: {configured}")
            print("Storage policy: compressed raw JSON plus SQLite outside the synchronized repository.")
            return 0

        if args.resume_run is not None:
            resume_run = validate_resume_run(args.resume_run)
            # Expected layout: <root>/runs/<run-folder>
            output_root = resume_run.parent.parent
            output_root_source = "resume_run"
        else:
            resume_run = None
            output_root, output_root_source = resolve_output_root(args.output_root, config_path=args.local_config)

        if args.show_output_root:
            print(f"Long-history output root: {output_root}")
            print(f"Output root source: {output_root_source}")
            print(f"SQLite database: {output_root / DATABASE_FILENAME}")
            print("Storage policy: compressed raw JSON plus SQLite outside the synchronized repository.")
            return 0

        if not args.mode:
            raise HistoryInputError("--mode is required unless configuring or showing the output root.")
        if args.limit is not None and args.limit < 1:
            raise HistoryInputError("--limit must be at least 1.")
        all_symbols = unique_symbols_from_input(args.input)
        exclusion_map = load_history_exclusions(args.history_exclusions)
        symbols, excluded_symbols = partition_history_symbols(
            all_symbols, exclusion_map, include_excluded=args.include_history_excluded
        )
        if args.include_history_excluded:
            excluded_symbols = []
        else:
            print(
                f"Long-history exclusions: {len(excluded_symbols)} request(s) skipped; "
                "Fast-mode capture remains enabled."
            )
        if args.smoke:
            symbols = symbols[:5]
        if args.limit is not None:
            symbols = symbols[: args.limit]
        request_end_epoch = parse_through_date(args.through_date)
        tasks = build_tasks(
            symbols,
            mode=args.mode,
            interval=args.interval,
            overlap_days=args.overlap_days,
            request_end_epoch=request_end_epoch,
            database_path=output_root / DATABASE_FILENAME,
        )

        if args.dry_run:
            plan = {
                "utility_version": UTILITY_VERSION,
                "history_exclusions": history_exclusion_manifest(
                    excluded_symbols, exclusion_file=args.history_exclusions,
                    override_used=args.include_history_excluded,
                ),
                "mode": args.mode,
                "interval": args.interval,
                "input_file_name": args.input.name,
                "unique_input_symbols": len(symbols),
                "planned_tasks": len(tasks),
                "full_range_tasks": sum(task.full_range for task in tasks),
                "incremental_tasks": sum(not task.full_range for task in tasks),
                "overlap_days": args.overlap_days,
                "request_end_epoch": request_end_epoch,
                "network_requests_sent": 0,
                "storage": {
                    "output_root": str(output_root),
                    "output_root_source": output_root_source,
                    "database_file": str(output_root / DATABASE_FILENAME),
                    "repository_output_allowed": False,
                },
                "first_tasks": [asdict(task) for task in tasks[:10]],
            }
            print(json.dumps(plan, indent=2))
            return 0

        prepare_output_root(output_root)
        print(f"Long-history output root: {output_root} ({output_root_source})")
        print(f"SQLite database: {output_root / DATABASE_FILENAME}")
        print("Storage policy: compressed raw JSON plus SQLite outside the synchronized repository.")
        run_dir, manifest = run_history_capture(
            tasks,
            input_file=args.input,
            output_root=output_root,
            output_root_source=output_root_source,
            mode=args.mode,
            interval=args.interval,
            overlap_days=args.overlap_days,
            concurrency=args.concurrency,
            timeout_seconds=args.timeout,
            maximum_attempts=args.max_attempts,
            backoff_seconds=parse_backoff(args.backoff_seconds),
            user_agent=args.user_agent,
            resume_run=resume_run,
        )
        manifest = write_history_exclusion_outputs(
            run_dir, excluded_symbols, exclusion_file=args.history_exclusions,
            override_used=args.include_history_excluded,
            manifest_file="run-manifest.json", report_file="run-summary.txt",
        )
        print(f"Run folder: {run_dir}")
        print(f"Completed symbols: {manifest['totals']['symbols_completed']}")
        print(f"New bars: {manifest['totals']['new_bars']}")
        print(f"Revised bars: {manifest['totals']['revised_bars']}")
        print(f"Symbols flagged full refresh: {manifest['totals']['symbols_flagged_for_full_refresh']}")
        return 0
    except (HistoryInputError, YahooSessionError, sqlite3.Error, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
