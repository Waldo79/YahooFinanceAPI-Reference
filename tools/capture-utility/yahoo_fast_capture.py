#!/usr/bin/env python3
"""High-volume Yahoo Finance capture utility candidate.

Version 0.5.0-candidate.3 adds external capture-storage safeguards to the
separate Fast-mode engine without replacing ``yahoo_capture.py``. It uses only
the Python 3.10+ standard library.

Supported high-volume endpoint stages:
- Quote: batched symbol requests with individual retest of batch omissions.
- QuoteSummary/Fundamental: concurrent single-symbol requests.
- Chart snapshot: concurrent single-symbol requests with a short fixed range.
- Options snapshot: concurrent single-symbol requests for the default/nearest chain.

Long history is deliberately excluded. Yahoo Finance endpoints are unofficial
and may change without notice.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import socket
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from email.message import Message
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit, parse_qsl
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

UTILITY_VERSION = "0.5.0-candidate.3"
CAPTURE_SCHEMA_VERSION = "0.5.0-candidate.3"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/150.0.0.0 Safari/537.36"
)

YAHOO_BASIC_COOKIE_URL = "https://fc.yahoo.com"
YAHOO_BASIC_CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"
YAHOO_FALLBACK_COOKIE_URL = "https://finance.yahoo.com/quote/AAPL"
YAHOO_FALLBACK_CRUMB_URL = "https://query2.finance.yahoo.com/v1/test/getcrumb"

QUOTE_BASE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"
QUOTE_SUMMARY_BASE_URL = "https://query1.finance.yahoo.com/v10/finance/quoteSummary"
CHART_BASE_URL = "https://query2.finance.yahoo.com/v8/finance/chart"
OPTIONS_BASE_URL = "https://query2.finance.yahoo.com/v7/finance/options"

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_FILE = REPOSITORY_ROOT / "data" / "high-volume" / "fast_mode_request_list_1547.csv"
LOCAL_CONFIG_FILE = REPOSITORY_ROOT / "config" / "local" / "fast_mode_local.json"
_DEFAULT_ARCHIVE_PARENT = (
    REPOSITORY_ROOT.parent.parent
    if REPOSITORY_ROOT.parent.name.casefold() == "code"
    else REPOSITORY_ROOT.parent
)
DEFAULT_EXTERNAL_OUTDIR = _DEFAULT_ARCHIVE_PARENT / "Captures" / "fast-mode"
OUTPUT_ROOT_ENVIRONMENT_VARIABLE = "YAHOO_FAST_CAPTURE_ROOT"

DEFAULT_ENDPOINTS = ("quote", "quoteSummary", "chart", "options")
VALID_ENDPOINTS = frozenset(DEFAULT_ENDPOINTS)
RETRYABLE_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})
AUTH_REFRESH_HTTP_STATUSES = frozenset({401, 403})
SENSITIVE_QUERY_KEYS = frozenset({"crumb", "token", "authorization", "auth", "cookie", "session"})
SYMBOL_RE = re.compile(r"^[A-Za-z0-9.^=\-]+$")
NOT_FOUND_RE = re.compile(
    r"(?:not\s+found|symbol\s+may\s+be\s+delisted|no\s+data\s+found|"
    r"invalid\s+symbol|symbol\s+not\s+available|unknown\s+symbol)",
    re.IGNORECASE,
)
NO_FUNDAMENTALS_RE = re.compile(r"no\s+fundamentals\s+data", re.IGNORECASE)


class FastCaptureInputError(ValueError):
    """Raised before any network request when an input is invalid."""


class YahooSessionError(RuntimeError):
    """Raised when an anonymous Yahoo cookie-and-crumb session cannot be prepared."""


def normalize_path(path: Path, *, relative_to: Path | None = None) -> Path:
    """Expand environment/user markers and return a non-strict absolute path."""
    expanded = Path(os.path.expandvars(str(path))).expanduser()
    if not expanded.is_absolute() and relative_to is not None:
        expanded = relative_to / expanded
    return expanded.resolve(strict=False)


def path_is_within(path: Path, parent: Path) -> bool:
    """Return True when *path* is equal to or contained by *parent*."""
    try:
        normalize_path(path).relative_to(normalize_path(parent))
        return True
    except ValueError:
        return False


def validate_external_output_root(path: Path) -> Path:
    """Reject raw-capture destinations inside the synchronized repository."""
    resolved = normalize_path(path)
    if path_is_within(resolved, REPOSITORY_ROOT):
        raise FastCaptureInputError(
            "Fast-mode raw captures must be stored outside the synchronized repository. "
            f"Choose an external output root instead of: {resolved}"
        )
    return resolved


def load_local_output_root(config_path: Path) -> Path | None:
    """Read the ignored local output-root configuration, when present."""
    config_path = normalize_path(config_path)
    if not config_path.exists():
        return None
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FastCaptureInputError(f"Cannot read local Fast-mode config {config_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise FastCaptureInputError(f"Local Fast-mode config must contain a JSON object: {config_path}")
    value = payload.get("output_root")
    if not isinstance(value, str) or not value.strip():
        raise FastCaptureInputError(f"Local Fast-mode config is missing a non-empty output_root: {config_path}")
    return normalize_path(Path(value.strip()), relative_to=config_path.parent)


def resolve_output_root(
    cli_output_root: Path | None,
    *,
    config_path: Path = LOCAL_CONFIG_FILE,
    environment: Mapping[str, str] | None = None,
) -> tuple[Path, str]:
    """Resolve output storage using CLI, environment, local config, then safe default."""
    if cli_output_root is not None:
        return validate_external_output_root(cli_output_root), "command_line"
    env = os.environ if environment is None else environment
    environment_value = env.get(OUTPUT_ROOT_ENVIRONMENT_VARIABLE, "").strip()
    if environment_value:
        return validate_external_output_root(Path(environment_value)), "environment"
    configured = load_local_output_root(config_path)
    if configured is not None:
        return validate_external_output_root(configured), "local_config"
    return validate_external_output_root(DEFAULT_EXTERNAL_OUTDIR), "safe_default"


def write_local_output_config(config_path: Path, output_root: Path) -> Path:
    """Write the ignored machine-local output-root configuration."""
    resolved_config = normalize_path(config_path)
    resolved_output = validate_external_output_root(output_root)
    resolved_config.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "output_root": str(resolved_output),
        "purpose": "Machine-local Fast-mode raw capture storage; do not commit this file.",
    }
    resolved_config.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return resolved_config


def prepare_output_root(path: Path) -> Path:
    """Create and write-test an external output root before network activity."""
    resolved = validate_external_output_root(path)
    try:
        resolved.mkdir(parents=True, exist_ok=True)
        probe = resolved / f".fast-mode-write-test-{os.getpid()}-{threading.get_ident()}"
        probe.write_text("write-test\n", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise FastCaptureInputError(f"Fast-mode output root is not writable: {resolved}: {exc}") from exc
    return resolved


def validate_resume_run(path: Path) -> Path:
    """Validate that a resumable run exists outside the synchronized repository."""
    resolved = validate_external_output_root(path)
    if not resolved.is_dir():
        raise FastCaptureInputError(f"Resume run folder does not exist: {resolved}")
    checkpoint = resolved / "checkpoint.jsonl"
    if not checkpoint.is_file():
        raise FastCaptureInputError(f"Resume run folder has no checkpoint.jsonl: {resolved}")
    return resolved


@dataclass(frozen=True)
class InputRow:
    request_sequence: int
    symbol: str
    request_occurrence: int = 1
    duplicate_control: bool = False
    control_reason: str = ""
    project_security_type: str = ""
    exchange_code: str = ""
    exchange_name: str = ""
    exchange_groups: str = ""
    source_membership: str = ""
    name: str = ""
    review_status: str = ""
    notes: str = ""


@dataclass(frozen=True)
class RetryPolicy:
    maximum_attempts: int = 3
    backoff_seconds: tuple[float, ...] = (1.0, 3.0)


@dataclass(frozen=True)
class SessionSnapshot:
    crumb: str
    cookie_header: str
    generation: int
    strategy: str


@dataclass
class AttemptRecord:
    attempt: int
    requested_at_utc: str
    response_received_at_utc: str
    elapsed_ms: int
    http_status: int | None
    error: str | None


@dataclass
class HttpResult:
    body: bytes | None
    http_status: int | None
    content_type: str
    final_url_redacted: str
    requested_at_utc: str
    response_received_at_utc: str
    elapsed_ms: int
    attempts: list[AttemptRecord]
    error_message: str | None
    session_generation: int | None


@dataclass(frozen=True)
class CaptureTask:
    task_key: str
    endpoint: str
    task_sequence: int
    rows: tuple[InputRow, ...]
    retest: bool = False


@dataclass
class RequestResult:
    request_sequence: int
    symbol: str
    endpoint: str
    task_key: str
    request_occurrence: int
    duplicate_control: bool
    classification: str
    http_status: int | None
    returned_symbol: str | None = None
    returned_symbols: tuple[str, ...] = ()
    response_result_reused_for_duplicate_occurrence: bool = False
    individual_retest_classification: str | None = None
    error_description: str | None = None


@dataclass
class TaskResult:
    task_key: str
    endpoint: str
    task_sequence: int
    requested_symbols: tuple[str, ...]
    requested_row_sequences: tuple[int, ...]
    result_classification: str
    http_status: int | None
    returned_symbols: tuple[str, ...]
    request_results: list[RequestResult]
    raw_response_file: str | None
    raw_response_sha256: str | None
    raw_response_bytes: int
    metadata_file: str
    attempts: list[AttemptRecord]
    elapsed_ms: int
    error_message: str | None
    error_description: str | None
    retest: bool = False


@dataclass(frozen=True)
class EndpointSettings:
    concurrency: int
    quote_batch_size: int = 100
    chart_range: str = "5d"
    chart_interval: str = "1d"
    quote_summary_modules: tuple[str, ...] = (
        "summaryDetail",
        "calendarEvents",
        "defaultKeyStatistics",
        "summaryProfile",
        "quoteType",
        "financialData",
    )


@dataclass
class RunState:
    run_dir: Path
    checkpoint_path: Path
    checkpoint_lock: threading.Lock = field(default_factory=threading.Lock)


class SharedBackoffGate:
    """Endpoint-wide backoff gate shared by concurrent workers.

    It does not resize the worker pool. It temporarily pauses all workers after
    retryable throttling responses and gradually clears the penalty after
    successful requests.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._blocked_until = 0.0
        self._penalty_level = 0

    def wait(self) -> None:
        while True:
            with self._lock:
                delay = self._blocked_until - self._clock()
            if delay <= 0:
                return
            self._sleep(min(delay, 1.0))

    def on_throttle(self) -> None:
        with self._lock:
            self._penalty_level = min(self._penalty_level + 1, 6)
            delay = float(2 ** (self._penalty_level - 1))
            self._blocked_until = max(self._blocked_until, self._clock() + delay)

    def on_success(self) -> None:
        with self._lock:
            if self._penalty_level:
                self._penalty_level -= 1


class YahooAnonymousSession:
    """Thread-safe provider of an in-memory cookie/crumb snapshot.

    Cookie and crumb values are never returned by ``public_summary`` and are
    never written to output files. Network workers receive immutable snapshots
    and send the cookie header without mutating the shared CookieJar.
    """

    def __init__(
        self,
        *,
        user_agent: str,
        timeout_seconds: float,
        opener: Callable[..., Any] | None = None,
        cookie_jar: CookieJar | None = None,
    ):
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.cookie_jar = cookie_jar or CookieJar()
        self._bootstrap_open = opener or build_opener(HTTPCookieProcessor(self.cookie_jar)).open
        self._lock = threading.RLock()
        self._snapshot: SessionSnapshot | None = None
        self._refresh_count = 0
        self._last_error: str | None = None

    @staticmethod
    def _read_response(response: Any) -> tuple[int, bytes, str, str]:
        status = int(getattr(response, "status", response.getcode()))
        body = response.read()
        content_type = response.headers.get("Content-Type", "") if getattr(response, "headers", None) else ""
        final_url = getattr(response, "url", "")
        return status, body, content_type, final_url

    def _open_bootstrap(self, url: str, *, accept: str) -> tuple[int, bytes]:
        request = Request(url, headers={"User-Agent": self.user_agent, "Accept": accept})
        try:
            with self._bootstrap_open(request, timeout=self.timeout_seconds) as response:
                status, body, _, _ = self._read_response(response)
                return status, body
        except HTTPError as exc:
            body = exc.read() if hasattr(exc, "read") else b""
            return int(exc.code), body
        except (URLError, TimeoutError, socket.timeout, OSError) as exc:
            raise YahooSessionError(f"Yahoo session bootstrap failed: {type(exc).__name__}: {exc}") from exc

    @staticmethod
    def _validate_crumb(body: bytes) -> str:
        crumb = body.decode("utf-8", errors="replace").strip()
        lowered = crumb.lower()
        if not crumb or len(crumb) > 512 or "too many requests" in lowered:
            raise YahooSessionError("Yahoo crumb endpoint returned an invalid value.")
        if lowered.startswith("<!doctype") or lowered.startswith("<html") or "\r" in crumb or "\n" in crumb:
            raise YahooSessionError("Yahoo crumb endpoint returned HTML or a multiline value.")
        return crumb

    def _cookie_header(self) -> str:
        return "; ".join(f"{cookie.name}={cookie.value}" for cookie in self.cookie_jar)

    def _prepare_locked(self, *, force: bool) -> SessionSnapshot:
        if self._snapshot is not None and not force:
            return self._snapshot
        if force:
            self._refresh_count += 1
            self._snapshot = None
            try:
                self.cookie_jar.clear()
            except Exception:
                pass

        errors: list[str] = []
        strategies = (
            ("basic-query1", YAHOO_BASIC_COOKIE_URL, YAHOO_BASIC_CRUMB_URL),
            ("finance-query2-fallback", YAHOO_FALLBACK_COOKIE_URL, YAHOO_FALLBACK_CRUMB_URL),
        )
        for strategy, cookie_url, crumb_url in strategies:
            cookie_status, _ = self._open_bootstrap(cookie_url, accept="text/html,application/xhtml+xml,*/*")
            if cookie_status == 429:
                errors.append(f"{strategy}: cookie bootstrap HTTP 429")
                continue
            crumb_status, crumb_body = self._open_bootstrap(crumb_url, accept="text/plain,*/*")
            if not 200 <= crumb_status < 300:
                errors.append(f"{strategy}: crumb HTTP {crumb_status}")
                continue
            try:
                crumb = self._validate_crumb(crumb_body)
            except YahooSessionError as exc:
                errors.append(f"{strategy}: {exc}")
                continue
            generation = 1 if self._snapshot is None else self._snapshot.generation + 1
            # Generation must also advance after a forced refresh when _snapshot was cleared.
            generation = self._refresh_count + 1
            snapshot = SessionSnapshot(
                crumb=crumb,
                cookie_header=self._cookie_header(),
                generation=generation,
                strategy=strategy,
            )
            self._snapshot = snapshot
            self._last_error = None
            return snapshot

        self._last_error = "Could not establish anonymous Yahoo session: " + "; ".join(errors)
        raise YahooSessionError(self._last_error)

    def snapshot(self) -> SessionSnapshot:
        with self._lock:
            return self._prepare_locked(force=False)

    def refresh_after_auth_error(self, observed_generation: int) -> SessionSnapshot:
        with self._lock:
            current = self._prepare_locked(force=False)
            if current.generation != observed_generation:
                return current
            if self._refresh_count >= 1:
                return current
            return self._prepare_locked(force=True)

    def public_summary(self) -> dict[str, Any]:
        with self._lock:
            snapshot = self._snapshot
            return {
                "mode": "anonymous-cookie-crumb",
                "strategy": snapshot.strategy if snapshot else None,
                "cookie_count": sum(1 for _ in self.cookie_jar),
                "crumb_present": snapshot is not None,
                "refresh_count": self._refresh_count,
                "last_error": self._last_error,
                "sensitive_values_persisted": False,
            }


class StaticSession:
    """Testing/diagnostic session with a non-secret fixed snapshot."""

    def __init__(self, crumb: str = "test-crumb", cookie_header: str = ""):
        self._snapshot = SessionSnapshot(crumb, cookie_header, 1, "static")
        self.refresh_count = 0

    def snapshot(self) -> SessionSnapshot:
        return self._snapshot

    def refresh_after_auth_error(self, observed_generation: int) -> SessionSnapshot:
        self.refresh_count += 1
        self._snapshot = SessionSnapshot(self._snapshot.crumb, self._snapshot.cookie_header, self._snapshot.generation + 1, "static")
        return self._snapshot

    def public_summary(self) -> dict[str, Any]:
        return {
            "mode": "static",
            "strategy": "static",
            "cookie_count": 0,
            "crumb_present": True,
            "refresh_count": self.refresh_count,
            "last_error": None,
            "sensitive_values_persisted": False,
        }


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def filename_utc(value: datetime) -> str:
    return format_utc(value).replace(":", "-")


def parse_bool(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def validate_symbol(symbol: str, *, row_number: int | None = None) -> str:
    clean = symbol.strip()
    prefix = f"Row {row_number}: " if row_number is not None else ""
    if not clean:
        raise FastCaptureInputError(prefix + "symbol is blank.")
    if "://" in clean or clean.lower().startswith(("http://", "https://")):
        raise FastCaptureInputError(prefix + f"full URLs are not valid symbols: {clean!r}.")
    if not SYMBOL_RE.fullmatch(clean):
        raise FastCaptureInputError(prefix + f"symbol contains unsupported characters: {clean!r}.")
    return clean


def load_input_rows(path: Path) -> list[InputRow]:
    if not path.exists():
        raise FastCaptureInputError(f"Input file not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise FastCaptureInputError("Input CSV has no header row.")
        names = {name.strip() for name in reader.fieldnames if name}
        if "symbol" not in names:
            raise FastCaptureInputError("Input CSV must contain a 'symbol' column.")
        rows: list[InputRow] = []
        for row_number, raw in enumerate(reader, start=2):
            normalized = {(key or "").strip(): (value or "").strip() for key, value in raw.items()}
            symbol = validate_symbol(normalized.get("symbol", ""), row_number=row_number)
            request_sequence_text = normalized.get("request_sequence", "")
            request_sequence = int(request_sequence_text) if request_sequence_text else len(rows) + 1
            occurrence_text = normalized.get("request_occurrence", "")
            occurrence = int(occurrence_text) if occurrence_text else 1
            rows.append(
                InputRow(
                    request_sequence=request_sequence,
                    symbol=symbol,
                    request_occurrence=occurrence,
                    duplicate_control=parse_bool(normalized.get("duplicate_control")),
                    control_reason=normalized.get("control_reason", ""),
                    project_security_type=normalized.get("project_security_type", ""),
                    exchange_code=normalized.get("exchange_code", ""),
                    exchange_name=normalized.get("exchange_name", ""),
                    exchange_groups=normalized.get("exchange_groups", ""),
                    source_membership=normalized.get("source_membership", ""),
                    name=normalized.get("name", ""),
                    review_status=normalized.get("review_status", ""),
                    notes=normalized.get("notes", ""),
                )
            )
    if not rows:
        raise FastCaptureInputError("Input CSV contains no symbol rows.")
    sequences = [row.request_sequence for row in rows]
    if len(sequences) != len(set(sequences)):
        raise FastCaptureInputError("request_sequence values must be unique.")
    return rows


def select_smoke_rows(rows: Sequence[InputRow], target_count: int = 30) -> list[InputRow]:
    """Choose a deterministic smoke set that includes both duplicate occurrences."""
    duplicate_symbols = {row.symbol for row in rows if row.duplicate_control}
    selected: list[InputRow] = [row for row in rows if row.symbol in duplicate_symbols]
    selected_sequences = {row.request_sequence for row in selected}
    for row in rows:
        if len(selected) >= target_count:
            break
        if row.request_sequence not in selected_sequences:
            selected.append(row)
            selected_sequences.add(row.request_sequence)
    selected.sort(key=lambda row: row.request_sequence)
    return selected[:target_count]


def chunks(values: Sequence[InputRow], size: int) -> Iterable[tuple[InputRow, ...]]:
    if size < 1:
        raise FastCaptureInputError("quote batch size must be at least 1.")
    for index in range(0, len(values), size):
        yield tuple(values[index : index + size])


def redact_url(url: str) -> str:
    parts = urlsplit(url)
    safe_query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        safe_query.append((key, "REDACTED" if key.lower() in SENSITIVE_QUERY_KEYS else value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(safe_query, doseq=True), parts.fragment))


def encode_path_symbol(symbol: str) -> str:
    return quote(symbol, safe="")


def build_endpoint_url(task: CaptureTask, snapshot: SessionSnapshot, settings: EndpointSettings) -> str:
    symbols = [row.symbol for row in task.rows]
    if task.endpoint == "quote":
        query = {
            "symbols": ",".join(symbols),
            "formatted": "false",
            "lang": "en-US",
            "region": "US",
            "crumb": snapshot.crumb,
        }
        return QUOTE_BASE_URL + "?" + urlencode(query)
    if len(symbols) != 1:
        raise FastCaptureInputError(f"{task.endpoint} tasks require one symbol; got {len(symbols)}.")
    symbol_path = encode_path_symbol(symbols[0])
    if task.endpoint == "quoteSummary":
        query = {
            "modules": ",".join(settings.quote_summary_modules),
            "formatted": "false",
            "crumb": snapshot.crumb,
        }
        return f"{QUOTE_SUMMARY_BASE_URL}/{symbol_path}?{urlencode(query)}"
    if task.endpoint == "chart":
        query = {
            "range": settings.chart_range,
            "interval": settings.chart_interval,
            "includePrePost": "false",
            "events": "div,splits,capitalGains",
            "crumb": snapshot.crumb,
        }
        return f"{CHART_BASE_URL}/{symbol_path}?{urlencode(query)}"
    if task.endpoint == "options":
        return f"{OPTIONS_BASE_URL}/{symbol_path}?{urlencode({'crumb': snapshot.crumb})}"
    raise FastCaptureInputError(f"Unsupported endpoint: {task.endpoint}")


def _content_type(headers: Any) -> str:
    if headers is None:
        return ""
    try:
        return headers.get("Content-Type", "") or ""
    except Exception:
        return ""


def request_with_retry(
    task: CaptureTask,
    *,
    session: Any,
    settings: EndpointSettings,
    timeout_seconds: float,
    retry_policy: RetryPolicy,
    user_agent: str,
    gate: SharedBackoffGate,
    opener: Callable[..., Any] = urlopen,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = utc_now,
) -> HttpResult:
    attempts: list[AttemptRecord] = []
    final_body: bytes | None = None
    final_status: int | None = None
    final_content_type = ""
    final_url_redacted = ""
    final_error: str | None = None
    final_generation: int | None = None
    overall_started = clock()
    auth_refresh_attempted = False

    for attempt_number in range(1, retry_policy.maximum_attempts + 1):
        gate.wait()
        snapshot = session.snapshot()
        final_generation = snapshot.generation
        url = build_endpoint_url(task, snapshot, settings)
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
            AttemptRecord(
                attempt=attempt_number,
                requested_at_utc=format_utc(started_at),
                response_received_at_utc=format_utc(ended_at),
                elapsed_ms=elapsed_ms,
                http_status=status,
                error=error,
            )
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
            if attempt_number < retry_policy.maximum_attempts:
                continue

        retryable = status in RETRYABLE_HTTP_STATUSES or status is None
        if status == 429:
            gate.on_throttle()
        if retryable and attempt_number < retry_policy.maximum_attempts:
            delay_index = min(attempt_number - 1, len(retry_policy.backoff_seconds) - 1)
            delay = retry_policy.backoff_seconds[delay_index] if retry_policy.backoff_seconds else 0.0
            if delay > 0:
                sleep(delay)
            continue
        break

    total_elapsed = max(0, int(round((clock() - overall_started) * 1000)))
    return HttpResult(
        body=final_body,
        http_status=final_status,
        content_type=final_content_type,
        final_url_redacted=final_url_redacted,
        requested_at_utc=attempts[0].requested_at_utc if attempts else format_utc(now()),
        response_received_at_utc=attempts[-1].response_received_at_utc if attempts else format_utc(now()),
        elapsed_ms=total_elapsed,
        attempts=attempts,
        error_message=final_error,
        session_generation=final_generation,
    )


def _json_or_error(body: bytes | None) -> tuple[Any | None, str | None]:
    if body is None:
        return None, "No response body was captured."
    try:
        return json.loads(body.decode("utf-8-sig")), None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _error_parts(error_obj: Any) -> tuple[str | None, str | None]:
    if not isinstance(error_obj, Mapping):
        return None, None
    code = error_obj.get("code")
    description = error_obj.get("description")
    return str(code) if code is not None else None, str(description) if description is not None else None


def _transport_classification(http: HttpResult) -> str | None:
    status = http.http_status
    if status is None:
        return "NETWORK_OR_TIMEOUT_ERROR"
    if not 200 <= status < 300:
        return f"HTTP_ERROR_{status}"
    return None


def _description_is_not_found(code: str | None, description: str | None) -> bool:
    combined = " ".join(part for part in (code, description) if part)
    return bool(NOT_FOUND_RE.search(combined))


def analyze_task(task: CaptureTask, http: HttpResult) -> tuple[str, tuple[str, ...], list[RequestResult], str | None]:
    transport = _transport_classification(http)
    if transport:
        rows = [
            RequestResult(
                request_sequence=row.request_sequence,
                symbol=row.symbol,
                endpoint=task.endpoint,
                task_key=task.task_key,
                request_occurrence=row.request_occurrence,
                duplicate_control=row.duplicate_control,
                classification=transport,
                http_status=http.http_status,
                error_description=http.error_message,
            )
            for row in task.rows
        ]
        return transport, (), rows, http.error_message

    data, parse_error = _json_or_error(http.body)
    if parse_error:
        rows = [
            RequestResult(
                request_sequence=row.request_sequence,
                symbol=row.symbol,
                endpoint=task.endpoint,
                task_key=task.task_key,
                request_occurrence=row.request_occurrence,
                duplicate_control=row.duplicate_control,
                classification="JSON_PARSE_ERROR",
                http_status=http.http_status,
                error_description=parse_error,
            )
            for row in task.rows
        ]
        return "JSON_PARSE_ERROR", (), rows, parse_error

    if task.endpoint == "quote":
        container = data.get("quoteResponse", {}) if isinstance(data, Mapping) else {}
        result = container.get("result") if isinstance(container, Mapping) else None
        code, description = _error_parts(container.get("error") if isinstance(container, Mapping) else None)
        returned_symbols = tuple(
            str(item.get("symbol"))
            for item in (result or [])
            if isinstance(item, Mapping) and item.get("symbol") is not None
        )
        returned_set = set(returned_symbols)
        requested_counts = Counter(row.symbol for row in task.rows)
        request_results: list[RequestResult] = []
        for row in task.rows:
            if row.symbol in returned_set:
                classification = "SUCCESS_RESULT_RETURNED"
            elif _description_is_not_found(code, description):
                classification = "SYMBOL_NOT_AVAILABLE"
            elif not result:
                classification = "EMPTY_RESULT_SYMBOL_NOT_RETURNED"
            else:
                classification = "REQUESTED_SYMBOL_MISSING_FROM_RESULT"
            request_results.append(
                RequestResult(
                    request_sequence=row.request_sequence,
                    symbol=row.symbol,
                    endpoint=task.endpoint,
                    task_key=task.task_key,
                    request_occurrence=row.request_occurrence,
                    duplicate_control=row.duplicate_control,
                    classification=classification,
                    http_status=http.http_status,
                    returned_symbol=row.symbol if row.symbol in returned_set else None,
                    returned_symbols=returned_symbols,
                    response_result_reused_for_duplicate_occurrence=(
                        row.request_occurrence > 1
                        and requested_counts[row.symbol] > 1
                        and row.symbol in returned_set
                    ),
                    error_description=description,
                )
            )
        task_classification = (
            "SUCCESS_RESULT_RETURNED"
            if all(item.classification == "SUCCESS_RESULT_RETURNED" for item in request_results)
            else "PARTIAL_SYMBOL_RESULT"
            if any(item.classification == "SUCCESS_RESULT_RETURNED" for item in request_results)
            else request_results[0].classification if request_results else "EMPTY_RESULT"
        )
        return task_classification, returned_symbols, request_results, description

    if task.endpoint == "quoteSummary":
        container = data.get("quoteSummary", {}) if isinstance(data, Mapping) else {}
        result = container.get("result") if isinstance(container, Mapping) else None
        code, description = _error_parts(container.get("error") if isinstance(container, Mapping) else None)
        symbol = task.rows[0].symbol
        returned_symbol: str | None = None
        if isinstance(result, list) and result:
            first = result[0]
            if isinstance(first, Mapping):
                quote_type = first.get("quoteType")
                if isinstance(quote_type, Mapping) and quote_type.get("symbol"):
                    returned_symbol = str(quote_type["symbol"])
            classification = "SUCCESS_RESULT_RETURNED"
        elif description and NO_FUNDAMENTALS_RE.search(description):
            classification = "NO_FUNDAMENTALS_AVAILABLE"
        elif _description_is_not_found(code, description):
            classification = "SYMBOL_NOT_AVAILABLE"
        elif result is None:
            classification = "NULL_RESULT"
        else:
            classification = "EMPTY_RESULT"
        rr = RequestResult(
            request_sequence=task.rows[0].request_sequence,
            symbol=symbol,
            endpoint=task.endpoint,
            task_key=task.task_key,
            request_occurrence=task.rows[0].request_occurrence,
            duplicate_control=task.rows[0].duplicate_control,
            classification=classification,
            http_status=http.http_status,
            returned_symbol=returned_symbol,
            returned_symbols=(returned_symbol,) if returned_symbol else (),
            error_description=description,
        )
        return classification, rr.returned_symbols, [rr], description

    if task.endpoint == "chart":
        container = data.get("chart", {}) if isinstance(data, Mapping) else {}
        result = container.get("result") if isinstance(container, Mapping) else None
        code, description = _error_parts(container.get("error") if isinstance(container, Mapping) else None)
        returned_symbol: str | None = None
        if isinstance(result, list) and result:
            first = result[0]
            if isinstance(first, Mapping):
                meta = first.get("meta")
                if isinstance(meta, Mapping) and meta.get("symbol"):
                    returned_symbol = str(meta["symbol"])
            classification = "SUCCESS_RESULT_RETURNED"
        elif _description_is_not_found(code, description):
            classification = "SYMBOL_NOT_AVAILABLE"
        elif result is None:
            classification = "NULL_RESULT"
        else:
            classification = "EMPTY_RESULT"
        rr = RequestResult(
            request_sequence=task.rows[0].request_sequence,
            symbol=task.rows[0].symbol,
            endpoint=task.endpoint,
            task_key=task.task_key,
            request_occurrence=task.rows[0].request_occurrence,
            duplicate_control=task.rows[0].duplicate_control,
            classification=classification,
            http_status=http.http_status,
            returned_symbol=returned_symbol,
            returned_symbols=(returned_symbol,) if returned_symbol else (),
            error_description=description,
        )
        return classification, rr.returned_symbols, [rr], description

    if task.endpoint == "options":
        container = data.get("optionChain", {}) if isinstance(data, Mapping) else {}
        result = container.get("result") if isinstance(container, Mapping) else None
        code, description = _error_parts(container.get("error") if isinstance(container, Mapping) else None)
        returned_symbol: str | None = None
        if isinstance(result, list) and result:
            first = result[0]
            option_payload_present = False
            if isinstance(first, Mapping):
                quote_obj = first.get("quote")
                if isinstance(quote_obj, Mapping) and quote_obj.get("symbol"):
                    returned_symbol = str(quote_obj["symbol"])
                expiration_dates = first.get("expirationDates")
                option_sets = first.get("options")
                option_payload_present = bool(expiration_dates) or bool(option_sets)
            classification = (
                "SUCCESS_RESULT_RETURNED"
                if option_payload_present
                else "NOT_OPTIONABLE_OR_NO_CHAIN"
            )
        elif _description_is_not_found(code, description):
            classification = "SYMBOL_NOT_AVAILABLE"
        elif result == []:
            classification = "NOT_OPTIONABLE_OR_NO_CHAIN"
        elif result is None:
            classification = "NULL_RESULT"
        else:
            classification = "EMPTY_RESULT"
        rr = RequestResult(
            request_sequence=task.rows[0].request_sequence,
            symbol=task.rows[0].symbol,
            endpoint=task.endpoint,
            task_key=task.task_key,
            request_occurrence=task.rows[0].request_occurrence,
            duplicate_control=task.rows[0].duplicate_control,
            classification=classification,
            http_status=http.http_status,
            returned_symbol=returned_symbol,
            returned_symbols=(returned_symbol,) if returned_symbol else (),
            error_description=description,
        )
        return classification, rr.returned_symbols, [rr], description

    raise FastCaptureInputError(f"Unsupported endpoint analysis: {task.endpoint}")


def safe_filename(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return text or "item"


def write_task_result(
    run_state: RunState,
    task: CaptureTask,
    http: HttpResult,
    *,
    now: Callable[[], datetime] = utc_now,
) -> TaskResult:
    classification, returned_symbols, request_results, error_description = analyze_task(task, http)
    endpoint_dir = safe_filename(task.endpoint)
    raw_relative: str | None = None
    raw_sha: str | None = None
    raw_size = 0
    if http.body is not None:
        raw_relative = f"raw/{endpoint_dir}/{safe_filename(task.task_key)}.raw.json"
        raw_path = run_state.run_dir / raw_relative
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(http.body)
        raw_sha = hashlib.sha256(http.body).hexdigest()
        raw_size = len(http.body)

    metadata_relative = f"metadata/{endpoint_dir}/{safe_filename(task.task_key)}.metadata.json"
    metadata_path = run_state.run_dir / metadata_relative
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    result = TaskResult(
        task_key=task.task_key,
        endpoint=task.endpoint,
        task_sequence=task.task_sequence,
        requested_symbols=tuple(row.symbol for row in task.rows),
        requested_row_sequences=tuple(row.request_sequence for row in task.rows),
        result_classification=classification,
        http_status=http.http_status,
        returned_symbols=returned_symbols,
        request_results=request_results,
        raw_response_file=raw_relative,
        raw_response_sha256=raw_sha,
        raw_response_bytes=raw_size,
        metadata_file=metadata_relative,
        attempts=http.attempts,
        elapsed_ms=http.elapsed_ms,
        error_message=http.error_message,
        error_description=error_description,
        retest=task.retest,
    )
    metadata = asdict(result)
    metadata["captured_at_utc"] = format_utc(now())
    metadata["request_url_redacted"] = http.final_url_redacted
    metadata["content_type"] = http.content_type
    metadata["session_generation"] = http.session_generation
    metadata["sensitive_values_persisted"] = False
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    append_checkpoint(run_state, result)
    return result


def append_checkpoint(run_state: RunState, result: TaskResult) -> None:
    payload = asdict(result)
    with run_state.checkpoint_lock:
        run_state.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        with run_state.checkpoint_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def task_result_from_dict(data: Mapping[str, Any]) -> TaskResult:
    request_results = [RequestResult(**item) for item in data.get("request_results", [])]
    attempts = [AttemptRecord(**item) for item in data.get("attempts", [])]
    return TaskResult(
        task_key=str(data["task_key"]),
        endpoint=str(data["endpoint"]),
        task_sequence=int(data["task_sequence"]),
        requested_symbols=tuple(data.get("requested_symbols", [])),
        requested_row_sequences=tuple(int(v) for v in data.get("requested_row_sequences", [])),
        result_classification=str(data["result_classification"]),
        http_status=data.get("http_status"),
        returned_symbols=tuple(data.get("returned_symbols", [])),
        request_results=request_results,
        raw_response_file=data.get("raw_response_file"),
        raw_response_sha256=data.get("raw_response_sha256"),
        raw_response_bytes=int(data.get("raw_response_bytes", 0)),
        metadata_file=str(data["metadata_file"]),
        attempts=attempts,
        elapsed_ms=int(data.get("elapsed_ms", 0)),
        error_message=data.get("error_message"),
        error_description=data.get("error_description"),
        retest=bool(data.get("retest", False)),
    )


def load_checkpoint(path: Path) -> dict[str, TaskResult]:
    completed: dict[str, TaskResult] = {}
    if not path.exists():
        return completed
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                result = task_result_from_dict(payload)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise FastCaptureInputError(f"Invalid checkpoint line {line_number}: {exc}") from exc
            completed[result.task_key] = result
    return completed


def build_tasks(rows: Sequence[InputRow], endpoints: Sequence[str], settings: EndpointSettings) -> list[CaptureTask]:
    tasks: list[CaptureTask] = []
    sequence = 1
    for endpoint in endpoints:
        if endpoint == "quote":
            for batch_index, batch in enumerate(chunks(rows, settings.quote_batch_size), start=1):
                tasks.append(CaptureTask(f"quote-batch-{batch_index:05d}", endpoint, sequence, batch))
                sequence += 1
        else:
            for row in rows:
                tasks.append(
                    CaptureTask(
                        f"{endpoint}-{row.request_sequence:06d}-{safe_filename(row.symbol)}",
                        endpoint,
                        sequence,
                        (row,),
                    )
                )
                sequence += 1
    return tasks


def execute_task(
    task: CaptureTask,
    *,
    run_state: RunState,
    session: Any,
    settings: EndpointSettings,
    timeout_seconds: float,
    retry_policy: RetryPolicy,
    user_agent: str,
    gate: SharedBackoffGate,
    opener: Callable[..., Any],
    sleep: Callable[[float], None],
    clock: Callable[[], float],
    now: Callable[[], datetime],
) -> TaskResult:
    http = request_with_retry(
        task,
        session=session,
        settings=settings,
        timeout_seconds=timeout_seconds,
        retry_policy=retry_policy,
        user_agent=user_agent,
        gate=gate,
        opener=opener,
        sleep=sleep,
        clock=clock,
        now=now,
    )
    return write_task_result(run_state, task, http, now=now)


def run_task_stage(
    tasks: Sequence[CaptureTask],
    *,
    concurrency: int,
    completed: Mapping[str, TaskResult],
    run_state: RunState,
    session: Any,
    settings: EndpointSettings,
    timeout_seconds: float,
    retry_policy: RetryPolicy,
    user_agent: str,
    opener: Callable[..., Any],
    sleep: Callable[[float], None],
    clock: Callable[[], float],
    now: Callable[[], datetime],
    progress: Callable[[str], None],
) -> list[TaskResult]:
    results: list[TaskResult] = []
    pending = [task for task in tasks if task.task_key not in completed]
    results.extend(completed[task.task_key] for task in tasks if task.task_key in completed)
    if not pending:
        return sorted(results, key=lambda result: result.task_sequence)

    gate = SharedBackoffGate(clock=clock, sleep=sleep)
    total = len(tasks)
    done_count = len(results)
    with ThreadPoolExecutor(max_workers=max(1, concurrency), thread_name_prefix="yf-fast") as pool:
        future_map: dict[Future[TaskResult], CaptureTask] = {
            pool.submit(
                execute_task,
                task,
                run_state=run_state,
                session=session,
                settings=settings,
                timeout_seconds=timeout_seconds,
                retry_policy=retry_policy,
                user_agent=user_agent,
                gate=gate,
                opener=opener,
                sleep=sleep,
                clock=clock,
                now=now,
            ): task
            for task in pending
        }
        for future in as_completed(future_map):
            task = future_map[future]
            try:
                result = future.result()
            except Exception as exc:  # Defensive: record worker failure and continue.
                http = HttpResult(
                    body=None,
                    http_status=None,
                    content_type="",
                    final_url_redacted="",
                    requested_at_utc=format_utc(now()),
                    response_received_at_utc=format_utc(now()),
                    elapsed_ms=0,
                    attempts=[],
                    error_message=f"WORKER_EXCEPTION: {type(exc).__name__}: {exc}",
                    session_generation=None,
                )
                result = write_task_result(run_state, task, http, now=now)
            results.append(result)
            done_count += 1
            progress(
                f"[{done_count:05d}/{total:05d}] {task.endpoint:<12} "
                f"{','.join(row.symbol for row in task.rows[:2]):<24} {result.result_classification}"
            )
    return sorted(results, key=lambda result: result.task_sequence)


def quote_missing_rows(results: Sequence[TaskResult], row_lookup: Mapping[int, InputRow]) -> list[InputRow]:
    missing_sequences: list[int] = []
    for result in results:
        if result.endpoint != "quote" or result.retest:
            continue
        for request_result in result.request_results:
            if request_result.classification in {
                "REQUESTED_SYMBOL_MISSING_FROM_RESULT",
                "EMPTY_RESULT_SYMBOL_NOT_RETURNED",
            }:
                missing_sequences.append(request_result.request_sequence)
    return [row_lookup[seq] for seq in sorted(set(missing_sequences))]


def build_quote_retest_tasks(rows: Sequence[InputRow], starting_sequence: int) -> list[CaptureTask]:
    tasks: list[CaptureTask] = []
    for offset, row in enumerate(rows):
        tasks.append(
            CaptureTask(
                task_key=f"quote-retest-{row.request_sequence:06d}-{safe_filename(row.symbol)}",
                endpoint="quote",
                task_sequence=starting_sequence + offset,
                rows=(row,),
                retest=True,
            )
        )
    return tasks


def apply_quote_retests(primary: Sequence[TaskResult], retests: Sequence[TaskResult]) -> None:
    retest_by_sequence: dict[int, RequestResult] = {}
    for result in retests:
        for item in result.request_results:
            retest_by_sequence[item.request_sequence] = item
    for result in primary:
        if result.endpoint != "quote" or result.retest:
            continue
        for item in result.request_results:
            retest = retest_by_sequence.get(item.request_sequence)
            if retest is None:
                continue
            item.individual_retest_classification = retest.classification
            if item.classification in {
                "REQUESTED_SYMBOL_MISSING_FROM_RESULT",
                "EMPTY_RESULT_SYMBOL_NOT_RETURNED",
            }:
                if retest.classification == "SUCCESS_RESULT_RETURNED":
                    item.classification = "BATCH_OMISSION_INDIVIDUAL_SUCCESS"
                elif retest.classification == "SYMBOL_NOT_AVAILABLE":
                    item.classification = "SYMBOL_NOT_AVAILABLE"


def persist_retest_updates(run_state: RunState, primary_results: Sequence[TaskResult]) -> None:
    """Rewrite primary Quote metadata/checkpoint after individual retest reconciliation."""
    for result in primary_results:
        if result.endpoint != "quote" or result.retest:
            continue
        if not any(item.individual_retest_classification for item in result.request_results):
            continue
        metadata_path = run_state.run_dir / result.metadata_file
        existing: dict[str, Any] = {}
        if metadata_path.exists():
            try:
                loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    existing = loaded
            except (json.JSONDecodeError, OSError):
                existing = {}
        existing.update(asdict(result))
        existing["retest_reconciled"] = True
        existing["sensitive_values_persisted"] = False
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        # Appending the same task key is intentional; load_checkpoint keeps the
        # last record for that key.
        append_checkpoint(run_state, result)


def serialize_input_row(row: InputRow) -> dict[str, Any]:
    return asdict(row)


def create_run_dir(outdir: Path, now: Callable[[], datetime]) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    base = filename_utc(now()) + "_fast-run"
    candidate = outdir / base
    suffix = 1
    while candidate.exists():
        candidate = outdir / f"{base}-{suffix:02d}"
        suffix += 1
    candidate.mkdir(parents=True)
    return candidate


def status_is_review(classification: str) -> bool:
    normal = {
        "SUCCESS_RESULT_RETURNED",
        "BATCH_OMISSION_INDIVIDUAL_SUCCESS",
        "SYMBOL_NOT_AVAILABLE",
        "NO_FUNDAMENTALS_AVAILABLE",
        "NOT_OPTIONABLE_OR_NO_CHAIN",
    }
    return classification not in normal


def write_results_csv(run_dir: Path, task_results: Sequence[TaskResult]) -> Path:
    rows: list[RequestResult] = []
    for result in task_results:
        rows.extend(result.request_results)
    path = run_dir / "summary" / "request-results.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = [
        "request_sequence",
        "symbol",
        "endpoint",
        "task_key",
        "request_occurrence",
        "duplicate_control",
        "classification",
        "http_status",
        "returned_symbol",
        "returned_symbols",
        "response_result_reused_for_duplicate_occurrence",
        "individual_retest_classification",
        "error_description",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in sorted(rows, key=lambda item: (item.endpoint, item.request_sequence, item.task_key)):
            payload = asdict(row)
            payload["returned_symbols"] = ";".join(row.returned_symbols)
            writer.writerow(payload)
    return path


def make_summary(
    input_rows: Sequence[InputRow], task_results: Sequence[TaskResult], elapsed_seconds: float
) -> dict[str, Any]:
    request_results = [item for result in task_results for item in result.request_results]
    endpoint_task_counts = Counter(result.endpoint for result in task_results)
    task_classifications = Counter(result.result_classification for result in task_results)
    request_classifications = Counter(item.classification for item in request_results)
    http_statuses = Counter(str(result.http_status) if result.http_status is not None else "NONE" for result in task_results)
    retries = sum(max(0, len(result.attempts) - 1) for result in task_results)
    raw_bytes = sum(result.raw_response_bytes for result in task_results)
    return {
        "input_rows": len(input_rows),
        "input_unique_symbols": len({row.symbol for row in input_rows}),
        "intentional_duplicate_rows": sum(1 for row in input_rows if row.duplicate_control),
        "task_count": len(task_results),
        "endpoint_task_counts": dict(sorted(endpoint_task_counts.items())),
        "task_classifications": dict(sorted(task_classifications.items())),
        "request_result_count": len(request_results),
        "request_classifications": dict(sorted(request_classifications.items())),
        "http_statuses": dict(sorted(http_statuses.items())),
        "retry_count": retries,
        "raw_response_bytes": raw_bytes,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "tasks_per_second": round(len(task_results) / elapsed_seconds, 3) if elapsed_seconds > 0 else None,
        "review_request_results": sum(1 for item in request_results if status_is_review(item.classification)),
    }


def write_manifest(
    run_dir: Path,
    *,
    input_file: Path,
    input_rows: Sequence[InputRow],
    endpoints: Sequence[str],
    settings_by_endpoint: Mapping[str, EndpointSettings],
    retry_policy: RetryPolicy,
    timeout_seconds: float,
    session_summary: Mapping[str, Any],
    task_results: Sequence[TaskResult],
    started_at: datetime,
    completed_at: datetime,
    elapsed_seconds: float,
    resumed: bool,
    output_root_source: str,
) -> dict[str, Any]:
    summary = make_summary(input_rows, task_results, elapsed_seconds)
    manifest = {
        "utility_version": UTILITY_VERSION,
        "capture_schema_version": CAPTURE_SCHEMA_VERSION,
        "mode": "fast",
        "run_started_at_utc": format_utc(started_at),
        "run_completed_at_utc": format_utc(completed_at),
        "resumed": resumed,
        "input_file": input_file.name,
        "endpoints": list(endpoints),
        "settings_by_endpoint": {
            key: asdict(value) for key, value in sorted(settings_by_endpoint.items())
        },
        "retry_policy": asdict(retry_policy),
        "timeout_seconds": timeout_seconds,
        "authentication": dict(session_summary),
        "input": {
            "row_count": len(input_rows),
            "unique_symbol_count": len({row.symbol for row in input_rows}),
            "duplicate_control_count": sum(1 for row in input_rows if row.duplicate_control),
            "rows": [serialize_input_row(row) for row in input_rows],
        },
        "summary": summary,
        "storage": {
            "policy": "external_raw_capture",
            "output_root_source": output_root_source,
            "run_folder_name": run_dir.name,
            "absolute_output_path_persisted": False,
            "repository_output_allowed": False,
        },
        "tasks": [asdict(result) for result in sorted(task_results, key=lambda item: item.task_sequence)],
        "privacy": {
            "crumb_persisted": False,
            "cookie_persisted": False,
            "authorization_persisted": False,
            "request_urls_redacted": True,
        },
    }
    (run_dir / "run-manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (run_dir / "run-summary.txt").write_text(render_summary_text(manifest), encoding="utf-8")
    return manifest


def render_summary_text(manifest: Mapping[str, Any]) -> str:
    summary = manifest["summary"]
    lines = [
        "Yahoo Finance Fast-mode capture summary",
        "=" * 42,
        f"Utility version       : {manifest['utility_version']}",
        f"Started UTC           : {manifest['run_started_at_utc']}",
        f"Completed UTC         : {manifest['run_completed_at_utc']}",
        f"Input rows            : {summary['input_rows']}",
        f"Unique symbols        : {summary['input_unique_symbols']}",
        f"Duplicate controls    : {summary['intentional_duplicate_rows']}",
        f"Tasks                 : {summary['task_count']}",
        f"Per-symbol results    : {summary['request_result_count']}",
        f"Retries               : {summary['retry_count']}",
        f"Raw bytes             : {summary['raw_response_bytes']}",
        f"Elapsed seconds       : {summary['elapsed_seconds']}",
        f"Tasks/second          : {summary['tasks_per_second']}",
        f"Review results        : {summary['review_request_results']}",
        "",
        "Endpoint task counts:",
    ]
    lines.extend(f"  {key:<16} {value}" for key, value in summary["endpoint_task_counts"].items())
    lines.append("")
    lines.append("Task classifications:")
    lines.extend(f"  {key:<42} {value}" for key, value in summary["task_classifications"].items())
    lines.append("")
    lines.append("Per-symbol endpoint classifications:")
    lines.extend(f"  {key:<42} {value}" for key, value in summary["request_classifications"].items())
    return "\n".join(lines) + "\n"


def dry_run_plan(rows: Sequence[InputRow], endpoints: Sequence[str], settings: EndpointSettings) -> dict[str, Any]:
    tasks = build_tasks(rows, endpoints, settings)
    by_endpoint = Counter(task.endpoint for task in tasks)
    return {
        "utility_version": UTILITY_VERSION,
        "mode": "fast-dry-run",
        "input_rows": len(rows),
        "unique_symbols": len({row.symbol for row in rows}),
        "duplicate_controls": sum(1 for row in rows if row.duplicate_control),
        "endpoints": list(endpoints),
        "task_count_before_quote_retests": len(tasks),
        "tasks_by_endpoint": dict(sorted(by_endpoint.items())),
        "settings_by_endpoint": {
            key: asdict(value) for key, value in sorted(settings_by_endpoint.items())
        },
        "history_included": False,
        "network_requests_sent": 0,
    }


def run_capture(
    rows: Sequence[InputRow],
    *,
    input_file: Path,
    outdir: Path,
    endpoints: Sequence[str] = DEFAULT_ENDPOINTS,
    settings_by_endpoint: Mapping[str, EndpointSettings] | None = None,
    timeout_seconds: float = 20.0,
    retry_policy: RetryPolicy = RetryPolicy(),
    user_agent: str = DEFAULT_USER_AGENT,
    session: Any | None = None,
    opener: Callable[..., Any] = urlopen,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = utc_now,
    progress: Callable[[str], None] = print,
    resume_run: Path | None = None,
    output_root_source: str = "function_argument",
) -> tuple[Path, dict[str, Any]]:
    normalized_endpoints = tuple(endpoints)
    unknown = [endpoint for endpoint in normalized_endpoints if endpoint not in VALID_ENDPOINTS]
    if unknown:
        raise FastCaptureInputError(f"Unsupported endpoint(s): {', '.join(unknown)}")
    if not normalized_endpoints:
        raise FastCaptureInputError("At least one endpoint must be selected.")
    if timeout_seconds <= 0:
        raise FastCaptureInputError("timeout must be greater than zero.")
    if retry_policy.maximum_attempts < 1:
        raise FastCaptureInputError("maximum attempts must be at least 1.")

    default_settings = {
        "quote": EndpointSettings(concurrency=4, quote_batch_size=100),
        "quoteSummary": EndpointSettings(concurrency=10),
        "chart": EndpointSettings(concurrency=10),
        "options": EndpointSettings(concurrency=5),
    }
    if settings_by_endpoint:
        default_settings.update(settings_by_endpoint)

    started_at = now()
    started_clock = clock()
    resumed = resume_run is not None
    run_dir = resume_run.resolve() if resume_run else create_run_dir(outdir, now)
    run_dir.mkdir(parents=True, exist_ok=True)
    run_state = RunState(run_dir=run_dir, checkpoint_path=run_dir / "checkpoint.jsonl")
    completed = load_checkpoint(run_state.checkpoint_path)
    request_session = session or YahooAnonymousSession(user_agent=user_agent, timeout_seconds=timeout_seconds)
    row_lookup = {row.request_sequence: row for row in rows}

    all_results: list[TaskResult] = []
    global_sequence = 1
    for endpoint in normalized_endpoints:
        endpoint_settings = default_settings[endpoint]
        endpoint_tasks = build_tasks(rows, (endpoint,), endpoint_settings)
        # Give every stage a globally unique task sequence.
        endpoint_tasks = [
            CaptureTask(task.task_key, task.endpoint, global_sequence + index, task.rows, task.retest)
            for index, task in enumerate(endpoint_tasks)
        ]
        global_sequence += len(endpoint_tasks)
        stage_completed = {key: value for key, value in completed.items() if key in {task.task_key for task in endpoint_tasks}}
        progress(f"Starting {endpoint}: {len(endpoint_tasks)} task(s), concurrency {endpoint_settings.concurrency}")
        stage_results = run_task_stage(
            endpoint_tasks,
            concurrency=endpoint_settings.concurrency,
            completed=stage_completed,
            run_state=run_state,
            session=request_session,
            settings=endpoint_settings,
            timeout_seconds=timeout_seconds,
            retry_policy=retry_policy,
            user_agent=user_agent,
            opener=opener,
            sleep=sleep,
            clock=clock,
            now=now,
            progress=progress,
        )
        all_results.extend(stage_results)

        if endpoint == "quote":
            missing_rows = quote_missing_rows(stage_results, row_lookup)
            if missing_rows:
                retest_tasks = build_quote_retest_tasks(missing_rows, global_sequence)
                global_sequence += len(retest_tasks)
                retest_completed = {key: value for key, value in completed.items() if key in {task.task_key for task in retest_tasks}}
                progress(f"Starting quote individual retests: {len(retest_tasks)} task(s)")
                retest_results = run_task_stage(
                    retest_tasks,
                    concurrency=endpoint_settings.concurrency,
                    completed=retest_completed,
                    run_state=run_state,
                    session=request_session,
                    settings=endpoint_settings,
                    timeout_seconds=timeout_seconds,
                    retry_policy=retry_policy,
                    user_agent=user_agent,
                    opener=opener,
                    sleep=sleep,
                    clock=clock,
                    now=now,
                    progress=progress,
                )
                apply_quote_retests(stage_results, retest_results)
                persist_retest_updates(run_state, stage_results)
                all_results.extend(retest_results)

    write_results_csv(run_dir, all_results)
    completed_at = now()
    elapsed_seconds = max(0.0, clock() - started_clock)
    manifest = write_manifest(
        run_dir,
        input_file=input_file,
        input_rows=rows,
        endpoints=normalized_endpoints,
        settings_by_endpoint={endpoint: default_settings[endpoint] for endpoint in normalized_endpoints},
        retry_policy=retry_policy,
        timeout_seconds=timeout_seconds,
        session_summary=request_session.public_summary(),
        task_results=all_results,
        started_at=started_at,
        completed_at=completed_at,
        elapsed_seconds=elapsed_seconds,
        resumed=resumed,
        output_root_source=output_root_source,
    )
    return run_dir, manifest


def parse_endpoints(text: str) -> tuple[str, ...]:
    values = tuple(part.strip() for part in text.split(",") if part.strip())
    unknown = [value for value in values if value not in VALID_ENDPOINTS]
    if unknown:
        raise FastCaptureInputError(f"Unsupported endpoint(s): {', '.join(unknown)}")
    return values


def parse_backoff(text: str) -> tuple[float, ...]:
    if not text.strip():
        return ()
    values = tuple(float(part.strip()) for part in text.split(",") if part.strip())
    if any(value < 0 for value in values):
        raise FastCaptureInputError("backoff values cannot be negative.")
    return values


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="High-volume Yahoo Finance Fast-mode capture candidate.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_FILE, help="CSV containing a symbol column.")
    parser.add_argument(
        "--output-root",
        "--outdir",
        dest="output_root",
        type=Path,
        help=(
            "External destination for raw capture runs. --outdir remains an alias for compatibility. "
            "Resolution order: command line, YAHOO_FAST_CAPTURE_ROOT, ignored local config, safe default."
        ),
    )
    parser.add_argument(
        "--local-config",
        type=Path,
        default=LOCAL_CONFIG_FILE,
        help="Ignored machine-local JSON file that stores output_root.",
    )
    parser.add_argument(
        "--configure-output-root",
        type=Path,
        metavar="PATH",
        help="Write PATH to the ignored local config and exit without network access.",
    )
    parser.add_argument(
        "--show-output-root",
        action="store_true",
        help="Display the resolved external output root and exit without network access.",
    )
    parser.add_argument(
        "--endpoints",
        default=",".join(DEFAULT_ENDPOINTS),
        help="Comma-separated: quote,quoteSummary,chart,options",
    )
    parser.add_argument("--smoke", action="store_true", help="Use a deterministic 30-row smoke set including duplicate controls.")
    parser.add_argument("--limit", type=int, help="Use only the first N selected input rows.")
    parser.add_argument("--dry-run", action="store_true", help="Print the request plan without network access.")
    parser.add_argument("--resume-run", type=Path, help="Resume an existing run folder using checkpoint.jsonl.")
    parser.add_argument("--quote-batch-size", type=int, default=100)
    parser.add_argument("--quote-concurrency", type=int, default=4)
    parser.add_argument("--fundamental-concurrency", type=int, default=10)
    parser.add_argument("--chart-concurrency", type=int, default=10)
    parser.add_argument("--options-concurrency", type=int, default=5)
    parser.add_argument("--chart-range", default="5d")
    parser.add_argument("--chart-interval", default="1d")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--backoff-seconds", default="1,3")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.configure_output_root is not None:
            config_path = write_local_output_config(args.local_config, args.configure_output_root)
            configured_root = load_local_output_root(config_path)
            assert configured_root is not None
            print(f"Local Fast-mode config: {config_path}")
            print(f"Capture output root: {configured_root}")
            print("Storage policy: external raw captures; synchronized repository output is blocked.")
            return 0

        resume_run: Path | None = None
        if args.resume_run is not None:
            resume_run = validate_resume_run(args.resume_run)
            output_root = resume_run.parent
            output_root_source = "resume_run"
        else:
            output_root, output_root_source = resolve_output_root(
                args.output_root,
                config_path=args.local_config,
            )

        if args.show_output_root:
            print(f"Capture output root: {output_root}")
            print(f"Output root source: {output_root_source}")
            print("Storage policy: external raw captures; synchronized repository output is blocked.")
            return 0

        rows = load_input_rows(args.input)
        if args.smoke:
            rows = select_smoke_rows(rows)
        if args.limit is not None:
            if args.limit < 1:
                raise FastCaptureInputError("--limit must be at least 1.")
            rows = rows[: args.limit]
        endpoints = parse_endpoints(args.endpoints)
        retry_policy = RetryPolicy(args.max_attempts, parse_backoff(args.backoff_seconds))
        settings_by_endpoint = {
            "quote": EndpointSettings(
                concurrency=args.quote_concurrency,
                quote_batch_size=args.quote_batch_size,
            ),
            "quoteSummary": EndpointSettings(concurrency=args.fundamental_concurrency),
            "chart": EndpointSettings(
                concurrency=args.chart_concurrency,
                chart_range=args.chart_range,
                chart_interval=args.chart_interval,
            ),
            "options": EndpointSettings(concurrency=args.options_concurrency),
        }
        if args.dry_run:
            plan_tasks = []
            for endpoint in endpoints:
                plan_tasks.extend(build_tasks(rows, (endpoint,), settings_by_endpoint[endpoint]))
            plan = {
                "utility_version": UTILITY_VERSION,
                "mode": "fast-dry-run",
                "input_file": str(args.input),
                "input_rows": len(rows),
                "unique_symbols": len({row.symbol for row in rows}),
                "duplicate_controls": sum(1 for row in rows if row.duplicate_control),
                "endpoints": list(endpoints),
                "task_count_before_quote_retests": len(plan_tasks),
                "tasks_by_endpoint": dict(sorted(Counter(task.endpoint for task in plan_tasks).items())),
                "settings_by_endpoint": {key: asdict(value) for key, value in settings_by_endpoint.items()},
                "history_included": False,
                "network_requests_sent": 0,
                "storage": {
                    "policy": "external_raw_capture",
                    "output_root": str(output_root),
                    "output_root_source": output_root_source,
                    "repository_output_allowed": False,
                },
            }
            print(json.dumps(plan, indent=2))
            return 0

        prepare_output_root(output_root)
        print(f"Capture output root: {output_root} ({output_root_source})")
        print("Storage policy: external raw captures; synchronized repository output is blocked.")
        run_dir, manifest = run_capture(
            rows,
            input_file=args.input,
            outdir=output_root,
            endpoints=endpoints,
            settings_by_endpoint=settings_by_endpoint,
            timeout_seconds=args.timeout,
            retry_policy=retry_policy,
            user_agent=args.user_agent,
            resume_run=resume_run,
            output_root_source=output_root_source,
        )
        print(f"Run folder: {run_dir}")
        print(f"Completed tasks: {manifest['summary']['task_count']}")
        print(f"Review results: {manifest['summary']['review_request_results']}")
        return 2 if manifest["summary"]["review_request_results"] else 0
    except (FastCaptureInputError, YahooSessionError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted. Re-run with --resume-run and the existing external run folder.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
