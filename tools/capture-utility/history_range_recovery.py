#!/usr/bin/env python3
"""Recover Yahoo daily history that exceeds one-request range limits.

Version 0.1.0-candidate.9. This utility is intentionally limited to symbols
whose prior 1900-through-current request returned REQUEST_RANGE_NOT_SUPPORTED.
It requests explicit overlapping windows shorter than Yahoo's 100-year daily
limit, merges and deduplicates the responses, and applies the merged result to
an external compact long-history database.

By default, the verified ``history_compact.sqlite`` is copied to a separate
validation folder and only the copy is updated. Direct in-place updates require
both ``--in-place`` and ``--acknowledge-in-place-update``.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import importlib.util
import json
import sqlite3
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.request import urlopen

UTILITY_VERSION = "0.1.0-candidate.9"
INTERVAL = "1d"
DEFAULT_WINDOW_YEARS = 90
DEFAULT_OVERLAP_DAYS = 31
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SYMBOLS_FILE = REPOSITORY_ROOT / "data" / "history_range_recovery_symbols_12.csv"


def _load_adjacent(name: str, filename: str) -> Any:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load required dependency: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


compact = _load_adjacent("_range_recovery_compact_dependency", "history_compact_incremental.py")
history = compact.history
fast = history.fast


class RangeRecoveryError(RuntimeError):
    """Raised when split-window history recovery cannot safely continue."""


@dataclass(frozen=True)
class RecoveryWindow:
    window_index: int
    start_epoch: int
    end_epoch: int
    start_date: str
    end_date_exclusive: str


@dataclass(frozen=True)
class WindowRequest:
    symbol: str
    window: RecoveryWindow
    task: Any


@dataclass
class WindowCapture:
    symbol: str
    window_index: int
    task_key: str
    start_epoch: int
    end_epoch: int
    classification: str
    http_status: int | None
    returned_symbol: str | None
    bars: list[Any]
    events: list[Any]
    raw_file: str | None
    raw_sha256: str | None
    elapsed_ms: int
    attempts: int
    error_code: str | None
    error_description: str | None


@dataclass
class MergeOutcome:
    symbol: str
    classification: str
    returned_symbol: str | None
    bars: list[Any]
    events: list[Any]
    duplicate_bars: int
    duplicate_events: int
    bar_conflicts: int
    event_conflicts: int
    window_count: int
    successful_windows: int
    error_description: str | None


@dataclass
class SymbolRecoveryResult:
    symbol: str
    classification: str
    bars_returned: int
    new_bars: int
    revised_bars: int
    unchanged_bars: int
    missing_bars: int
    events_returned: int
    new_events: int
    revised_events: int
    unchanged_events: int
    duplicate_bars: int
    duplicate_events: int
    bar_conflicts: int
    event_conflicts: int
    window_count: int
    successful_windows: int
    merged_file: str | None
    error_description: str | None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc(value: datetime) -> str:
    return compact.format_utc(value)


def filename_utc(value: datetime) -> str:
    return compact.filename_utc(value)


def normalize_path(path: Path) -> Path:
    return compact.normalize_path(path)


def epoch_to_date(value: int) -> date:
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (epoch + timedelta(seconds=int(value))).date()


def date_to_epoch(value: date) -> int:
    return int(datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc).timestamp())


def add_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        # February 29 to a non-leap year.
        return value.replace(month=2, day=28, year=value.year + years)


def build_windows(
    *,
    start_epoch: int,
    end_epoch: int,
    window_years: int = DEFAULT_WINDOW_YEARS,
    overlap_days: int = DEFAULT_OVERLAP_DAYS,
) -> list[RecoveryWindow]:
    if window_years < 1 or window_years > 99:
        raise RangeRecoveryError("--window-years must be between 1 and 99.")
    if overlap_days < 0:
        raise RangeRecoveryError("--overlap-days cannot be negative.")
    if end_epoch <= start_epoch:
        raise RangeRecoveryError("Recovery end must be after recovery start.")
    start_date = epoch_to_date(start_epoch)
    end_date = epoch_to_date(end_epoch)
    windows: list[RecoveryWindow] = []
    current = start_date
    while current < end_date:
        window_end = min(add_years(current, window_years), end_date)
        if window_end <= current:
            raise RangeRecoveryError("Window calculation did not advance.")
        windows.append(
            RecoveryWindow(
                window_index=len(windows) + 1,
                start_epoch=date_to_epoch(current),
                end_epoch=date_to_epoch(window_end),
                start_date=current.isoformat(),
                end_date_exclusive=window_end.isoformat(),
            )
        )
        if window_end >= end_date:
            break
        next_start = window_end - timedelta(days=overlap_days)
        if next_start <= current:
            raise RangeRecoveryError("Window overlap is too large for the selected window length.")
        current = next_start
    return windows


def load_symbols(path: Path) -> list[str]:
    resolved = normalize_path(path)
    if not resolved.is_file():
        raise RangeRecoveryError(f"Recovery-symbol file does not exist: {resolved}")
    with resolved.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "Symbol" not in reader.fieldnames:
            raise RangeRecoveryError("Recovery-symbol CSV must contain a Symbol column.")
        symbols: list[str] = []
        seen: set[str] = set()
        for row in reader:
            symbol = str(row.get("Symbol") or "").strip()
            if symbol and symbol not in seen:
                seen.add(symbol)
                symbols.append(symbol)
    if not symbols:
        raise RangeRecoveryError("Recovery-symbol CSV contains no symbols.")
    return symbols


def build_requests(symbols: Sequence[str], windows: Sequence[RecoveryWindow]) -> list[WindowRequest]:
    requests: list[WindowRequest] = []
    sequence = 0
    for symbol in symbols:
        for window in windows:
            sequence += 1
            task = history.HistoryTask(
                task_key=(
                    f"range-recovery-{sequence:04d}-{fast.safe_filename(symbol)}-"
                    f"w{window.window_index:02d}"
                ),
                task_sequence=sequence,
                symbol=symbol,
                interval=INTERVAL,
                mode="range-recovery-window",
                full_range=False,
                request_start_epoch=window.start_epoch,
                request_end_epoch=window.end_epoch,
                prior_latest_epoch=None,
                prior_full_refresh_required=False,
            )
            requests.append(WindowRequest(symbol=symbol, window=window, task=task))
    return requests


def create_run_folder(
    source_database: Path,
    *,
    started_at: datetime,
    in_place: bool,
) -> tuple[Path, Path, bool]:
    source_database = normalize_path(source_database)
    if in_place:
        run_dir = source_database.parent / "range-recovery-runs" / (
            f"{filename_utc(started_at)}_range-recovery-run"
        )
        run_dir.mkdir(parents=True, exist_ok=False)
        return run_dir, source_database, False
    run_dir = source_database.parent / "range-recovery-validations" / (
        f"{filename_utc(started_at)}_range-recovery-validation"
    )
    validation_database = run_dir / compact.VALIDATION_DATABASE_FILENAME
    compact.sqlite_backup_copy(source_database, validation_database)
    return run_dir, validation_database, True


def _bar_values(bar: Any) -> tuple[Any, ...]:
    return (bar.open, bar.high, bar.low, bar.close, bar.adjclose, bar.volume)


def merge_window_captures(symbol: str, captures: Sequence[WindowCapture]) -> MergeOutcome:
    ordered = sorted(captures, key=lambda item: item.window_index)
    if not ordered:
        return MergeOutcome(symbol, "RECOVERY_NO_WINDOWS", None, [], [], 0, 0, 0, 0, 0, 0, "No windows captured.")

    acceptable = {"SUCCESS_HISTORY_RETURNED", "NO_HISTORY_DATA", "NO_CHART_HISTORY_AVAILABLE"}
    failed = [capture for capture in ordered if capture.classification not in acceptable]
    if failed:
        description = "; ".join(
            f"window {item.window_index}: {item.classification}"
            + (f" ({item.error_description})" if item.error_description else "")
            for item in failed
        )
        return MergeOutcome(
            symbol, "RECOVERY_WINDOW_FAILED", None, [], [], 0, 0, 0, 0,
            len(ordered), len(ordered) - len(failed), description,
        )

    returned_symbols = {item.returned_symbol for item in ordered if item.returned_symbol}
    if len(returned_symbols) > 1 or (returned_symbols and symbol not in returned_symbols):
        return MergeOutcome(
            symbol, "RECOVERY_RETURNED_SYMBOL_MISMATCH", None, [], [], 0, 0, 0, 0,
            len(ordered), len(ordered), f"Returned symbols: {sorted(returned_symbols)}",
        )

    bar_map: dict[int, Any] = {}
    duplicate_bars = 0
    bar_conflicts = 0
    for capture in ordered:
        for bar in capture.bars:
            existing = bar_map.get(bar.timestamp_utc)
            if existing is None:
                bar_map[bar.timestamp_utc] = bar
            elif _bar_values(existing) == _bar_values(bar):
                duplicate_bars += 1
            else:
                bar_conflicts += 1

    event_map: dict[tuple[str, str], Any] = {}
    event_slots: dict[tuple[str, int], str] = {}
    duplicate_events = 0
    event_conflicts = 0
    for capture in ordered:
        for event in capture.events:
            key = (event.event_type, event.event_key)
            existing = event_map.get(key)
            if existing is not None:
                if existing.event_json == event.event_json:
                    duplicate_events += 1
                else:
                    event_conflicts += 1
                continue
            slot = (event.event_type, event.event_timestamp_utc)
            prior_json = event_slots.get(slot)
            if prior_json is not None and prior_json != event.event_json:
                event_conflicts += 1
                continue
            event_slots[slot] = event.event_json
            event_map[key] = event

    if bar_conflicts or event_conflicts:
        return MergeOutcome(
            symbol, "RECOVERY_OVERLAP_CONFLICT", next(iter(returned_symbols), symbol), [], [],
            duplicate_bars, duplicate_events, bar_conflicts, event_conflicts,
            len(ordered), len(ordered),
            f"Overlap conflicts: bars={bar_conflicts}, events={event_conflicts}",
        )

    bars = [bar_map[key] for key in sorted(bar_map)]
    events = sorted(
        event_map.values(),
        key=lambda item: (item.event_timestamp_utc, item.event_type, item.event_key),
    )
    classification = "SUCCESS_HISTORY_RETURNED" if bars or events else "NO_HISTORY_DATA"
    return MergeOutcome(
        symbol, classification, next(iter(returned_symbols), symbol), bars, events,
        duplicate_bars, duplicate_events, 0, 0, len(ordered), len(ordered), None,
    )


def _merged_payload(outcome: MergeOutcome, captures: Sequence[WindowCapture], generated_at: str) -> bytes:
    payload = {
        "capture_kind": "derived_split_window_merge",
        "utility_version": UTILITY_VERSION,
        "generated_at_utc": generated_at,
        "symbol": outcome.symbol,
        "interval": INTERVAL,
        "classification": outcome.classification,
        "window_sources": [
            {
                "window_index": item.window_index,
                "start_epoch": item.start_epoch,
                "end_epoch": item.end_epoch,
                "classification": item.classification,
                "http_status": item.http_status,
                "raw_file": item.raw_file,
                "raw_sha256": item.raw_sha256,
            }
            for item in sorted(captures, key=lambda item: item.window_index)
        ],
        "merge": {
            "duplicate_bars": outcome.duplicate_bars,
            "duplicate_events": outcome.duplicate_events,
            "bar_conflicts": outcome.bar_conflicts,
            "event_conflicts": outcome.event_conflicts,
        },
        "bars": [asdict(item) for item in outcome.bars],
        "events": [asdict(item) for item in outcome.events],
        "absolute_local_path_persisted": False,
    }
    return (json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def write_merged_capture(
    run_dir: Path,
    outcome: MergeOutcome,
    captures: Sequence[WindowCapture],
    *,
    generated_at: str,
) -> tuple[str, str, str]:
    relative = Path("merged") / f"{fast.safe_filename(outcome.symbol)}.merged.json.gz"
    path = run_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = _merged_payload(outcome, captures, generated_at)
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    path.write_bytes(compressed)
    return relative.as_posix(), hashlib.sha256(raw).hexdigest(), hashlib.sha256(compressed).hexdigest()


def capture_window_response(
    run_dir: Path,
    archive_root: Path,
    request: WindowRequest,
    http: Any,
) -> WindowCapture:
    state = history.RunState(
        output_root=archive_root,
        run_dir=run_dir,
        database_path=run_dir / "not-used.sqlite",
        checkpoint_path=run_dir / "checkpoint.jsonl",
        checkpoint_lock=threading.Lock(),
    )
    raw_file, raw_sha, _compressed_sha, _raw_bytes, _compressed_bytes, _metadata = (
        history.write_raw_and_metadata(state, request.task, http)
    )
    parsed = compact.classify_http_result(http, request.task)
    return WindowCapture(
        symbol=request.symbol,
        window_index=request.window.window_index,
        task_key=request.task.task_key,
        start_epoch=request.window.start_epoch,
        end_epoch=request.window.end_epoch,
        classification=parsed.classification,
        http_status=http.http_status,
        returned_symbol=parsed.returned_symbol,
        bars=list(parsed.bars),
        events=list(parsed.events),
        raw_file=raw_file,
        raw_sha256=raw_sha,
        elapsed_ms=int(http.elapsed_ms),
        attempts=len(http.attempts),
        error_code=parsed.error_code,
        error_description=parsed.error_description,
    )


def apply_recovery_outcome(
    connection: sqlite3.Connection,
    lookups: Any,
    *,
    run_id: str,
    task_sequence: int,
    outcome: MergeOutcome,
    captures: Sequence[WindowCapture],
    run_dir: Path,
    source_database: Path,
    detected_at: str,
) -> SymbolRecoveryResult:
    symbol_id = compact.ensure_symbol(connection, lookups, outcome.symbol)
    interval_id = compact.ensure_interval(connection, lookups, INTERVAL)
    run_key = compact.ensure_run(connection, lookups, run_id)
    merged_file: str | None = None
    source_id: int | None = None
    stats = history.ApplyStats()

    if outcome.classification in {"SUCCESS_HISTORY_RETURNED", "NO_HISTORY_DATA"}:
        merged_file, merged_sha, _compressed_sha = write_merged_capture(
            run_dir, outcome, captures, generated_at=detected_at
        )
        try:
            relative_run = run_dir.relative_to(source_database.parent)
            portable_source = (relative_run / merged_file).as_posix()
        except ValueError:
            portable_source = f"{run_dir.name}/{merged_file}"
        source_id = compact.ensure_source(connection, lookups, portable_source, merged_sha)
        parsed = history.ParsedHistory(
            outcome.classification,
            outcome.returned_symbol,
            outcome.bars,
            outcome.events,
            {"recovery_window_count": outcome.window_count},
        )
        task = history.HistoryTask(
            task_key=f"range-recovery-{task_sequence:04d}-{fast.safe_filename(outcome.symbol)}",
            task_sequence=task_sequence,
            symbol=outcome.symbol,
            interval=INTERVAL,
            mode="range-recovery",
            full_range=True,
            request_start_epoch=history.BASELINE_START_EPOCH,
            request_end_epoch=max(item.end_epoch for item in captures),
            prior_latest_epoch=None,
            prior_full_refresh_required=False,
        )
        stats = compact.apply_parsed_history_compact(
            connection,
            lookups,
            run_id=run_id,
            task=task,
            parsed=parsed,
            source_file=portable_source,
            source_sha256=merged_sha,
            detected_at_utc=detected_at,
        )
    else:
        task = history.HistoryTask(
            task_key=f"range-recovery-{task_sequence:04d}-{fast.safe_filename(outcome.symbol)}",
            task_sequence=task_sequence,
            symbol=outcome.symbol,
            interval=INTERVAL,
            mode="range-recovery",
            full_range=True,
            request_start_epoch=history.BASELINE_START_EPOCH,
            request_end_epoch=max(item.end_epoch for item in captures),
            prior_latest_epoch=None,
            prior_full_refresh_required=False,
        )

    previous_state = connection.execute(
        "SELECT * FROM symbol_state WHERE symbol_id=? AND interval_id=?",
        (symbol_id, interval_id),
    ).fetchone()
    latest_row = connection.execute(
        "SELECT MAX(timestamp_utc) FROM bars WHERE symbol_id=? AND interval_id=?",
        (symbol_id, interval_id),
    ).fetchone()
    current_latest = latest_row[0] if latest_row else None
    success = outcome.classification in {"SUCCESS_HISTORY_RETURNED", "NO_HISTORY_DATA"}
    prior_flag = bool(previous_state["full_refresh_required"]) if previous_state else False
    prior_reason = str(previous_state["full_refresh_reason"] or "") if previous_state else ""
    if success:
        final_flag = bool(stats.full_refresh_required)
        final_reason = stats.full_refresh_reason
    else:
        final_flag = True
        final_reason = ",".join(
            item for item in dict.fromkeys(
                part for part in (prior_reason, outcome.classification) if part
            )
        )
    baseline_key = previous_state["baseline_run_key"] if previous_state else None
    if baseline_key is None and success:
        baseline_key = run_key

    connection.execute(
        """
        INSERT INTO symbol_state(
            symbol_id, interval_id, last_bar_timestamp, last_checked_at_utc,
            last_success_run_key, baseline_run_key, full_refresh_required, full_refresh_reason
        ) VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(symbol_id, interval_id) DO UPDATE SET
            last_bar_timestamp=excluded.last_bar_timestamp,
            last_checked_at_utc=excluded.last_checked_at_utc,
            last_success_run_key=CASE WHEN ? THEN excluded.last_success_run_key ELSE symbol_state.last_success_run_key END,
            baseline_run_key=COALESCE(symbol_state.baseline_run_key, excluded.baseline_run_key),
            full_refresh_required=excluded.full_refresh_required,
            full_refresh_reason=excluded.full_refresh_reason
        """,
        (
            symbol_id, interval_id, current_latest, detected_at, run_key if success else None,
            baseline_key, int(final_flag), final_reason, int(success),
        ),
    )

    http_statuses = [item.http_status for item in captures if item.http_status is not None]
    total_elapsed = sum(item.elapsed_ms for item in captures)
    total_attempts = sum(item.attempts for item in captures)
    connection.execute(
        """
        INSERT OR REPLACE INTO symbol_runs(
            run_key, task_key, task_sequence, symbol_id, interval_id, mode, full_range,
            request_start_epoch, request_end_epoch, classification, http_status,
            bars_returned, new_bars, revised_bars, unchanged_bars, missing_bars,
            events_returned, new_events, revised_events, unchanged_events,
            full_refresh_required, full_refresh_reason, raw_source_id,
            raw_file_fallback, raw_sha256_fallback, elapsed_ms, attempts, error_description
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            run_key, task.task_key, task_sequence, symbol_id, interval_id, task.mode, 1,
            task.request_start_epoch, task.request_end_epoch, outcome.classification,
            max(http_statuses) if http_statuses else None,
            stats.bars_returned, stats.new_bars, stats.revised_bars, stats.unchanged_bars,
            stats.missing_bars, stats.events_returned, stats.new_events,
            stats.revised_events, stats.unchanged_events, int(final_flag), final_reason,
            source_id, None, None, total_elapsed, total_attempts, outcome.error_description,
        ),
    )

    return SymbolRecoveryResult(
        symbol=outcome.symbol,
        classification=outcome.classification,
        bars_returned=stats.bars_returned,
        new_bars=stats.new_bars,
        revised_bars=stats.revised_bars,
        unchanged_bars=stats.unchanged_bars,
        missing_bars=stats.missing_bars,
        events_returned=stats.events_returned,
        new_events=stats.new_events,
        revised_events=stats.revised_events,
        unchanged_events=stats.unchanged_events,
        duplicate_bars=outcome.duplicate_bars,
        duplicate_events=outcome.duplicate_events,
        bar_conflicts=outcome.bar_conflicts,
        event_conflicts=outcome.event_conflicts,
        window_count=outcome.window_count,
        successful_windows=outcome.successful_windows,
        merged_file=merged_file,
        error_description=outcome.error_description,
    )


def write_window_results(run_dir: Path, captures: Sequence[WindowCapture]) -> None:
    fields = (
        "symbol", "window_index", "task_key", "start_epoch", "end_epoch",
        "classification", "http_status", "returned_symbol", "bars", "events",
        "raw_file", "raw_sha256", "elapsed_ms", "attempts", "error_code",
        "error_description",
    )
    with (run_dir / "window-results.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in sorted(captures, key=lambda row: (row.symbol, row.window_index)):
            writer.writerow({
                "symbol": item.symbol,
                "window_index": item.window_index,
                "task_key": item.task_key,
                "start_epoch": item.start_epoch,
                "end_epoch": item.end_epoch,
                "classification": item.classification,
                "http_status": item.http_status,
                "returned_symbol": item.returned_symbol,
                "bars": len(item.bars),
                "events": len(item.events),
                "raw_file": item.raw_file,
                "raw_sha256": item.raw_sha256,
                "elapsed_ms": item.elapsed_ms,
                "attempts": item.attempts,
                "error_code": item.error_code,
                "error_description": item.error_description,
            })


def write_symbol_results(run_dir: Path, results: Sequence[SymbolRecoveryResult]) -> None:
    fields = tuple(SymbolRecoveryResult.__dataclass_fields__)
    with (run_dir / "symbol-results.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in results:
            writer.writerow(asdict(item))


def verify_outcomes(
    connection: sqlite3.Connection,
    outcomes: Sequence[MergeOutcome],
) -> dict[str, int]:
    bars_checked = 0
    bar_mismatches = 0
    events_checked = 0
    event_mismatches = 0
    for outcome in outcomes:
        if outcome.classification != "SUCCESS_HISTORY_RETURNED":
            continue
        symbol_row = connection.execute(
            "SELECT symbol_id FROM symbols WHERE symbol=?", (outcome.symbol,)
        ).fetchone()
        interval_row = connection.execute(
            "SELECT interval_id FROM intervals WHERE interval=?", (INTERVAL,)
        ).fetchone()
        if symbol_row is None or interval_row is None:
            bar_mismatches += len(outcome.bars)
            event_mismatches += len(outcome.events)
            continue
        symbol_id = int(symbol_row[0])
        interval_id = int(interval_row[0])
        for bar in outcome.bars:
            bars_checked += 1
            row = connection.execute(
                """SELECT open, high, low, close, adjclose, volume FROM bars
                   WHERE symbol_id=? AND interval_id=? AND timestamp_utc=?""",
                (symbol_id, interval_id, bar.timestamp_utc),
            ).fetchone()
            if row is None or tuple(row) != _bar_values(bar):
                bar_mismatches += 1
        for event in outcome.events:
            events_checked += 1
            row = connection.execute(
                """SELECT e.event_json FROM events e
                   JOIN event_types et ON et.event_type_id=e.event_type_id
                   WHERE e.symbol_id=? AND e.interval_id=? AND et.event_type=? AND e.event_key=?""",
                (symbol_id, interval_id, event.event_type, event.event_key),
            ).fetchone()
            if row is None or row[0] != event.event_json:
                event_mismatches += 1
    return {
        "bars_checked": bars_checked,
        "bar_mismatches": bar_mismatches,
        "events_checked": events_checked,
        "event_mismatches": event_mismatches,
    }


def run_recovery(
    *,
    symbols: Sequence[str],
    windows: Sequence[RecoveryWindow],
    source_database: Path,
    archive_root: Path,
    database_resolution: str,
    symbols_file: Path,
    in_place: bool,
    overlap_days: int,
    concurrency: int,
    timeout_seconds: float,
    maximum_attempts: int,
    backoff_seconds: Sequence[float],
    user_agent: str,
    session: Any | None = None,
    opener: Callable[..., Any] = urlopen,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = utc_now,
    progress: Callable[[str], None] = print,
    request_override: Callable[[Any], Any] | None = None,
) -> tuple[Path, dict[str, Any]]:
    if concurrency < 1:
        raise RangeRecoveryError("--concurrency must be at least 1.")
    started_at = now()
    started_clock = clock()
    source_before = compact.fingerprint_files(source_database)
    run_dir, working_database, validation_copy = create_run_folder(
        source_database, started_at=started_at, in_place=in_place
    )
    requests = build_requests(symbols, windows)
    plan = {
        "utility_version": UTILITY_VERSION,
        "started_at_utc": format_utc(started_at),
        "symbols_file_name": symbols_file.name,
        "symbols": list(symbols),
        "interval": INTERVAL,
        "overlap_days": overlap_days,
        "window_count_per_symbol": len(windows),
        "network_request_count": len(requests),
        "windows": [asdict(item) for item in windows],
        "validation_copy": validation_copy,
        "in_place_update": in_place,
        "source_compact_database_file": source_database.name,
        "working_database_file": working_database.name,
        "absolute_local_path_persisted": False,
    }
    (run_dir / "range-recovery-plan.json").write_text(
        json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    request_session = session or fast.YahooAnonymousSession(
        user_agent=user_agent, timeout_seconds=timeout_seconds
    )
    gate = fast.SharedBackoffGate(clock=clock, sleep=sleep)

    def request_function(item: WindowRequest) -> Any:
        if request_override is not None:
            return request_override(item.task)
        return history.request_history_with_retry(
            item.task,
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

    progress(
        f"Starting {len(symbols)}-symbol split-window recovery: {len(requests)} request(s), "
        f"concurrency {concurrency}"
    )
    captures: list[WindowCapture] = []
    iterator = iter(requests)
    pending: dict[Future[Any], WindowRequest] = {}
    completed = 0
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        while len(pending) < max(concurrency * 2, 1):
            try:
                item = next(iterator)
            except StopIteration:
                break
            pending[executor.submit(request_function, item)] = item
        while pending:
            done, _ = wait(set(pending), return_when=FIRST_COMPLETED)
            for future in done:
                item = pending.pop(future)
                http = future.result()
                capture = capture_window_response(run_dir, archive_root, item, http)
                captures.append(capture)
                completed += 1
                progress(
                    f"[{completed:02d}/{len(requests):02d}] {item.symbol:<12} "
                    f"window {item.window.window_index}: {capture.classification} "
                    f"bars={len(capture.bars):,}"
                )
            while len(pending) < max(concurrency * 2, 1):
                try:
                    item = next(iterator)
                except StopIteration:
                    break
                pending[executor.submit(request_function, item)] = item

    write_window_results(run_dir, captures)
    grouped: dict[str, list[WindowCapture]] = defaultdict(list)
    for capture in captures:
        grouped[capture.symbol].append(capture)
    outcomes = [merge_window_captures(symbol, grouped.get(symbol, [])) for symbol in symbols]

    connection = compact.connect_writable(working_database)
    lookups = compact.load_lookups(connection)
    run_id = run_dir.name
    run_key = compact.ensure_run(connection, lookups, run_id)
    interval_id = compact.ensure_interval(connection, lookups, INTERVAL)
    connection.execute(
        """
        INSERT INTO runs(
            run_key, mode, interval_id, overlap_days, started_at_utc, completed_at_utc,
            status, input_file_name, requested_symbols, completed_symbols,
            run_folder_name, utility_version
        ) VALUES(?,?,?,?,?,NULL,'RUNNING',?,?,0,?,?)
        ON CONFLICT(run_key) DO UPDATE SET status='RUNNING', utility_version=excluded.utility_version
        """,
        (
            run_key, "range-recovery", interval_id, overlap_days,
            format_utc(started_at), symbols_file.name, len(symbols), run_dir.name,
            UTILITY_VERSION,
        ),
    )
    connection.commit()

    symbol_results: list[SymbolRecoveryResult] = []
    for sequence, outcome in enumerate(outcomes, start=1):
        detected_at = format_utc(now())
        with connection:
            result = apply_recovery_outcome(
                connection,
                lookups,
                run_id=run_id,
                task_sequence=sequence,
                outcome=outcome,
                captures=grouped.get(outcome.symbol, []),
                run_dir=run_dir,
                source_database=source_database,
                detected_at=detected_at,
            )
            connection.execute(
                "UPDATE runs SET completed_symbols=? WHERE run_key=?", (sequence, run_key)
            )
        symbol_results.append(result)
        progress(
            f"MERGED {outcome.symbol:<12} {outcome.classification} "
            f"bars={len(outcome.bars):,} events={len(outcome.events):,}"
        )

    verification = verify_outcomes(connection, outcomes)
    completed_at = now()
    connection.execute(
        "UPDATE runs SET completed_at_utc=?, status='COMPLETED', completed_symbols=? WHERE run_key=?",
        (format_utc(completed_at), len(symbol_results), run_key),
    )
    connection.execute(
        "INSERT OR REPLACE INTO meta(key,value) VALUES('last_range_recovery_run_id',?)",
        (run_id,),
    )
    if in_place:
        connection.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('build_status','ACTIVE_COMPACT')")
    connection.commit()
    quick_rows = [str(row[0]) for row in connection.execute("PRAGMA quick_check")]
    quick_check = "ok" if quick_rows == ["ok"] else "; ".join(quick_rows)
    foreign_key_ok = not list(connection.execute("PRAGMA foreign_key_check"))
    state_mismatches = int(connection.execute(
        """SELECT COUNT(*) FROM symbol_state st
           WHERE st.last_bar_timestamp IS NOT (
             SELECT MAX(b.timestamp_utc) FROM bars b
             WHERE b.symbol_id=st.symbol_id AND b.interval_id=st.interval_id
           )"""
    ).fetchone()[0])
    connection.close()

    source_after = compact.fingerprint_files(source_database)
    source_unchanged = None if in_place else compact.main_database_unchanged(source_before, source_after)
    if not in_place and not source_unchanged:
        raise RangeRecoveryError("The verified source compact database changed during validation.")

    write_symbol_results(run_dir, symbol_results)
    classifications = Counter(item.classification for item in symbol_results)
    window_classifications = Counter(item.classification for item in captures)
    totals = Counter()
    for item in symbol_results:
        for key in (
            "bars_returned", "new_bars", "revised_bars", "unchanged_bars",
            "missing_bars", "events_returned", "new_events", "revised_events",
            "unchanged_events", "duplicate_bars", "duplicate_events",
            "bar_conflicts", "event_conflicts",
        ):
            totals[key] += int(getattr(item, key))
    recovery_complete = all(
        item.classification in {"SUCCESS_HISTORY_RETURNED", "NO_HISTORY_DATA"}
        for item in symbol_results
    )
    verification_ok = (
        quick_check == "ok"
        and foreign_key_ok
        and state_mismatches == 0
        and verification["bar_mismatches"] == 0
        and verification["event_mismatches"] == 0
        and (in_place or source_unchanged is True)
    )
    manifest = {
        **plan,
        "completed_at_utc": format_utc(completed_at),
        "elapsed_seconds": round(max(0.0, clock() - started_clock), 3),
        "database_resolution": database_resolution,
        "source_compact_database_unchanged": source_unchanged,
        "symbols_completed": len(symbol_results),
        "window_requests_completed": len(captures),
        "window_classifications": dict(sorted(window_classifications.items())),
        "symbol_classifications": dict(sorted(classifications.items())),
        "totals": dict(totals),
        "verification": verification,
        "quick_check": quick_check,
        "foreign_key_check_ok": foreign_key_ok,
        "symbol_state_latest_mismatches": state_mismatches,
        "recovery_complete": recovery_complete,
        "verification_ok": verification_ok,
        "legacy_history_database_touched": False,
        "database_promotion_or_replacement_performed": False,
    }
    (run_dir / "range-recovery-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    report = [
        "Yahoo Long-History Split-Window Range Recovery",
        f"Utility version: {UTILITY_VERSION}",
        f"Started UTC: {manifest['started_at_utc']}",
        f"Completed UTC: {manifest['completed_at_utc']}",
        f"Validation copy: {validation_copy}",
        f"In-place update: {in_place}",
        "Source compact database unchanged: " + (
            "not applicable (updated in place)" if in_place else str(source_unchanged)
        ),
        f"Symbols completed: {len(symbol_results)} of {len(symbols)}",
        f"Window requests completed: {len(captures)} of {len(requests)}",
        f"Bars merged: {totals['bars_returned']:,}",
        f"New bars: {totals['new_bars']:,}",
        f"Revised bars: {totals['revised_bars']:,}",
        f"Events merged: {totals['events_returned']:,}",
        f"New events: {totals['new_events']:,}",
        f"Duplicate overlap bars removed: {totals['duplicate_bars']:,}",
        f"Duplicate overlap events removed: {totals['duplicate_events']:,}",
        f"Bar overlap conflicts: {totals['bar_conflicts']:,}",
        f"Event overlap conflicts: {totals['event_conflicts']:,}",
        f"Returned bars checked: {verification['bars_checked']:,}",
        f"Returned bar mismatches: {verification['bar_mismatches']:,}",
        f"Returned events checked: {verification['events_checked']:,}",
        f"Returned event mismatches: {verification['event_mismatches']:,}",
        f"Quick check: {quick_check}",
        f"Foreign-key check: {'ok' if foreign_key_ok else 'FAILED'}",
        f"Symbol-state/latest mismatches: {state_mismatches:,}",
        f"Recovery complete: {recovery_complete}",
        f"Overall verification: {'PASS' if verification_ok else 'FAIL'}",
        "",
        "Symbol classifications:",
    ]
    report.extend(f"- {key}: {value}" for key, value in sorted(classifications.items()))
    report.extend(["", "Per-symbol results:"])
    for item in symbol_results:
        suffix = f"; {item.error_description}" if item.error_description else ""
        report.append(
            f"- {item.symbol}: {item.classification}; bars={item.bars_returned:,}; "
            f"events={item.events_returned:,}; duplicates={item.duplicate_bars + item.duplicate_events:,}"
            f"{suffix}"
        )
    report.extend([
        "",
        "Safety conclusion",
        "- The legacy history.sqlite database was not opened or changed.",
    ])
    if validation_copy:
        report.extend([
            "- Only history_compact_validation.sqlite was updated.",
            "- The verified history_compact.sqlite remained unchanged.",
            "- No database promotion, replacement, rename, or deletion was performed.",
        ])
    else:
        report.extend([
            "- The verified history_compact.sqlite was updated only after explicit acknowledgment.",
            "- No database replacement, rename, or deletion was performed.",
        ])
    (run_dir / "range-recovery-report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    return run_dir, manifest



def review_existing_run(run_dir: Path) -> tuple[Path, Path]:
    """Review merged recovery bars without network or database access."""
    resolved = normalize_path(run_dir)
    merged_dir = resolved / "merged"
    if not merged_dir.is_dir():
        raise RangeRecoveryError(f"Merged recovery folder does not exist: {merged_dir}")

    rows: list[dict[str, Any]] = []
    summary: list[dict[str, Any]] = []
    for path in sorted(merged_dir.glob("*.merged.json.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        symbol = str(payload.get("symbol") or path.stem)
        bars = payload.get("bars") or []
        for bar in bars:
            ts = int(bar.get("timestamp_utc"))
            rows.append({
                "symbol": symbol,
                "timestamp_utc": ts,
                "datetime_utc": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
                "open": bar.get("open"),
                "high": bar.get("high"),
                "low": bar.get("low"),
                "close": bar.get("close"),
                "adjclose": bar.get("adjclose"),
                "volume": bar.get("volume"),
                "merged_file": path.relative_to(resolved).as_posix(),
            })
        timestamps = sorted(int(item.get("timestamp_utc")) for item in bars) if bars else []
        summary.append({
            "symbol": symbol,
            "bar_count": len(bars),
            "first_timestamp_utc": timestamps[0] if timestamps else None,
            "first_datetime_utc": datetime.fromtimestamp(timestamps[0], tz=timezone.utc).isoformat().replace("+00:00", "Z") if timestamps else None,
            "last_timestamp_utc": timestamps[-1] if timestamps else None,
            "last_datetime_utc": datetime.fromtimestamp(timestamps[-1], tz=timezone.utc).isoformat().replace("+00:00", "Z") if timestamps else None,
            "assessment": "SINGLE_BAR_ONLY" if len(bars) == 1 else ("NO_BARS" if not bars else "MULTIPLE_BARS_RETURNED"),
        })

    detail_path = resolved / "range-recovery-bar-review.csv"
    with detail_path.open("w", encoding="utf-8", newline="") as handle:
        fields = ("symbol", "timestamp_utc", "datetime_utc", "open", "high", "low", "close", "adjclose", "volume", "merged_file")
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda r: (r["symbol"], r["timestamp_utc"])))

    summary_path = resolved / "range-recovery-bar-review.txt"
    lines = [
        "Yahoo Long-History Range-Recovery Bar Review",
        f"Utility version: {UTILITY_VERSION}",
        f"Run folder name: {resolved.name}",
        "Network access: False",
        "Database opened or changed: False",
        f"Symbols reviewed: {len(summary)}",
        f"Bars reviewed: {len(rows)}",
        "",
        "Assessments:",
    ]
    counts = Counter(item["assessment"] for item in summary)
    lines.extend(f"- {key}: {value}" for key, value in sorted(counts.items()))
    lines.extend(["", "Per-symbol date range:"])
    for item in sorted(summary, key=lambda r: r["symbol"]):
        lines.append(
            f"- {item['symbol']}: bars={item['bar_count']}; first={item['first_datetime_utc']}; "
            f"last={item['last_datetime_utc']}; assessment={item['assessment']}"
        )
    lines.extend(["", "Outputs", f"- {detail_path.name}", f"- {summary_path.name}"])
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary_path, detail_path

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Recover 12 long-history range errors using explicit split windows."
    )
    parser.add_argument("--symbols-file", type=Path, default=DEFAULT_SYMBOLS_FILE)
    parser.add_argument("--review-run", type=Path)
    parser.add_argument("--through-date")
    parser.add_argument("--window-years", type=int, default=DEFAULT_WINDOW_YEARS)
    parser.add_argument("--overlap-days", type=int, default=DEFAULT_OVERLAP_DAYS)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--rebuild-dir", type=Path)
    parser.add_argument("--in-place", action="store_true")
    parser.add_argument("--acknowledge-in-place-update", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--backoff-seconds", default="2,10")
    parser.add_argument("--user-agent", default=history.DEFAULT_USER_AGENT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.review_run is not None:
            report_path, csv_path = review_existing_run(args.review_run)
            print(f"Report: {report_path}")
            print(f"CSV: {csv_path}")
            return 0
        if args.in_place and not args.acknowledge_in_place_update:
            raise RangeRecoveryError(
                "--in-place requires --acknowledge-in-place-update. Run validation-copy mode first."
            )
        if args.limit is not None and args.limit < 1:
            raise RangeRecoveryError("--limit must be at least 1.")
        symbols = load_symbols(args.symbols_file)
        if args.limit is not None:
            symbols = symbols[: args.limit]
        request_end_epoch = history.parse_through_date(args.through_date)
        windows = build_windows(
            start_epoch=history.BASELINE_START_EPOCH,
            end_epoch=request_end_epoch,
            window_years=args.window_years,
            overlap_days=args.overlap_days,
        )
        source_database, database_resolution, archive_root = compact.resolve_compact_database(
            args.database, args.rebuild_dir
        )
        requests = build_requests(symbols, windows)
        if args.dry_run:
            print(json.dumps({
                "utility_version": UTILITY_VERSION,
                "symbols": symbols,
                "symbol_count": len(symbols),
                "windows": [asdict(item) for item in windows],
                "window_count_per_symbol": len(windows),
                "planned_network_requests": len(requests),
                "validation_copy": not args.in_place,
                "source_compact_database_file": source_database.name,
                "network_requests_sent": 0,
            }, indent=2))
            return 0
        print(f"Verified compact database: {source_database} ({database_resolution})")
        print("Update target: " + (
            "verified compact database IN PLACE" if args.in_place else "separate validation copy"
        ))
        run_dir, manifest = run_recovery(
            symbols=symbols,
            windows=windows,
            source_database=source_database,
            archive_root=archive_root,
            database_resolution=database_resolution,
            symbols_file=args.symbols_file,
            in_place=args.in_place,
            overlap_days=args.overlap_days,
            concurrency=args.concurrency,
            timeout_seconds=args.timeout,
            maximum_attempts=args.max_attempts,
            backoff_seconds=history.parse_backoff(args.backoff_seconds),
            user_agent=args.user_agent,
        )
        print(f"Run folder: {run_dir}")
        print(f"Working database: {manifest['working_database_file']}")
        print(f"Recovery complete: {manifest['recovery_complete']}")
        print(f"Overall verification: {'PASS' if manifest['verification_ok'] else 'FAIL'}")
        print("Primary report: range-recovery-report.txt")
        return 0 if manifest["verification_ok"] else 3
    except (
        RangeRecoveryError,
        compact.CompactUpdateError,
        history.HistoryInputError,
        history.YahooSessionError,
        sqlite3.Error,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
