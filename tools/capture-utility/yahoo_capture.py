#!/usr/bin/env python3
"""Yahoo Finance evidence capture utility.

v0.4.2 implements the v0.3.9 capture-format specification for the Quote
endpoint, anonymous Yahoo cookie-and-crumb session handling, portable output
paths, and validation of completed capture runs. It uses only the Python
standard library.

The utility:
- reads up to 30 enabled symbols from a CSV table (or --symbols),
- establishes an anonymous Yahoo cookie-and-crumb session,
- sends one Quote request per symbol, sequentially,
- refreshes the anonymous session once after HTTP 401 or 403,
- preserves each final HTTP response body byte-for-byte,
- writes a metadata sidecar and SHA-256 digest,
- writes deterministic normalized text for valid JSON responses,
- writes a run manifest covering every attempted request, and
- validates run structure, hashes, privacy, and mapped/unmapped JSON paths.

Yahoo Finance endpoints are unofficial and may change without notice.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import socket
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

UTILITY_VERSION = "0.4.2"
CAPTURE_SCHEMA_VERSION = "0.3.9"
VALIDATION_SCHEMA_VERSION = "0.4.2"
DEFAULT_ENDPOINT_ID = "quote"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/150.0.0.0 Safari/537.36"
)
DEFAULT_BASE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"
YAHOO_BASIC_COOKIE_URL = "https://fc.yahoo.com"
YAHOO_BASIC_CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"
YAHOO_FALLBACK_COOKIE_URL = "https://finance.yahoo.com/quote/AAPL"
YAHOO_FALLBACK_CRUMB_URL = "https://query2.finance.yahoo.com/v1/test/getcrumb"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SYMBOLS_FILE = Path(__file__).with_name("symbols.csv")
DEFAULT_MASTER_FIELDS = REPOSITORY_ROOT / "data" / "master_field_database.csv"
DEFAULT_OUTDIR = REPOSITORY_ROOT / "captures" / "local"
VALIDATION_JSON_FILENAME = "run-validation.json"
VALIDATION_TEXT_FILENAME = "run-validation.txt"
MAX_SYMBOLS = 30
RETRYABLE_HTTP_STATUSES = {429, 500, 502, 503, 504}
AUTH_REFRESH_HTTP_STATUSES = {401, 403}
TRUE_VALUES = {"1", "true", "yes", "y", "on", "enabled"}
FALSE_VALUES = {"0", "false", "no", "n", "off", "disabled"}
SENSITIVE_QUERY_KEYS = {"crumb", "token", "authorization", "auth", "cookie", "session"}
ARRAY_INDEX_RE = re.compile(r"\[\d+\]")
SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
TIMESTAMP_FIELD_RE = re.compile(r"(?:time|timestamp|datemilliseconds)$", re.IGNORECASE)
WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")
UNREDACTED_CRUMB_RE = re.compile(
    r"(?i)(?:[?&]|\b)crumb=(?!REDACTED(?:[&\s\"']|$))[^&\s\"']+"
)
SENSITIVE_HEADER_RE = re.compile(r"(?im)^(?:set-cookie|cookie|authorization)\s*:\s*\S+")
SENSITIVE_JSON_VALUE_RE = re.compile(
    r'(?i)"(?:crumb|cookie|authorization)"\s*:\s*"(?!REDACTED(?:"|$)|NONE(?:"|$))[^"\r\n]+"'
)
LOCAL_ABSOLUTE_PATH_RE = re.compile(r'(?i)(?:[A-Za-z]:\\Users\\|/Users/|/home/)[^\r\n"]+')


class CaptureInputError(ValueError):
    """Raised when command input is invalid before any request is sent."""


class YahooSessionError(RuntimeError):
    """Raised when an anonymous Yahoo cookie-and-crumb session cannot be prepared."""


@dataclass(frozen=True)
class SymbolRequest:
    symbol: str
    enabled: bool = True
    project_security_type: str = ""
    endpoint_id: str = DEFAULT_ENDPOINT_ID
    notes: str = ""


@dataclass(frozen=True)
class RetryPolicy:
    maximum_attempts: int
    backoff_seconds: tuple[float, ...]


@dataclass
class AttemptRecord:
    attempt: int
    requested_at_utc: str
    response_received_at_utc: str
    elapsed_ms: int
    http_status: int | None
    error: str | None


@dataclass
class HttpCapture:
    body: bytes | None
    http_status: int | None
    content_type: str
    final_url: str
    requested_at_utc: str
    response_received_at_utc: str
    elapsed_ms: int
    attempts: list[AttemptRecord]
    error_message: str | None


@dataclass(frozen=True)
class MasterField:
    display_order: int
    json_path: str
    field_name: str
    endpoint: str
    yahoo_type: str


@dataclass(frozen=True)
class FlatField:
    json_path: str
    field_name: str
    raw_type: str
    raw_value: Any


@dataclass(frozen=True)
class ResponseAnalysis:
    classification: str
    returned_symbol: str | None
    returned_symbols: tuple[str, ...]
    parsed_json: Any | None
    parse_error: str | None


class NoAuthSession:
    """Diagnostic session that sends requests without Yahoo cookie/crumb setup."""

    mode = "none"
    can_refresh = False

    def __init__(self, *, opener: Callable[..., Any] = urlopen):
        self._opener = opener
        self.strategy: str | None = None
        self.refresh_count = 0
        self.last_error: str | None = None

    def open(self, request: Request, timeout: float):
        return self._opener(request, timeout=timeout)

    def quote_url(self, symbol: str, base_url: str, *, force_refresh: bool = False) -> str:
        return build_quote_url(symbol, base_url)

    def public_summary(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "strategy": None,
            "cookie_count": 0,
            "crumb_present": False,
            "refresh_count": 0,
            "last_error": self.last_error,
            "sensitive_values_persisted": False,
        }


class YahooAnonymousSession:
    """In-memory anonymous Yahoo cookie-and-crumb session.

    Cookie and crumb values are never returned by ``public_summary`` and are
    never written to capture metadata. The same in-memory session is reused for
    the complete run.
    """

    mode = "anonymous-cookie-crumb"
    can_refresh = True

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
        if opener is None:
            self._opener = build_opener(HTTPCookieProcessor(self.cookie_jar)).open
        else:
            self._opener = opener
        self._crumb: str | None = None
        self.strategy: str | None = None
        self.refresh_count = 0
        self.last_error: str | None = None

    def open(self, request: Request, timeout: float):
        return self._opener(request, timeout=timeout)

    def _prime_cookie(self, url: str) -> str | None:
        request = make_request(url, self.user_agent, accept="text/html,application/xhtml+xml,*/*")
        try:
            with self.open(request, timeout=self.timeout_seconds) as response:
                response.read()
            return None
        except HTTPError as exc:
            # fc.yahoo.com commonly returns a non-success page while still
            # establishing a cookie. Continue unless Yahoo is throttling.
            if exc.code == 429:
                raise YahooSessionError("Yahoo rate-limited anonymous cookie setup (HTTP 429).") from exc
            _read_http_error_body(exc)
            return f"cookie bootstrap returned HTTP {exc.code}"
        except (URLError, TimeoutError, socket.timeout, OSError) as exc:
            return f"cookie bootstrap failed: {type(exc).__name__}: {exc}"

    @staticmethod
    def _validate_crumb(text: str) -> str | None:
        crumb = text.strip()
        lowered = crumb.lower()
        if not crumb or len(crumb) > 512:
            return None
        if "too many requests" in lowered or lowered.startswith("<!doctype") or lowered.startswith("<html"):
            return None
        if "\r" in crumb or "\n" in crumb:
            return None
        return crumb

    def _fetch_crumb(self, url: str) -> str:
        request = make_request(url, self.user_agent, accept="text/plain,*/*")
        try:
            with self.open(request, timeout=self.timeout_seconds) as response:
                status = int(getattr(response, "status", response.getcode()))
                body = response.read()
        except HTTPError as exc:
            _read_http_error_body(exc)
            if exc.code == 429:
                raise YahooSessionError("Yahoo rate-limited crumb setup (HTTP 429).") from exc
            raise YahooSessionError(f"crumb endpoint returned HTTP {exc.code}") from exc
        except (URLError, TimeoutError, socket.timeout, OSError) as exc:
            raise YahooSessionError(f"crumb endpoint failed: {type(exc).__name__}: {exc}") from exc
        if not 200 <= status < 300:
            raise YahooSessionError(f"crumb endpoint returned HTTP {status}")
        crumb = self._validate_crumb(body.decode("utf-8", errors="replace"))
        if crumb is None:
            raise YahooSessionError("crumb endpoint returned an invalid or empty value")
        return crumb

    def ensure_crumb(self, *, force_refresh: bool = False) -> str:
        if self._crumb is not None and not force_refresh:
            return self._crumb
        if force_refresh:
            self.refresh_count += 1
            self._crumb = None
            self.strategy = None
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
            prime_note = self._prime_cookie(cookie_url)
            try:
                crumb = self._fetch_crumb(crumb_url)
            except YahooSessionError as exc:
                note = str(exc)
                if prime_note:
                    note = f"{prime_note}; {note}"
                errors.append(f"{strategy}: {note}")
                continue
            self._crumb = crumb
            self.strategy = strategy
            self.last_error = None
            return crumb

        self.last_error = "Could not establish an anonymous Yahoo cookie-and-crumb session: " + "; ".join(errors)
        raise YahooSessionError(self.last_error)

    def quote_url(self, symbol: str, base_url: str, *, force_refresh: bool = False) -> str:
        crumb = self.ensure_crumb(force_refresh=force_refresh)
        return build_quote_url(symbol, base_url, crumb=crumb)

    def public_summary(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "strategy": self.strategy,
            "cookie_count": sum(1 for _ in self.cookie_jar),
            "crumb_present": self._crumb is not None,
            "refresh_count": self.refresh_count,
            "last_error": self.last_error,
            "sensitive_values_persisted": False,
        }


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def filename_utc(value: datetime) -> str:
    return format_utc(value).replace(":", "-")



def _resolve_path(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except OSError:
        return path.expanduser().absolute()


def portable_path_reference(value: str | Path, repository_root: Path = REPOSITORY_ROOT) -> tuple[str, str]:
    """Return a privacy-safe path reference and its scope.

    Repository paths are stored relative to the repository root. External paths
    are reduced to their filename so local usernames and directory layouts are
    not written into public evidence.
    """
    if isinstance(value, str) and value.startswith("--"):
        return value, "command-line"
    path = _resolve_path(Path(value))
    root = _resolve_path(repository_root)
    try:
        return path.relative_to(root).as_posix(), "repository"
    except ValueError:
        return path.name, "external"


def is_absolute_path_text(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    return bool(WINDOWS_ABSOLUTE_PATH_RE.match(value) or value.startswith("/"))


def safe_run_path(run_dir: Path, relative_value: Any) -> Path | None:
    """Resolve a manifest path only when it remains inside the run directory."""
    if not isinstance(relative_value, str) or not relative_value.strip():
        return None
    candidate = _resolve_path(run_dir / relative_value)
    root = _resolve_path(run_dir)
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def parse_enabled(value: str | None, *, row_number: int) -> bool:
    if value is None or not value.strip():
        return True
    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise CaptureInputError(
        f"Row {row_number}: enabled must be one of {sorted(TRUE_VALUES | FALSE_VALUES)}; got {value!r}."
    )


def validate_symbol(symbol: str, *, row_number: int | None = None) -> str:
    clean = symbol.strip()
    prefix = f"Row {row_number}: " if row_number is not None else ""
    if not clean:
        raise CaptureInputError(prefix + "symbol is blank.")
    if clean.lower().startswith(("http://", "https://")) or "://" in clean or clean.startswith("//"):
        raise CaptureInputError(prefix + f"full URLs are not valid symbols: {clean!r}.")
    if any(ch in clean for ch in ("\r", "\n", "\x00")):
        raise CaptureInputError(prefix + "symbol contains a control character.")
    return clean


def load_symbol_table(path: Path) -> list[SymbolRequest]:
    if not path.exists():
        raise CaptureInputError(f"Symbol table not found: {path}")

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or "symbol" not in {name.strip() for name in reader.fieldnames if name}:
            raise CaptureInputError("Symbol table must contain a 'symbol' column.")

        requests: list[SymbolRequest] = []
        for row_number, row in enumerate(reader, start=2):
            normalized = {(key or "").strip(): (value or "") for key, value in row.items()}
            symbol_text = normalized.get("symbol", "")
            if not symbol_text.strip() and not any(value.strip() for value in normalized.values()):
                continue
            symbol = validate_symbol(symbol_text, row_number=row_number)
            enabled = parse_enabled(normalized.get("enabled"), row_number=row_number)
            endpoint_id = normalized.get("endpoint_id", "").strip() or DEFAULT_ENDPOINT_ID
            if endpoint_id != DEFAULT_ENDPOINT_ID:
                raise CaptureInputError(
                    f"Row {row_number}: v{UTILITY_VERSION} supports endpoint_id='quote' only; got {endpoint_id!r}."
                )
            requests.append(
                SymbolRequest(
                    symbol=symbol,
                    enabled=enabled,
                    project_security_type=normalized.get("project_security_type", "").strip(),
                    endpoint_id=endpoint_id,
                    notes=normalized.get("notes", "").strip(),
                )
            )

    enabled_requests = [item for item in requests if item.enabled]
    if not enabled_requests:
        raise CaptureInputError("The symbol table contains no enabled symbol rows.")
    if len(enabled_requests) > MAX_SYMBOLS:
        raise CaptureInputError(
            f"The symbol table has {len(enabled_requests)} enabled rows; the project limit is {MAX_SYMBOLS}."
        )
    return enabled_requests


def parse_symbols_argument(value: str) -> list[SymbolRequest]:
    symbols = [validate_symbol(part) for part in value.split(",") if part.strip()]
    if not symbols:
        raise CaptureInputError("--symbols did not contain any symbols.")
    if len(symbols) > MAX_SYMBOLS:
        raise CaptureInputError(f"--symbols contains {len(symbols)} symbols; the project limit is {MAX_SYMBOLS}.")
    return [SymbolRequest(symbol=symbol) for symbol in symbols]


def parse_backoff(value: str, maximum_attempts: int) -> tuple[float, ...]:
    if maximum_attempts < 1:
        raise CaptureInputError("--max-attempts must be at least 1.")
    if not value.strip():
        values: list[float] = []
    else:
        try:
            values = [float(part.strip()) for part in value.split(",") if part.strip()]
        except ValueError as exc:
            raise CaptureInputError("--backoff-seconds must be a comma-separated list of numbers.") from exc
    if any(number < 0 for number in values):
        raise CaptureInputError("--backoff-seconds cannot contain negative values.")
    needed = maximum_attempts - 1
    if needed == 0:
        return ()
    if not values:
        values = [2.0]
    while len(values) < needed:
        values.append(values[-1])
    return tuple(values[:needed])


def filename_symbol(symbol: str) -> str:
    value = symbol.lstrip("^").replace("=", "_").replace("/", "_").replace("\\", "_")
    value = SAFE_FILENAME_RE.sub("_", value).strip("._-")
    return value or "symbol"


def build_quote_url(symbol: str, base_url: str = DEFAULT_BASE_URL, crumb: str | None = None) -> str:
    params = {"symbols": symbol}
    if crumb is not None:
        params["crumb"] = crumb
    return base_url + "?" + urlencode(params)


def redact_url(url: str) -> str:
    parts = urlsplit(url)
    query_items = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        query_items.append((key, "REDACTED" if key.lower() in SENSITIVE_QUERY_KEYS else value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query_items), ""))


def make_request(url: str, user_agent: str, *, accept: str = "application/json,text/plain,*/*") -> Request:
    return Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": accept,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "identity",
        },
    )


def _header_value(headers: Any, name: str) -> str:
    if headers is None:
        return ""
    if hasattr(headers, "get_content_type") and name.lower() == "content-type":
        try:
            return str(headers.get_content_type())
        except Exception:
            pass
    try:
        return str(headers.get(name, ""))
    except Exception:
        return ""


def _read_http_error_body(exc: HTTPError) -> bytes:
    try:
        return exc.read()
    except Exception:
        return b""


def request_with_retry(
    url: str,
    *,
    timeout_seconds: float,
    retry_policy: RetryPolicy,
    user_agent: str,
    opener: Callable[..., Any] = urlopen,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.perf_counter,
    now: Callable[[], datetime] = utc_now,
) -> HttpCapture:
    attempts: list[AttemptRecord] = []
    last_body: bytes | None = None
    last_status: int | None = None
    last_content_type = ""
    last_final_url = url
    first_requested_at: str | None = None
    last_received_at = ""
    last_error: str | None = None
    overall_started = clock()

    for attempt_number in range(1, retry_policy.maximum_attempts + 1):
        requested_dt = now()
        requested_at = format_utc(requested_dt)
        if first_requested_at is None:
            first_requested_at = requested_at
        started = clock()
        body: bytes | None = None
        status: int | None = None
        content_type = ""
        final_url = url
        error_text: str | None = None
        retryable = False

        try:
            request = make_request(url, user_agent)
            with opener(request, timeout=timeout_seconds) as response:
                body = response.read()
                status = int(getattr(response, "status", response.getcode()))
                content_type = _header_value(getattr(response, "headers", None), "Content-Type")
                final_url = str(getattr(response, "url", url) or url)
                retryable = status in RETRYABLE_HTTP_STATUSES
        except HTTPError as exc:
            body = _read_http_error_body(exc)
            status = int(exc.code)
            content_type = _header_value(exc.headers, "Content-Type")
            final_url = str(exc.geturl() or url)
            error_text = f"HTTP {exc.code}: {exc.reason}"
            retryable = status in RETRYABLE_HTTP_STATUSES
        except (URLError, TimeoutError, socket.timeout, OSError) as exc:
            error_text = f"{type(exc).__name__}: {exc}"
            retryable = True

        elapsed_ms = max(0, round((clock() - started) * 1000))
        received_at = format_utc(now())
        attempts.append(
            AttemptRecord(
                attempt=attempt_number,
                requested_at_utc=requested_at,
                response_received_at_utc=received_at,
                elapsed_ms=elapsed_ms,
                http_status=status,
                error=error_text,
            )
        )

        last_body = body
        last_status = status
        last_content_type = content_type
        last_final_url = final_url
        last_received_at = received_at
        last_error = error_text

        if not retryable or attempt_number >= retry_policy.maximum_attempts:
            break
        sleep(retry_policy.backoff_seconds[attempt_number - 1])

    total_elapsed_ms = max(0, round((clock() - overall_started) * 1000))
    return HttpCapture(
        body=last_body,
        http_status=last_status,
        content_type=last_content_type,
        final_url=last_final_url,
        requested_at_utc=first_requested_at or format_utc(now()),
        response_received_at_utc=last_received_at or format_utc(now()),
        elapsed_ms=total_elapsed_ms,
        attempts=attempts,
        error_message=last_error,
    )


def combine_http_captures(first: HttpCapture, second: HttpCapture) -> HttpCapture:
    """Combine a pre-refresh response with the response after session refresh."""
    attempts = list(first.attempts)
    for record in second.attempts:
        attempts.append(
            AttemptRecord(
                attempt=len(attempts) + 1,
                requested_at_utc=record.requested_at_utc,
                response_received_at_utc=record.response_received_at_utc,
                elapsed_ms=record.elapsed_ms,
                http_status=record.http_status,
                error=record.error,
            )
        )
    return HttpCapture(
        body=second.body,
        http_status=second.http_status,
        content_type=second.content_type,
        final_url=second.final_url,
        requested_at_utc=first.requested_at_utc,
        response_received_at_utc=second.response_received_at_utc,
        elapsed_ms=first.elapsed_ms + second.elapsed_ms,
        attempts=attempts,
        error_message=second.error_message,
    )


def analyze_quote_response(body: bytes | None, http_status: int | None, requested_symbol: str) -> ResponseAnalysis:
    if http_status == 429:
        return ResponseAnalysis("RATE_LIMIT_OR_THROTTLE", None, (), None, None)
    if http_status is None:
        return ResponseAnalysis("NETWORK_ERROR", None, (), None, None)
    if not 200 <= http_status < 300:
        return ResponseAnalysis("HTTP_ERROR", None, (), None, None)
    if body is None:
        return ResponseAnalysis("PARSE_ERROR", None, (), None, "HTTP success contained no response body.")

    try:
        parsed = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return ResponseAnalysis("PARSE_ERROR", None, (), None, f"{type(exc).__name__}: {exc}")

    try:
        results = parsed["quoteResponse"]["result"]
    except (KeyError, TypeError) as exc:
        return ResponseAnalysis(
            "PARSE_ERROR",
            None,
            (),
            parsed,
            f"Quote response does not contain quoteResponse.result: {type(exc).__name__}: {exc}",
        )
    if not isinstance(results, list):
        return ResponseAnalysis(
            "PARSE_ERROR",
            None,
            (),
            parsed,
            "quoteResponse.result is not an array.",
        )
    if not results:
        return ResponseAnalysis("SUCCESS_EMPTY_RESULT", None, (), parsed, None)

    returned_symbols = tuple(
        str(item.get("symbol"))
        for item in results
        if isinstance(item, Mapping) and item.get("symbol") is not None
    )
    exact_match = next((symbol for symbol in returned_symbols if symbol == requested_symbol), None)
    if exact_match is None:
        return ResponseAnalysis(
            "REQUESTED_SYMBOL_MISSING_FROM_RESULT",
            returned_symbols[0] if returned_symbols else None,
            returned_symbols,
            parsed,
            None,
        )
    return ResponseAnalysis("SUCCESS_RESULT_RETURNED", exact_match, returned_symbols, parsed, None)


def raw_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def flatten_json(value: Any, path: str = "") -> list[FlatField]:
    fields: list[FlatField] = []
    if isinstance(value, dict):
        if not value:
            fields.append(FlatField(path, path.rsplit(".", 1)[-1] if path else "", "object", {}))
        else:
            for key, child in value.items():
                child_path = f"{path}.{key}" if path else str(key)
                fields.extend(flatten_json(child, child_path))
    elif isinstance(value, list):
        if not value:
            fields.append(FlatField(path, path.rsplit(".", 1)[-1] if path else "", "array", []))
        else:
            for index, child in enumerate(value):
                child_path = f"{path}[{index}]"
                fields.extend(flatten_json(child, child_path))
    else:
        field_name = path.rsplit(".", 1)[-1] if path else ""
        field_name = re.sub(r"\[\d+\]$", "", field_name)
        fields.append(FlatField(path, field_name, raw_type(value), value))
    return fields


def normalize_array_path(path: str) -> str:
    return ARRAY_INDEX_RE.sub("[]", path)


def load_master_fields(path: Path, endpoint_id: str = DEFAULT_ENDPOINT_ID) -> dict[str, MasterField]:
    if not path.exists():
        return {}
    fields: dict[str, MasterField] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for display_order, row in enumerate(reader, start=1):
            endpoint = (row.get("endpoint") or "").strip()
            json_path = (row.get("json_path") or "").strip()
            if endpoint != endpoint_id or not json_path:
                continue
            fields.setdefault(
                json_path,
                MasterField(
                    display_order=display_order,
                    json_path=json_path,
                    field_name=(row.get("field_name") or "").strip(),
                    endpoint=endpoint,
                    yahoo_type=(row.get("yahoo_type") or "").strip(),
                ),
            )
    return fields


def decode_timestamp(field: FlatField) -> str:
    if field.raw_type != "number" or not TIMESTAMP_FIELD_RE.search(field.field_name):
        return ""
    value = field.raw_value
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        return ""
    seconds = float(value)
    if seconds >= 100_000_000_000:
        seconds /= 1000.0
    try:
        decoded = datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return ""
    return format_utc(decoded)


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def normalized_rows(parsed_json: Any, master_fields: Mapping[str, MasterField]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for field in flatten_json(parsed_json):
        master = master_fields.get(normalize_array_path(field.json_path))
        rows.append(
            {
                "display_order": f"{master.display_order:04d}" if master else "",
                "json_path": field.json_path,
                "field_name": field.field_name,
                "raw_type": field.raw_type,
                "raw_value": compact_json(field.raw_value),
                "decoded_utc": decode_timestamp(field),
                "master_field_status": "Known" if master else "New",
                "notes": "",
            }
        )
    rows.sort(
        key=lambda row: (
            0 if row["display_order"] else 1,
            int(row["display_order"]) if row["display_order"] else 0,
            row["json_path"],
        )
    )
    return rows


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_bytes(path: Path, data: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def write_normalized_text(
    path: Path,
    *,
    symbol: str,
    endpoint_id: str,
    capture: HttpCapture,
    analysis: ResponseAnalysis,
    raw_filename: str,
    rows: Sequence[Mapping[str, str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(f"Symbol: {symbol}\n")
        handle.write(f"Endpoint: {endpoint_id}\n")
        handle.write(f"Requested UTC: {capture.requested_at_utc}\n")
        handle.write(f"Received UTC: {capture.response_received_at_utc}\n")
        handle.write(f"HTTP status: {capture.http_status if capture.http_status is not None else ''}\n")
        handle.write(f"Result classification: {analysis.classification}\n")
        handle.write(f"Raw file: {raw_filename}\n\n")
        columns = [
            "display_order",
            "json_path",
            "field_name",
            "raw_type",
            "raw_value",
            "decoded_utc",
            "master_field_status",
            "notes",
        ]
        handle.write(" | ".join(columns) + "\n")
        for row in rows:
            safe_values = [str(row[column]).replace("\n", "\\n").replace("\r", "\\r").replace(" | ", " \\| ") for column in columns]
            handle.write(" | ".join(safe_values) + "\n")


def next_run_id(outdir: Path, started: datetime) -> tuple[str, Path]:
    prefix = filename_utc(started)
    for number in range(1, 10_000):
        run_id = f"{prefix}_run-{number:04d}"
        run_dir = outdir / run_id
        if not run_dir.exists():
            return run_id, run_dir
    raise RuntimeError("Could not allocate a unique run folder.")


def summary_template() -> dict[str, int]:
    return {
        "success_result_returned": 0,
        "success_empty_result": 0,
        "requested_symbol_missing_from_result": 0,
        "http_error": 0,
        "parse_error": 0,
        "rate_limit_or_throttle": 0,
        "network_error": 0,
        "other": 0,
    }


def increment_summary(summary: dict[str, int], classification: str) -> None:
    key = classification.lower()
    if key in summary:
        summary[key] += 1
    else:
        summary["other"] += 1


def capture_one(
    item: SymbolRequest,
    *,
    sequence: int,
    run_id: str,
    run_dir: Path,
    master_fields: Mapping[str, MasterField],
    timeout_seconds: float,
    retry_policy: RetryPolicy,
    user_agent: str,
    base_url: str,
    request_session: Any,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
    now: Callable[[], datetime],
) -> dict[str, Any]:
    auth_session_error: str | None = None
    auth_refresh_performed = False
    try:
        request_url = request_session.quote_url(item.symbol, base_url)
    except YahooSessionError as exc:
        auth_session_error = str(exc)
        request_url = build_quote_url(item.symbol, base_url)

    capture = request_with_retry(
        request_url,
        timeout_seconds=timeout_seconds,
        retry_policy=retry_policy,
        user_agent=user_agent,
        opener=request_session.open,
        sleep=sleep,
        clock=clock,
        now=now,
    )

    if (
        capture.http_status in AUTH_REFRESH_HTTP_STATUSES
        and getattr(request_session, "can_refresh", False)
        and getattr(request_session, "refresh_count", 0) < 1
    ):
        try:
            refreshed_url = request_session.quote_url(item.symbol, base_url, force_refresh=True)
            refreshed_capture = request_with_retry(
                refreshed_url,
                timeout_seconds=timeout_seconds,
                retry_policy=retry_policy,
                user_agent=user_agent,
                opener=request_session.open,
                sleep=sleep,
                clock=clock,
                now=now,
            )
            capture = combine_http_captures(capture, refreshed_capture)
            request_url = refreshed_url
            auth_refresh_performed = True
        except YahooSessionError as exc:
            auth_session_error = (
                f"{auth_session_error}; refresh failed: {exc}" if auth_session_error else f"refresh failed: {exc}"
            )
    analysis = analyze_quote_response(capture.body, capture.http_status, item.symbol)
    received_filename_stamp = capture.response_received_at_utc.replace(":", "-")
    base_name = f"{sequence:04d}_{filename_symbol(item.symbol)}_{item.endpoint_id}_{received_filename_stamp}"

    raw_rel: str | None = None
    raw_sha256: str | None = None
    error_rel: str | None = None
    normalized_rel: str | None = None

    if capture.body is not None:
        raw_extension = ".raw.json" if "json" in capture.content_type.lower() else ".raw.bin"
        raw_path = run_dir / "raw" / f"{base_name}{raw_extension}"
        raw_sha256 = write_bytes(raw_path, capture.body)
        raw_rel = raw_path.relative_to(run_dir).as_posix()
    elif capture.error_message:
        error_path = run_dir / "errors" / f"{base_name}.error.txt"
        error_path.parent.mkdir(parents=True, exist_ok=True)
        error_path.write_text(capture.error_message + "\n", encoding="utf-8")
        error_rel = error_path.relative_to(run_dir).as_posix()

    if analysis.parsed_json is not None:
        rows = normalized_rows(analysis.parsed_json, master_fields)
        normalized_path = run_dir / "normalized" / f"{base_name}.normalized.txt"
        write_normalized_text(
            normalized_path,
            symbol=item.symbol,
            endpoint_id=item.endpoint_id,
            capture=capture,
            analysis=analysis,
            raw_filename=raw_rel or "",
            rows=rows,
        )
        normalized_rel = normalized_path.relative_to(run_dir).as_posix()

    error_message = analysis.parse_error or capture.error_message
    metadata: dict[str, Any] = {
        "capture_schema_version": CAPTURE_SCHEMA_VERSION,
        "run_id": run_id,
        "sequence": sequence,
        "utility_version": UTILITY_VERSION,
        "endpoint_id": item.endpoint_id,
        "requested_symbol": item.symbol,
        "returned_symbol": analysis.returned_symbol,
        "returned_symbols": list(analysis.returned_symbols),
        "project_security_type": item.project_security_type,
        "requested_at_utc": capture.requested_at_utc,
        "response_received_at_utc": capture.response_received_at_utc,
        "elapsed_ms": capture.elapsed_ms,
        "attempt_count": len(capture.attempts),
        "attempts": [asdict(attempt) for attempt in capture.attempts],
        "http_status": capture.http_status,
        "content_type": capture.content_type,
        "response_bytes": len(capture.body) if capture.body is not None else 0,
        "raw_response_file": raw_rel,
        "raw_response_sha256": raw_sha256,
        "normalized_output_file": normalized_rel,
        "error_file": error_rel,
        "result_classification": analysis.classification,
        "error_message": error_message,
        "request_url_redacted": redact_url(capture.final_url or request_url),
        "auth_mode": getattr(request_session, "mode", "unknown"),
        "auth_strategy": getattr(request_session, "strategy", None),
        "auth_refresh_performed": auth_refresh_performed,
        "auth_refresh_count_run": getattr(request_session, "refresh_count", 0),
        "auth_session_error": auth_session_error,
        "session_sensitive_values_persisted": False,
        "notes": item.notes,
    }
    metadata_path = run_dir / "metadata" / f"{base_name}.meta.json"
    metadata["metadata_file"] = metadata_path.relative_to(run_dir).as_posix()
    write_json(metadata_path, metadata)
    return metadata


def run_capture(
    symbol_requests: Sequence[SymbolRequest],
    *,
    outdir: Path,
    input_file: str,
    master_field_path: Path,
    pause_between_requests_ms: int,
    timeout_seconds: float,
    retry_policy: RetryPolicy,
    user_agent: str,
    base_url: str = DEFAULT_BASE_URL,
    auth_mode: str = "anonymous-crumb",
    opener: Callable[..., Any] | None = None,
    request_session: Any | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.perf_counter,
    now: Callable[[], datetime] = utc_now,
    repository_root: Path = REPOSITORY_ROOT,
    progress: Callable[[str], None] | None = None,
) -> tuple[Path, dict[str, Any]]:
    if not symbol_requests:
        raise CaptureInputError("No symbol requests were supplied.")
    if len(symbol_requests) > MAX_SYMBOLS:
        raise CaptureInputError(f"A run may contain at most {MAX_SYMBOLS} enabled symbols.")
    if pause_between_requests_ms < 0:
        raise CaptureInputError("--pause-ms cannot be negative.")
    if timeout_seconds <= 0:
        raise CaptureInputError("--timeout must be greater than zero.")
    if auth_mode not in {"anonymous-crumb", "none"}:
        raise CaptureInputError("--auth-mode must be 'anonymous-crumb' or 'none'.")

    if request_session is None:
        selected_opener = opener or urlopen
        if auth_mode == "none":
            request_session = NoAuthSession(opener=selected_opener)
        else:
            request_session = YahooAnonymousSession(
                user_agent=user_agent,
                timeout_seconds=timeout_seconds,
                opener=opener,
            )

    started_dt = now()
    run_id, run_dir = next_run_id(outdir, started_dt)
    for name in ("raw", "metadata", "normalized", "errors"):
        (run_dir / name).mkdir(parents=True, exist_ok=True)

    master_fields = load_master_fields(master_field_path)
    input_reference, input_scope = portable_path_reference(input_file, repository_root)
    master_reference, master_scope = portable_path_reference(master_field_path, repository_root)
    manifest: dict[str, Any] = {
        "capture_schema_version": CAPTURE_SCHEMA_VERSION,
        "run_id": run_id,
        "utility_version": UTILITY_VERSION,
        "input_file": input_reference,
        "input_file_scope": input_scope,
        "master_field_database": master_reference,
        "master_field_database_scope": master_scope,
        "default_endpoint_id": DEFAULT_ENDPOINT_ID,
        "pause_between_requests_ms": pause_between_requests_ms,
        "timeout_seconds": timeout_seconds,
        "retry_policy": {
            "maximum_attempts": retry_policy.maximum_attempts,
            "backoff_seconds": list(retry_policy.backoff_seconds),
        },
        "authentication": request_session.public_summary(),
        "run_started_at_utc": format_utc(started_dt),
        "run_completed_at_utc": None,
        "symbols_requested": len(symbol_requests),
        "request_order": [item.symbol for item in symbol_requests],
        "summary": summary_template(),
        "requests": [],
    }
    manifest_path = run_dir / "run-manifest.json"
    write_json(manifest_path, manifest)

    for sequence, item in enumerate(symbol_requests, start=1):
        metadata = capture_one(
            item,
            sequence=sequence,
            run_id=run_id,
            run_dir=run_dir,
            master_fields=master_fields,
            timeout_seconds=timeout_seconds,
            retry_policy=retry_policy,
            user_agent=user_agent,
            base_url=base_url,
            request_session=request_session,
            sleep=sleep,
            clock=clock,
            now=now,
        )
        manifest["requests"].append(metadata)
        increment_summary(manifest["summary"], metadata["result_classification"])
        manifest["authentication"] = request_session.public_summary()
        write_json(manifest_path, manifest)
        if progress is not None:
            http_display = metadata["http_status"] if metadata["http_status"] is not None else "-"
            progress(
                f"[{sequence:02d}/{len(symbol_requests):02d}] {item.symbol} ... "
                f"HTTP {http_display} {metadata['result_classification']}"
            )
        if sequence < len(symbol_requests) and pause_between_requests_ms:
            sleep(pause_between_requests_ms / 1000.0)

    manifest["run_completed_at_utc"] = format_utc(now())
    write_json(manifest_path, manifest)
    return run_dir, manifest



def _new_validation_report(run_dir: Path) -> dict[str, Any]:
    return {
        "validation_schema_version": VALIDATION_SCHEMA_VERSION,
        "validator_utility_version": UTILITY_VERSION,
        "run_folder": run_dir.name,
        "run_id": None,
        "validated_at_utc": format_utc(utc_now()),
        "status": "FAIL",
        "counts": {
            "errors": 0,
            "warnings": 0,
            "information": 0,
            "manifest_requests": 0,
            "raw_files_checked": 0,
            "metadata_files_checked": 0,
            "normalized_files_checked": 0,
            "error_files_checked": 0,
            "known_json_paths_observed": 0,
            "unmapped_json_paths_observed": 0,
        },
        "issues": [],
        "known_json_paths": [],
        "unmapped_json_paths": [],
    }


def _add_validation_issue(
    report: dict[str, Any],
    severity: str,
    code: str,
    message: str,
    *,
    file: str | None = None,
    sequence: int | None = None,
) -> None:
    normalized = severity.lower()
    if normalized not in {"error", "warning", "information"}:
        raise ValueError(f"Unsupported validation severity: {severity}")
    issue: dict[str, Any] = {"severity": normalized, "code": code, "message": message}
    if file is not None:
        issue["file"] = file
    if sequence is not None:
        issue["sequence"] = sequence
    report["issues"].append(issue)
    report["counts"][f"{normalized}s" if normalized != "information" else "information"] += 1


def _read_json_for_validation(
    path: Path,
    report: dict[str, Any],
    *,
    code: str,
    display_file: str,
) -> Any | None:
    try:
        return json.loads(path.read_bytes())
    except OSError as exc:
        _add_validation_issue(
            report,
            "error",
            code,
            f"Could not read JSON file: {type(exc).__name__}: {exc}",
            file=display_file,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _add_validation_issue(
            report,
            "error",
            code,
            f"Invalid JSON: {type(exc).__name__}: {exc}",
            file=display_file,
        )
    return None


def _scan_privacy_file(path: Path, display_file: str, report: dict[str, Any]) -> None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        _add_validation_issue(
            report,
            "error",
            "PRIVACY_SCAN_READ_ERROR",
            f"Could not read file for privacy scanning: {type(exc).__name__}: {exc}",
            file=display_file,
        )
        return
    if UNREDACTED_CRUMB_RE.search(text):
        _add_validation_issue(
            report,
            "error",
            "UNREDACTED_CRUMB",
            "Potential unredacted Yahoo crumb was detected.",
            file=display_file,
        )
    if SENSITIVE_HEADER_RE.search(text):
        _add_validation_issue(
            report,
            "error",
            "SENSITIVE_HEADER_VALUE",
            "Potential Cookie, Set-Cookie, or Authorization header value was detected.",
            file=display_file,
        )
    if SENSITIVE_JSON_VALUE_RE.search(text):
        _add_validation_issue(
            report,
            "error",
            "SENSITIVE_JSON_VALUE",
            "Potential cookie, crumb, or authorization value was detected in JSON.",
            file=display_file,
        )
    if LOCAL_ABSOLUTE_PATH_RE.search(text):
        _add_validation_issue(
            report,
            "warning",
            "LOCAL_ABSOLUTE_PATH",
            "A local user-directory path was found; v0.4.2 stores portable path references instead.",
            file=display_file,
        )


def _write_validation_text(path: Path, report: Mapping[str, Any]) -> None:
    counts = report["counts"]
    lines = [
        f"Run validation: {report['status']}",
        f"Run folder: {report['run_folder']}",
        f"Run ID: {report.get('run_id') or ''}",
        f"Validated UTC: {report['validated_at_utc']}",
        "",
        f"Errors: {counts['errors']}",
        f"Warnings: {counts['warnings']}",
        f"Information: {counts['information']}",
        f"Manifest requests: {counts['manifest_requests']}",
        f"Raw files checked: {counts['raw_files_checked']}",
        f"Metadata files checked: {counts['metadata_files_checked']}",
        f"Normalized files checked: {counts['normalized_files_checked']}",
        f"Error files checked: {counts['error_files_checked']}",
        f"Known JSON paths observed: {counts['known_json_paths_observed']}",
        f"Unmapped JSON paths observed: {counts['unmapped_json_paths_observed']}",
        "",
        "Issues:",
    ]
    if report["issues"]:
        for issue in report["issues"]:
            location = issue.get("file", "")
            sequence = issue.get("sequence")
            context = ""
            if sequence is not None:
                context += f" sequence={sequence}"
            if location:
                context += f" file={location}"
            lines.append(
                f"- {issue['severity'].upper()} {issue['code']}:{context} {issue['message']}"
            )
    else:
        lines.append("- None")
    lines.extend(["", "Unmapped JSON paths:"])
    if report["unmapped_json_paths"]:
        lines.extend(f"- {item}" for item in report["unmapped_json_paths"])
    else:
        lines.append("- None")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def validate_run(
    run_dir: Path,
    *,
    master_field_path: Path = DEFAULT_MASTER_FIELDS,
    write_reports: bool = True,
) -> dict[str, Any]:
    """Validate a completed capture run without modifying its evidence files."""
    run_dir = _resolve_path(run_dir)
    report = _new_validation_report(run_dir)
    if not run_dir.is_dir():
        _add_validation_issue(report, "error", "RUN_FOLDER_MISSING", "Run folder does not exist.")
        return report

    manifest_path = run_dir / "run-manifest.json"
    if not manifest_path.is_file():
        _add_validation_issue(
            report,
            "error",
            "MANIFEST_MISSING",
            "run-manifest.json is missing.",
            file="run-manifest.json",
        )
        if write_reports:
            write_json(run_dir / VALIDATION_JSON_FILENAME, report)
            _write_validation_text(run_dir / VALIDATION_TEXT_FILENAME, report)
        return report

    manifest = _read_json_for_validation(
        manifest_path,
        report,
        code="MANIFEST_INVALID_JSON",
        display_file="run-manifest.json",
    )
    if not isinstance(manifest, dict):
        if manifest is not None:
            _add_validation_issue(
                report,
                "error",
                "MANIFEST_NOT_OBJECT",
                "The run manifest must be a JSON object.",
                file="run-manifest.json",
            )
        if write_reports:
            write_json(run_dir / VALIDATION_JSON_FILENAME, report)
            _write_validation_text(run_dir / VALIDATION_TEXT_FILENAME, report)
        return report

    report["run_id"] = manifest.get("run_id")
    if manifest.get("run_id") != run_dir.name:
        _add_validation_issue(
            report,
            "error",
            "RUN_ID_FOLDER_MISMATCH",
            "Manifest run_id does not match the run-folder name.",
            file="run-manifest.json",
        )
    if not manifest.get("run_completed_at_utc"):
        _add_validation_issue(
            report,
            "warning",
            "RUN_NOT_MARKED_COMPLETE",
            "The manifest does not contain run_completed_at_utc.",
            file="run-manifest.json",
        )

    for field_name in ("input_file", "master_field_database"):
        if is_absolute_path_text(manifest.get(field_name)):
            _add_validation_issue(
                report,
                "warning",
                "NONPORTABLE_MANIFEST_PATH",
                f"Manifest field {field_name!r} contains an absolute local path.",
                file="run-manifest.json",
            )

    requests = manifest.get("requests")
    if not isinstance(requests, list):
        _add_validation_issue(
            report,
            "error",
            "REQUESTS_NOT_ARRAY",
            "Manifest requests must be an array.",
            file="run-manifest.json",
        )
        requests = []
    report["counts"]["manifest_requests"] = len(requests)

    declared_count = manifest.get("symbols_requested")
    if declared_count != len(requests):
        _add_validation_issue(
            report,
            "error",
            "REQUEST_COUNT_MISMATCH",
            "symbols_requested does not equal the number of manifest request entries.",
            file="run-manifest.json",
        )

    request_order = manifest.get("request_order")
    if not isinstance(request_order, list):
        _add_validation_issue(
            report,
            "error",
            "REQUEST_ORDER_NOT_ARRAY",
            "request_order must be an array.",
            file="run-manifest.json",
        )
        request_order = []

    master_fields = load_master_fields(master_field_path)
    known_paths_observed: set[str] = set()
    unmapped_paths_observed: set[str] = set()
    classifications: dict[str, int] = {}
    expected_files: dict[str, set[str]] = {
        "raw": set(),
        "metadata": set(),
        "normalized": set(),
        "errors": set(),
    }
    referenced_files: set[str] = set()
    seen_sequences: set[int] = set()

    for index, request_meta in enumerate(requests, start=1):
        if not isinstance(request_meta, dict):
            _add_validation_issue(
                report,
                "error",
                "REQUEST_ENTRY_NOT_OBJECT",
                "A manifest request entry is not a JSON object.",
                sequence=index,
            )
            continue
        sequence = request_meta.get("sequence")
        if not isinstance(sequence, int):
            _add_validation_issue(
                report,
                "error",
                "SEQUENCE_NOT_INTEGER",
                "Request sequence must be an integer.",
                sequence=index,
            )
            sequence = index
        if sequence in seen_sequences:
            _add_validation_issue(
                report,
                "error",
                "DUPLICATE_SEQUENCE",
                "Duplicate request sequence was found.",
                sequence=sequence,
            )
        seen_sequences.add(sequence)
        if sequence != index:
            _add_validation_issue(
                report,
                "error",
                "SEQUENCE_ORDER_MISMATCH",
                "Request sequence does not match manifest array order.",
                sequence=sequence,
            )

        requested_symbol = request_meta.get("requested_symbol")
        if index <= len(request_order) and request_order[index - 1] != requested_symbol:
            _add_validation_issue(
                report,
                "error",
                "REQUEST_ORDER_SYMBOL_MISMATCH",
                "request_order does not match the request entry symbol.",
                sequence=sequence,
            )

        classification = request_meta.get("result_classification")
        if isinstance(classification, str):
            classifications[classification.lower()] = classifications.get(classification.lower(), 0) + 1

        file_fields = (
            ("metadata_file", "metadata"),
            ("raw_response_file", "raw"),
            ("normalized_output_file", "normalized"),
            ("error_file", "errors"),
        )
        resolved_files: dict[str, Path] = {}
        for field_name, folder_name in file_fields:
            relative_value = request_meta.get(field_name)
            if not relative_value:
                continue
            if not isinstance(relative_value, str):
                _add_validation_issue(
                    report,
                    "error",
                    "FILE_REFERENCE_NOT_STRING",
                    f"{field_name} must be a string or null.",
                    sequence=sequence,
                )
                continue
            if relative_value in referenced_files:
                _add_validation_issue(
                    report,
                    "error",
                    "DUPLICATE_FILE_REFERENCE",
                    "The same file is referenced by more than one manifest field or request.",
                    file=relative_value,
                    sequence=sequence,
                )
            referenced_files.add(relative_value)
            expected_files[folder_name].add(relative_value)
            resolved = safe_run_path(run_dir, relative_value)
            if resolved is None:
                _add_validation_issue(
                    report,
                    "error",
                    "UNSAFE_FILE_REFERENCE",
                    "Manifest file reference escapes the run directory or is invalid.",
                    file=relative_value,
                    sequence=sequence,
                )
                continue
            resolved_files[field_name] = resolved
            if not resolved.is_file():
                _add_validation_issue(
                    report,
                    "error",
                    "REFERENCED_FILE_MISSING",
                    "A file referenced by the manifest is missing.",
                    file=relative_value,
                    sequence=sequence,
                )
                continue
            if not resolved.name.startswith(f"{sequence:04d}_"):
                _add_validation_issue(
                    report,
                    "error",
                    "FILENAME_SEQUENCE_MISMATCH",
                    "Referenced filename does not begin with the four-digit request sequence.",
                    file=relative_value,
                    sequence=sequence,
                )

        metadata_path = resolved_files.get("metadata_file")
        if metadata_path and metadata_path.is_file():
            report["counts"]["metadata_files_checked"] += 1
            metadata_rel = request_meta.get("metadata_file")
            metadata = _read_json_for_validation(
                metadata_path,
                report,
                code="METADATA_INVALID_JSON",
                display_file=str(metadata_rel),
            )
            if isinstance(metadata, dict) and metadata != request_meta:
                _add_validation_issue(
                    report,
                    "error",
                    "METADATA_MANIFEST_MISMATCH",
                    "Metadata sidecar does not exactly match its manifest request entry.",
                    file=str(metadata_rel),
                    sequence=sequence,
                )

        raw_path = resolved_files.get("raw_response_file")
        if raw_path and raw_path.is_file():
            report["counts"]["raw_files_checked"] += 1
            raw_rel = str(request_meta.get("raw_response_file"))
            try:
                raw_bytes = raw_path.read_bytes()
            except OSError as exc:
                _add_validation_issue(
                    report,
                    "error",
                    "RAW_READ_ERROR",
                    f"Could not read raw response: {type(exc).__name__}: {exc}",
                    file=raw_rel,
                    sequence=sequence,
                )
                raw_bytes = b""
            expected_hash = request_meta.get("raw_response_sha256")
            actual_hash = hashlib.sha256(raw_bytes).hexdigest()
            if expected_hash != actual_hash:
                _add_validation_issue(
                    report,
                    "error",
                    "RAW_SHA256_MISMATCH",
                    "Raw response SHA-256 does not match the manifest.",
                    file=raw_rel,
                    sequence=sequence,
                )
            if request_meta.get("response_bytes") != len(raw_bytes):
                _add_validation_issue(
                    report,
                    "error",
                    "RAW_SIZE_MISMATCH",
                    "Raw response byte count does not match the manifest.",
                    file=raw_rel,
                    sequence=sequence,
                )
            if raw_path.suffix.lower() == ".json" or "json" in str(request_meta.get("content_type", "")).lower():
                try:
                    parsed_raw = json.loads(raw_bytes)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    _add_validation_issue(
                        report,
                        "error",
                        "RAW_INVALID_JSON",
                        f"Raw response expected to be JSON but could not be parsed: {type(exc).__name__}: {exc}",
                        file=raw_rel,
                        sequence=sequence,
                    )
                else:
                    for field in flatten_json(parsed_raw):
                        normalized_path = normalize_array_path(field.json_path)
                        if normalized_path in master_fields:
                            known_paths_observed.add(normalized_path)
                        else:
                            unmapped_paths_observed.add(normalized_path)

        normalized_path = resolved_files.get("normalized_output_file")
        if normalized_path and normalized_path.is_file():
            report["counts"]["normalized_files_checked"] += 1
        error_path = resolved_files.get("error_file")
        if error_path and error_path.is_file():
            report["counts"]["error_files_checked"] += 1

    expected_sequences = set(range(1, len(requests) + 1))
    if seen_sequences != expected_sequences:
        _add_validation_issue(
            report,
            "error",
            "SEQUENCE_SET_MISMATCH",
            "Request sequences are not exactly 1 through the manifest request count.",
            file="run-manifest.json",
        )

    declared_summary = manifest.get("summary")
    if not isinstance(declared_summary, dict):
        _add_validation_issue(
            report,
            "error",
            "SUMMARY_NOT_OBJECT",
            "Manifest summary must be a JSON object.",
            file="run-manifest.json",
        )
    else:
        for key in summary_template():
            actual = classifications.get(key, 0)
            if declared_summary.get(key) != actual:
                _add_validation_issue(
                    report,
                    "error",
                    "SUMMARY_CLASSIFICATION_MISMATCH",
                    f"Summary count for {key!r} does not match request classifications.",
                    file="run-manifest.json",
                )

    for folder_name, expected in expected_files.items():
        folder = run_dir / folder_name
        if not folder.is_dir():
            _add_validation_issue(
                report,
                "error",
                "RUN_SUBFOLDER_MISSING",
                f"Required run subfolder {folder_name!r} is missing.",
                file=folder_name,
            )
            continue
        actual = {
            item.relative_to(run_dir).as_posix()
            for item in folder.rglob("*")
            if item.is_file()
        }
        for extra in sorted(actual - expected):
            _add_validation_issue(
                report,
                "error",
                "UNREFERENCED_EXTRA_FILE",
                "File exists in a run subfolder but is not referenced by the manifest.",
                file=extra,
            )

    scan_files = [manifest_path]
    for folder_name in ("metadata", "normalized", "errors", "raw"):
        folder = run_dir / folder_name
        if folder.is_dir():
            scan_files.extend(item for item in folder.rglob("*") if item.is_file())
    for scan_path in scan_files:
        display = scan_path.relative_to(run_dir).as_posix()
        _scan_privacy_file(scan_path, display, report)

    report["known_json_paths"] = sorted(known_paths_observed)
    report["unmapped_json_paths"] = sorted(unmapped_paths_observed)
    report["counts"]["known_json_paths_observed"] = len(known_paths_observed)
    report["counts"]["unmapped_json_paths_observed"] = len(unmapped_paths_observed)
    if unmapped_paths_observed:
        _add_validation_issue(
            report,
            "information",
            "UNMAPPED_JSON_PATHS_OBSERVED",
            f"Observed {len(unmapped_paths_observed)} distinct JSON paths not present in the current master field database.",
        )

    errors = report["counts"]["errors"]
    warnings = report["counts"]["warnings"]
    report["status"] = "FAIL" if errors else ("PASS_WITH_WARNINGS" if warnings else "PASS")
    if write_reports:
        write_json(run_dir / VALIDATION_JSON_FILENAME, report)
        _write_validation_text(run_dir / VALIDATION_TEXT_FILENAME, report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture one Yahoo Finance Quote response per symbol and preserve evidence files."
    )
    parser.add_argument(
        "--validate-run",
        type=Path,
        help="Validate an existing run folder and write run-validation.json and run-validation.txt.",
    )
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument(
        "--symbols-file",
        type=Path,
        default=DEFAULT_SYMBOLS_FILE,
        help=f"CSV symbol table (default: {DEFAULT_SYMBOLS_FILE}).",
    )
    input_group.add_argument("--symbols", help="Comma-separated symbols for a quick run instead of a CSV table.")
    parser.add_argument("--endpoint", choices=[DEFAULT_ENDPOINT_ID], default=DEFAULT_ENDPOINT_ID)
    parser.add_argument(
        "--auth-mode",
        choices=["anonymous-crumb", "none"],
        default="anonymous-crumb",
        help="Yahoo request authentication mode. Default establishes an anonymous in-memory cookie and crumb.",
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        default=DEFAULT_OUTDIR,
        help=f"Parent output directory (default: {DEFAULT_OUTDIR}).",
    )
    parser.add_argument(
        "--master-fields",
        type=Path,
        default=DEFAULT_MASTER_FIELDS,
        help="Master field database used to order normalized fields.",
    )
    parser.add_argument("--pause-ms", type=int, default=1000, help="Pause between symbols in milliseconds.")
    parser.add_argument("--timeout", type=float, default=30.0, help="Per-attempt request timeout in seconds.")
    parser.add_argument("--max-attempts", type=int, default=3, help="Maximum attempts per symbol, including the first.")
    parser.add_argument(
        "--backoff-seconds",
        default="2,5",
        help="Comma-separated retry delays; the last value repeats when needed.",
    )
    parser.add_argument(
        "--user-agent",
        default=DEFAULT_USER_AGENT,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and display the request order without contacting Yahoo.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.validate_run is not None:
            report = validate_run(args.validate_run, master_field_path=args.master_fields)
            print(f"Validation status: {report['status']}")
            print(f"Run folder: {args.validate_run}")
            print(f"Errors: {report['counts']['errors']}")
            print(f"Warnings: {report['counts']['warnings']}")
            print(f"Unmapped JSON paths: {report['counts']['unmapped_json_paths_observed']}")
            print(f"Reports: {args.validate_run / VALIDATION_JSON_FILENAME}")
            print(f"         {args.validate_run / VALIDATION_TEXT_FILENAME}")
            return 0 if report["counts"]["errors"] == 0 else 2

        retry_policy = RetryPolicy(
            maximum_attempts=args.max_attempts,
            backoff_seconds=parse_backoff(args.backoff_seconds, args.max_attempts),
        )
        if args.symbols:
            symbol_requests = parse_symbols_argument(args.symbols)
            input_file = "--symbols"
        else:
            symbol_requests = load_symbol_table(args.symbols_file)
            input_file = str(args.symbols_file)

        if args.dry_run:
            print(f"Utility version: {UTILITY_VERSION}")
            print(f"Capture schema: {CAPTURE_SCHEMA_VERSION}")
            print(f"Endpoint: {DEFAULT_ENDPOINT_ID}")
            print(f"Authentication: {args.auth_mode}")
            print(f"Default output: {DEFAULT_OUTDIR}")
            print(f"Symbols ({len(symbol_requests)}):")
            for sequence, item in enumerate(symbol_requests, start=1):
                print(f"  {sequence:02d}. {item.symbol} [{item.project_security_type or 'Unspecified'}]")
            return 0

        run_dir, manifest = run_capture(
            symbol_requests,
            outdir=args.outdir,
            input_file=input_file,
            master_field_path=args.master_fields,
            pause_between_requests_ms=args.pause_ms,
            timeout_seconds=args.timeout,
            retry_policy=retry_policy,
            user_agent=args.user_agent,
            auth_mode=args.auth_mode,
            repository_root=REPOSITORY_ROOT,
            progress=print,
        )
    except CaptureInputError as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        print("Capture interrupted by user.", file=sys.stderr)
        return 130
    except OSError as exc:
        print(f"Capture failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"Run folder: {run_dir}")
    for key, value in manifest["summary"].items():
        print(f"  {key}: {value}")
    return 0 if manifest["summary"]["success_result_returned"] == manifest["symbols_requested"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
