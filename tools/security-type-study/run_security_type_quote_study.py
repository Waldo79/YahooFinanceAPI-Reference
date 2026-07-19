#!/usr/bin/env python3
r"""Run Study 02A: a Quote endpoint baseline across twelve security types.

The study changes one controlled variable: the reviewed project security type.
Every request uses the same Quote request pattern and a prepared anonymous
cookie-plus-crumb session.

Run from repository root:

    py tools\security-type-study\run_security_type_quote_study.py --dry-run
    py tools\security-type-study\run_security_type_quote_study.py

Analyze the completed run:

    py tools\endpoint-analysis\analyze_endpoint_captures.py "<study run folder>"

The script uses only the Python standard library. Cookie and crumb values are
never written to evidence.
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
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener

TOOL_VERSION = "0.1.0"
STUDY_SCHEMA_VERSION = "0.5.0"

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPOSITORY_ROOT / "config" / "studies" / "study-02a-security-type-quote.json"
DEFAULT_OUTDIR = REPOSITORY_ROOT / "captures" / "local"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/150.0.0.0 Safari/537.36"
)

COOKIE_URLS = (
    (
        "basic-query1",
        "https://fc.yahoo.com",
        "https://query1.finance.yahoo.com/v1/test/getcrumb",
    ),
    (
        "finance-query2-fallback",
        "https://finance.yahoo.com/quote/AAPL",
        "https://query2.finance.yahoo.com/v1/test/getcrumb",
    ),
)

RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
AUTH_REFRESH_STATUSES = {401, 403}
SENSITIVE_QUERY_KEYS = {"crumb", "cookie", "authorization", "token", "auth", "session"}

RESULT_COLUMNS = [
    "sequence",
    "sample_id",
    "symbol",
    "name",
    "project_security_type",
    "expected_quote_type",
    "expected_exchange",
    "selection_role",
    "http_status",
    "result_classification",
    "requested_symbol_returned",
    "returned_record_count",
    "returned_symbol",
    "returned_quote_type",
    "quote_type_match",
    "type_disp",
    "exchange",
    "full_exchange_name",
    "currency",
    "exchange_timezone_name",
    "market_state",
    "market",
    "regular_market_price",
    "regular_market_time",
    "field_count",
    "response_bytes",
    "raw_response_sha256",
    "canonical_json_sha256",
    "attempt_count",
    "auth_refresh_performed",
    "requested_at_utc",
    "response_received_at_utc",
    "elapsed_ms",
    "request_parameters_sha256",
    "raw_response_file",
    "metadata_file",
]

SUMMARY_COLUMNS = [
    "expected_quote_type",
    "subject_count",
    "http_response_count",
    "expected_symbol_returned_count",
    "quote_type_match_count",
    "quote_type_mismatch_count",
    "returned_quote_types_json",
    "project_security_types_json",
    "symbols_json",
]

SELECTED_QUOTE_FIELDS = (
    "symbol",
    "quoteType",
    "typeDisp",
    "exchange",
    "fullExchangeName",
    "currency",
    "exchangeTimezoneName",
    "marketState",
    "market",
    "regularMarketPrice",
    "regularMarketTime",
)


class StudyError(RuntimeError):
    """Raised when the study definition or run state is invalid."""


@dataclass(frozen=True)
class EndpointDefinition:
    endpoint_id: str
    method: str
    base_url: str
    params: dict[str, str]
    expected_top_level: str


@dataclass(frozen=True)
class SubjectDefinition:
    subject_id: str
    symbol: str
    name: str
    project_security_type: str
    expected_quote_type: str
    expected_exchange: str
    selection_role: str
    source_inventory: str


@dataclass(frozen=True)
class PlannedRequest:
    sequence: int
    sample_id: str
    subject: SubjectDefinition
    endpoint: EndpointDefinition
    request_parameters: dict[str, str]
    request_parameters_sha256: str


@dataclass
class Attempt:
    attempt: int
    requested_at_utc: str
    response_received_at_utc: str
    elapsed_ms: int
    http_status: int | None
    error: str | None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def filename_utc(value: datetime) -> str:
    return format_utc(value).replace(":", "-")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return cleaned or "subject"


def redact_url(url: str) -> str:
    parts = urlsplit(url)
    query: list[tuple[str, str]] = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        query.append((key, "REDACTED" if key.lower() in SENSITIVE_QUERY_KEYS else value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def build_url(base_url: str, params: dict[str, str], crumb: str | None) -> str:
    merged = dict(params)
    if crumb:
        merged["crumb"] = crumb
    parts = urlsplit(base_url)
    prior = dict(parse_qsl(parts.query, keep_blank_values=True))
    prior.update(merged)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(prior), parts.fragment))


def make_request(url: str, accept: str = "application/json,text/plain,*/*") -> Request:
    return Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": accept,
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "close",
        },
    )


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


def portable_source_path(definition_path: Path) -> str:
    resolved = definition_path.expanduser().resolve()
    try:
        return resolved.relative_to(REPOSITORY_ROOT.resolve()).as_posix()
    except ValueError:
        return resolved.name


def load_study_definition(path: Path) -> tuple[dict[str, Any], EndpointDefinition, list[SubjectDefinition]]:
    try:
        definition = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise StudyError(f"Could not read study definition {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise StudyError(f"Study definition is not valid JSON: {exc}") from exc

    raw_endpoint = definition.get("endpoint")
    raw_subjects = definition.get("subjects")
    if not isinstance(raw_endpoint, dict):
        raise StudyError("Study definition must contain an endpoint object.")
    if not isinstance(raw_subjects, list) or not raw_subjects:
        raise StudyError("Study definition must contain a nonempty subjects array.")

    endpoint = EndpointDefinition(
        endpoint_id=str(raw_endpoint["endpoint_id"]),
        method=str(raw_endpoint.get("method") or "GET").upper(),
        base_url=str(raw_endpoint["base_url"]),
        params={str(key): str(value) for key, value in dict(raw_endpoint.get("params") or {}).items()},
        expected_top_level=str(raw_endpoint["expected_top_level"]),
    )
    if endpoint.endpoint_id != "quote":
        raise StudyError("Study 02A requires endpoint_id=quote.")
    if endpoint.method != "GET":
        raise StudyError("Study 02A currently supports GET only.")
    if "{symbol}" not in canonical_json(endpoint.params):
        raise StudyError("Study 02A endpoint params must contain a {symbol} placeholder.")

    subjects: list[SubjectDefinition] = []
    seen_symbols: set[str] = set()
    seen_subject_ids: set[str] = set()
    for raw in raw_subjects:
        subject = SubjectDefinition(
            subject_id=str(raw["subject_id"]),
            symbol=str(raw["symbol"]),
            name=str(raw.get("name") or ""),
            project_security_type=str(raw["project_security_type"]),
            expected_quote_type=str(raw["expected_quote_type"]),
            expected_exchange=str(raw.get("expected_exchange") or ""),
            selection_role=str(raw.get("selection_role") or ""),
            source_inventory=str(raw.get("source_inventory") or ""),
        )
        if not subject.symbol.strip():
            raise StudyError("Every subject must have a nonempty symbol.")
        if subject.symbol in seen_symbols:
            raise StudyError(f"Duplicate symbol: {subject.symbol}")
        if subject.subject_id in seen_subject_ids:
            raise StudyError(f"Duplicate subject_id: {subject.subject_id}")
        seen_symbols.add(subject.symbol)
        seen_subject_ids.add(subject.subject_id)
        subjects.append(subject)

    if len(subjects) != 12:
        raise StudyError(f"Study 02A requires exactly twelve subjects; found {len(subjects)}.")
    return definition, endpoint, subjects


def build_execution_plan(
    definition: dict[str, Any],
    endpoint: EndpointDefinition,
    subjects: list[SubjectDefinition],
    *,
    run_id: str,
) -> list[PlannedRequest]:
    del definition
    plan: list[PlannedRequest] = []
    for sequence, subject in enumerate(subjects, 1):
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
        plan.append(
            PlannedRequest(
                sequence=sequence,
                sample_id=f"{run_id}_{sequence:06d}_quote_{safe_filename(subject.symbol)}",
                subject=subject,
                endpoint=endpoint,
                request_parameters=params,
                request_parameters_sha256=fingerprint,
            )
        )
    return plan


class PreparedYahooSession:
    def __init__(
        self,
        *,
        timeout: float,
        opener: Any | None = None,
        cookie_jar: CookieJar | None = None,
    ):
        self.timeout = timeout
        self.cookie_jar = cookie_jar or CookieJar()
        self.opener = opener or build_opener(HTTPCookieProcessor(self.cookie_jar))
        self.crumb: str | None = None
        self.strategy: str | None = None
        self.refresh_count = 0
        self.prepare_count = 0

    def open(self, request: Request):
        return self.opener.open(request, timeout=self.timeout)

    def _prime_cookie(self, url: str) -> str | None:
        try:
            with self.open(make_request(url, "text/html,application/xhtml+xml,*/*")) as response:
                response.read()
            return None
        except HTTPError as exc:
            try:
                exc.read()
            except Exception:
                pass
            return f"cookie bootstrap returned HTTP {exc.code}"
        except (URLError, TimeoutError, socket.timeout, OSError) as exc:
            return f"cookie bootstrap failed: {type(exc).__name__}: {exc}"

    @staticmethod
    def _valid_crumb(text: str) -> str | None:
        crumb = text.strip()
        lower = crumb.lower()
        if not crumb or len(crumb) > 512:
            return None
        if "\r" in crumb or "\n" in crumb:
            return None
        if "too many requests" in lower or lower.startswith("<html") or lower.startswith("<!doctype"):
            return None
        return crumb

    def _fetch_crumb(self, url: str) -> str:
        with self.open(make_request(url, "text/plain,*/*")) as response:
            body = response.read()
            status = int(getattr(response, "status", response.getcode()))
        if not 200 <= status < 300:
            raise StudyError(f"crumb endpoint returned HTTP {status}")
        crumb = self._valid_crumb(body.decode("utf-8", errors="replace"))
        if crumb is None:
            raise StudyError("crumb endpoint returned an invalid or empty value")
        return crumb

    def prepare(self, force_refresh: bool = False) -> str:
        if self.crumb is not None and not force_refresh:
            return self.crumb

        if force_refresh:
            self.refresh_count += 1
            self.crumb = None
            self.strategy = None
            try:
                self.cookie_jar.clear()
            except Exception:
                pass

        self.prepare_count += 1
        errors: list[str] = []
        for strategy, cookie_url, crumb_url in COOKIE_URLS:
            note = self._prime_cookie(cookie_url)
            try:
                crumb = self._fetch_crumb(crumb_url)
            except Exception as exc:
                errors.append(f"{strategy}: {note + '; ' if note else ''}{exc}")
                continue
            self.crumb = crumb
            self.strategy = strategy
            return crumb

        raise StudyError("Could not establish anonymous Yahoo session: " + "; ".join(errors))

    def public_summary(self) -> dict[str, Any]:
        return {
            "session_strategy": self.strategy,
            "cookie_count": sum(1 for _ in self.cookie_jar),
            "crumb_retrieved": self.crumb is not None,
            "session_refresh_count": self.refresh_count,
            "sensitive_values_persisted": False,
        }


def classify_quote_response(
    parsed: Any,
    requested_symbol: str,
) -> tuple[str, bool, list[str], dict[str, Any] | None, Any]:
    if not isinstance(parsed, dict):
        return "JSON_TOP_LEVEL_NOT_OBJECT", False, [], None, None
    if "quoteResponse" not in parsed:
        return "EXPECTED_TOP_LEVEL_MISSING", False, [], None, None

    quote_response = parsed["quoteResponse"]
    if not isinstance(quote_response, dict):
        return "QUOTE_RESPONSE_NOT_OBJECT", True, [], None, None

    response_error = quote_response.get("error")
    results = quote_response.get("result")
    if response_error not in (None, {}, []):
        return "QUOTE_RESPONSE_ERROR", True, [], None, response_error
    if not isinstance(results, list):
        return "QUOTE_RESULT_NOT_ARRAY", True, [], None, response_error

    returned_symbols = [
        str(item.get("symbol"))
        for item in results
        if isinstance(item, dict) and item.get("symbol") not in (None, "")
    ]
    exact = next(
        (
            item
            for item in results
            if isinstance(item, dict) and str(item.get("symbol") or "") == requested_symbol
        ),
        None,
    )
    if exact is not None:
        return "EXPECTED_SYMBOL_RETURNED", True, returned_symbols, exact, response_error
    if not results:
        return "EMPTY_RESULT", True, returned_symbols, None, response_error
    return "REQUESTED_SYMBOL_MISSING_FROM_RESULT", True, returned_symbols, None, response_error


def selected_quote_values(record: dict[str, Any] | None) -> dict[str, Any]:
    record = record or {}
    return {field: record.get(field) for field in SELECTED_QUOTE_FIELDS}


def capture_one(
    planned: PlannedRequest,
    session: PreparedYahooSession,
    *,
    maximum_attempts: int,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = utc_now,
    clock: Callable[[], float] = time.perf_counter,
) -> tuple[bytes, dict[str, Any]]:
    attempts: list[Attempt] = []
    body = b""
    status: int | None = None
    content_type = ""
    final_url = ""
    final_error: str | None = None
    auth_refresh_used = False
    refresh_on_next_attempt = False

    for attempt_number in range(1, maximum_attempts + 1):
        crumb = session.prepare(force_refresh=refresh_on_next_attempt)
        refresh_on_next_attempt = False
        url = build_url(planned.endpoint.base_url, planned.request_parameters, crumb)
        requested_at = now()
        started = clock()

        try:
            with session.open(make_request(url)) as response:
                body = response.read()
                status = int(getattr(response, "status", response.getcode()))
                content_type = response.headers.get("Content-Type", "")
                final_url = getattr(response, "url", url)
                error = None
        except HTTPError as exc:
            status = exc.code
            content_type = exc.headers.get("Content-Type", "") if exc.headers else ""
            final_url = getattr(exc, "url", url)
            try:
                body = exc.read()
            except Exception:
                body = b""
            error = f"HTTPError: {exc.code} {exc.reason}"
        except (URLError, TimeoutError, socket.timeout, OSError) as exc:
            status = None
            content_type = ""
            final_url = url
            body = b""
            error = f"{type(exc).__name__}: {exc}"

        received_at = now()
        elapsed_ms = round((clock() - started) * 1000)
        attempts.append(
            Attempt(
                attempt=attempt_number,
                requested_at_utc=format_utc(requested_at),
                response_received_at_utc=format_utc(received_at),
                elapsed_ms=elapsed_ms,
                http_status=status,
                error=error,
            )
        )

        if status in AUTH_REFRESH_STATUSES and not auth_refresh_used and attempt_number < maximum_attempts:
            auth_refresh_used = True
            refresh_on_next_attempt = True
            final_error = error
            continue
        if status in RETRYABLE_STATUSES and attempt_number < maximum_attempts:
            final_error = error
            sleep(2 if attempt_number == 1 else 5)
            continue
        final_error = error
        break

    parse_status = "NOT_JSON"
    parse_error = None
    parsed: Any = None
    canonical_json_sha256: str | None = None
    result_classification = "HTTP_OR_NETWORK_ERROR"
    expected_top_level_found = False
    returned_symbols: list[str] = []
    returned_record: dict[str, Any] | None = None
    response_error: Any = None

    if body:
        try:
            parsed = json.loads(body)
        except Exception as exc:
            parse_status = "PARSE_ERROR"
            parse_error = f"{type(exc).__name__}: {exc}"
        else:
            parse_status = "VALID_JSON"
            canonical_json_sha256 = sha256_json(parsed)
            (
                result_classification,
                expected_top_level_found,
                returned_symbols,
                returned_record,
                response_error,
            ) = classify_quote_response(parsed, planned.subject.symbol)

    if status is not None and not 200 <= status < 300 and parse_status == "VALID_JSON":
        result_classification = "HTTP_ERROR_JSON_RETURNED"
    elif status is not None and 200 <= status < 300 and parse_status == "PARSE_ERROR":
        result_classification = "HTTP_SUCCESS_JSON_PARSE_ERROR"

    selected = selected_quote_values(returned_record)
    returned_quote_type = selected.get("quoteType")
    quote_type_match = (
        returned_quote_type == planned.subject.expected_quote_type
        if returned_quote_type not in (None, "")
        else None
    )
    first_attempt = attempts[0]
    last_attempt = attempts[-1]
    session_public = session.public_summary()

    metadata = {
        "study_id": "study-02a-security-type-quote-baseline",
        "study_version": "0.1.0",
        "study_variable": "project_security_type",
        "study_condition": planned.subject.project_security_type,
        "sequence": planned.sequence,
        "sample_id": planned.sample_id,
        "request_id": f"quote-{planned.subject.subject_id}",
        "endpoint_id": planned.endpoint.endpoint_id,
        "request_subject": planned.subject.symbol,
        "requested_symbol": planned.subject.symbol,
        "requested_symbols": [planned.subject.symbol],
        "returned_symbols": returned_symbols,
        "project_security_type": planned.subject.project_security_type,
        "expected_quote_type": planned.subject.expected_quote_type,
        "expected_exchange": planned.subject.expected_exchange,
        "subject_name": planned.subject.name,
        "subject_id": planned.subject.subject_id,
        "selection_role": planned.subject.selection_role,
        "source_inventory": planned.subject.source_inventory,
        "session_mode": "cookie-crumb",
        "method": planned.endpoint.method,
        "request_parameters": dict(planned.request_parameters),
        "request_parameters_canonical": dict(planned.request_parameters),
        "request_parameters_sha256": planned.request_parameters_sha256,
        "request_url_redacted": redact_url(final_url or build_url(
            planned.endpoint.base_url,
            planned.request_parameters,
            "REDACTED",
        )),
        "expected_top_level": planned.endpoint.expected_top_level,
        "expected_top_level_found": expected_top_level_found,
        "requested_symbol_returned": returned_record is not None,
        "returned_record_count": len(returned_symbols),
        "returned_symbol": selected.get("symbol"),
        "returned_quote_type": returned_quote_type,
        "quote_type_match": quote_type_match,
        "selected_quote_fields": selected,
        "returned_field_count": len(returned_record) if returned_record else 0,
        "quote_response_error": response_error,
        "http_status": status,
        "content_type": content_type,
        "response_bytes": len(body),
        "raw_response_sha256": sha256_bytes(body),
        "canonical_json_sha256": canonical_json_sha256,
        "parse_status": parse_status,
        "parse_error": parse_error,
        "result_classification": result_classification,
        "error": final_error,
        "attempt_count": len(attempts),
        "attempts": [asdict(attempt) for attempt in attempts],
        "auth_refresh_performed": auth_refresh_used,
        "requested_at_utc": first_attempt.requested_at_utc,
        "response_received_at_utc": last_attempt.response_received_at_utc,
        "elapsed_ms": sum(attempt.elapsed_ms for attempt in attempts),
        "session_strategy": session_public["session_strategy"],
        "cookie_count": session_public["cookie_count"],
        "crumb_retrieved": session_public["crumb_retrieved"],
        "crumb_sent": True,
        "sensitive_values_persisted": False,
    }
    return body, metadata


def result_row(metadata: dict[str, Any]) -> dict[str, Any]:
    selected = metadata.get("selected_quote_fields") or {}
    return {
        "sequence": metadata["sequence"],
        "sample_id": metadata["sample_id"],
        "symbol": metadata["requested_symbol"],
        "name": metadata["subject_name"],
        "project_security_type": metadata["project_security_type"],
        "expected_quote_type": metadata["expected_quote_type"],
        "expected_exchange": metadata["expected_exchange"],
        "selection_role": metadata["selection_role"],
        "http_status": metadata["http_status"],
        "result_classification": metadata["result_classification"],
        "requested_symbol_returned": metadata["requested_symbol_returned"],
        "returned_record_count": metadata["returned_record_count"],
        "returned_symbol": metadata.get("returned_symbol"),
        "returned_quote_type": metadata.get("returned_quote_type"),
        "quote_type_match": metadata.get("quote_type_match"),
        "type_disp": selected.get("typeDisp"),
        "exchange": selected.get("exchange"),
        "full_exchange_name": selected.get("fullExchangeName"),
        "currency": selected.get("currency"),
        "exchange_timezone_name": selected.get("exchangeTimezoneName"),
        "market_state": selected.get("marketState"),
        "market": selected.get("market"),
        "regular_market_price": selected.get("regularMarketPrice"),
        "regular_market_time": selected.get("regularMarketTime"),
        "field_count": metadata["returned_field_count"],
        "response_bytes": metadata["response_bytes"],
        "raw_response_sha256": metadata["raw_response_sha256"],
        "canonical_json_sha256": metadata["canonical_json_sha256"],
        "attempt_count": metadata["attempt_count"],
        "auth_refresh_performed": metadata["auth_refresh_performed"],
        "requested_at_utc": metadata["requested_at_utc"],
        "response_received_at_utc": metadata["response_received_at_utc"],
        "elapsed_ms": metadata["elapsed_ms"],
        "request_parameters_sha256": metadata["request_parameters_sha256"],
        "raw_response_file": metadata["raw_response_file"],
        "metadata_file": metadata["metadata_file"],
    }


def build_quote_type_summary(metadata_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in metadata_rows:
        grouped.setdefault(str(row["expected_quote_type"]), []).append(row)

    summary: list[dict[str, Any]] = []
    for expected_quote_type in sorted(grouped):
        rows = grouped[expected_quote_type]
        returned_types = sorted({
            str(row["returned_quote_type"])
            for row in rows
            if row.get("returned_quote_type") not in (None, "")
        })
        summary.append(
            {
                "expected_quote_type": expected_quote_type,
                "subject_count": len(rows),
                "http_response_count": sum(row.get("http_status") is not None for row in rows),
                "expected_symbol_returned_count": sum(bool(row.get("requested_symbol_returned")) for row in rows),
                "quote_type_match_count": sum(row.get("quote_type_match") is True for row in rows),
                "quote_type_mismatch_count": sum(row.get("quote_type_match") is False for row in rows),
                "returned_quote_types_json": canonical_json(returned_types),
                "project_security_types_json": canonical_json(sorted(row["project_security_type"] for row in rows)),
                "symbols_json": canonical_json([row["requested_symbol"] for row in rows]),
            }
        )
    return summary


def build_resolved_definition(
    definition: dict[str, Any],
    endpoint: EndpointDefinition,
    subjects: list[SubjectDefinition],
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
        "session_mode": definition["session_mode"],
        "resolved_at_utc": run_started_at_utc,
        "request_order": definition.get("request_order"),
        "endpoint": asdict(endpoint),
        "subjects": [asdict(subject) for subject in subjects],
        "sensitive_values_persisted": False,
    }


def run_study(
    *,
    definition_path: Path,
    output_parent: Path,
    timeout: float,
    maximum_attempts: int,
    pause_ms: int,
    session_factory: Callable[[float], PreparedYahooSession] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = utc_now,
    clock: Callable[[], float] = time.perf_counter,
) -> tuple[Path, dict[str, Any]]:
    run_started = now()
    definition, endpoint, subjects = load_study_definition(definition_path)
    run_id = f"{filename_utc(run_started)}_study-02a-security-type-quote"
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
    )
    resolved_relative = "study-definition.resolved.json"
    resolved_path = run_dir / resolved_relative
    write_json(resolved_path, resolved)
    resolved_bytes = resolved_path.read_bytes()

    plan = build_execution_plan(definition, endpoint, subjects, run_id=run_id)
    session = session_factory(timeout) if session_factory else PreparedYahooSession(timeout=timeout)

    metadata_rows: list[dict[str, Any]] = []
    for index, planned in enumerate(plan):
        print(
            f"[{planned.sequence:02d}/{len(plan)}] "
            f"{planned.subject.symbol} / {planned.subject.project_security_type} ... ",
            end="",
            flush=True,
        )
        body, metadata = capture_one(
            planned,
            session,
            maximum_attempts=maximum_attempts,
            sleep=sleep,
            now=now,
            clock=clock,
        )

        base_name = f"{planned.sequence:03d}_{safe_filename(planned.subject.symbol)}_quote"
        raw_relative = f"raw/{base_name}.raw.json"
        metadata_relative = f"metadata/{base_name}.meta.json"
        error_relative = f"errors/{base_name}.error.txt"

        raw_path = run_dir / raw_relative
        metadata_path = run_dir / metadata_relative
        error_path = run_dir / error_relative
        raw_path.write_bytes(body)
        metadata.update(
            {
                "raw_response_file": raw_relative,
                "metadata_file": metadata_relative,
                "error_file": error_relative if metadata.get("error") else None,
            }
        )
        write_json(metadata_path, metadata)
        if metadata.get("error"):
            error_path.write_text(str(metadata["error"]) + "\n", encoding="utf-8", newline="\n")
        metadata_rows.append(metadata)

        status_display = metadata["http_status"] if metadata["http_status"] is not None else "NO_HTTP"
        print(f"HTTP {status_display} {metadata['result_classification']}")
        if pause_ms > 0 and index + 1 < len(plan):
            sleep(pause_ms / 1000.0)

    results_relative = "comparison/security-type-results.csv"
    summary_relative = "comparison/quote-type-summary.csv"
    write_csv(run_dir / results_relative, RESULT_COLUMNS, map(result_row, metadata_rows))
    write_csv(
        run_dir / summary_relative,
        SUMMARY_COLUMNS,
        build_quote_type_summary(metadata_rows),
    )

    run_completed = now()
    source_bytes = definition_path.read_bytes()
    manifest_requests: list[dict[str, Any]] = []
    for metadata in metadata_rows:
        sidecar = json.loads((run_dir / metadata["metadata_file"]).read_text(encoding="utf-8"))
        if sidecar != metadata:
            raise StudyError(f"Metadata write verification failed: {metadata['metadata_file']}")
        manifest_requests.append(metadata)

    manifest = {
        "study_schema_version": STUDY_SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "run_id": run_id,
        "study_id": definition["study_id"],
        "study_version": definition["study_version"],
        "study_title": definition["study_title"],
        "study_variable": definition["study_variable"],
        "session_mode": definition["session_mode"],
        "study_definition_file": resolved_relative,
        "study_definition_sha256": sha256_bytes(resolved_bytes),
        "study_definition_source_file": portable_source_path(definition_path),
        "study_definition_source_sha256": sha256_bytes(source_bytes),
        "run_started_at_utc": format_utc(run_started),
        "run_completed_at_utc": format_utc(run_completed),
        "default_pause_ms": pause_ms,
        "timeout_seconds": timeout,
        "maximum_attempts": maximum_attempts,
        "request_order": "configured subject order",
        "authentication": session.public_summary(),
        "comparison_files": {
            "security_type_results": results_relative,
            "quote_type_summary": summary_relative,
        },
        "requests": manifest_requests,
        "summary": {
            "planned_request_count": len(plan),
            "evidence_record_count": len(metadata_rows),
            "http_response_count": sum(row["http_status"] is not None for row in metadata_rows),
            "no_http_response_count": sum(row["http_status"] is None for row in metadata_rows),
            "expected_symbol_returned_count": sum(bool(row["requested_symbol_returned"]) for row in metadata_rows),
            "expected_symbol_missing_count": sum(not bool(row["requested_symbol_returned"]) for row in metadata_rows),
            "quote_type_match_count": sum(row.get("quote_type_match") is True for row in metadata_rows),
            "quote_type_mismatch_count": sum(row.get("quote_type_match") is False for row in metadata_rows),
            "subject_count": len(subjects),
            "expected_quote_type_count": len({subject.expected_quote_type for subject in subjects}),
            "all_evidence_records_written": len(metadata_rows) == len(plan),
            "sensitive_values_persisted": False,
        },
    }
    write_json(run_dir / "run-manifest.json", manifest)
    return run_dir, manifest


def print_dry_run(definition_path: Path, *, now: datetime | None = None) -> None:
    run_time = now or utc_now()
    definition, endpoint, subjects = load_study_definition(definition_path)
    run_id = f"{filename_utc(run_time)}_study-02a-security-type-quote"
    plan = build_execution_plan(definition, endpoint, subjects, run_id=run_id)

    print("Study 02A security-type Quote dry run")
    print(f"Study: {definition['study_id']} v{definition['study_version']}")
    print(f"Session mode: {definition['session_mode']}")
    print(f"Planned requests: {len(plan)}")
    for planned in plan:
        url = build_url(planned.endpoint.base_url, planned.request_parameters, "REDACTED")
        print(
            f"[{planned.sequence:02d}/{len(plan)}] "
            f"{planned.subject.symbol} / {planned.subject.project_security_type}"
        )
        print(f"  {redact_url(url)}")
        print(f"  expected quoteType: {planned.subject.expected_quote_type}")
        print(f"  parameter fingerprint: {planned.request_parameters_sha256}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the twelve-subject Study 02A Quote security-type baseline."
    )
    parser.add_argument(
        "--definition",
        type=Path,
        default=DEFAULT_CONFIG,
        help="Study definition JSON. Default: config/studies/study-02a-security-type-quote.json",
    )
    parser.add_argument(
        "--output-parent",
        type=Path,
        default=DEFAULT_OUTDIR,
        help="Parent directory for the timestamped study run.",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--pause-ms", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.attempts <= 0:
        parser.error("--attempts must be positive")
    if args.pause_ms < 0:
        parser.error("--pause-ms cannot be negative")

    definition_path = args.definition.expanduser().resolve()
    if args.dry_run:
        try:
            print_dry_run(definition_path)
        except StudyError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        return 0

    try:
        run_dir, manifest = run_study(
            definition_path=definition_path,
            output_parent=args.output_parent.expanduser().resolve(),
            timeout=args.timeout,
            maximum_attempts=args.attempts,
            pause_ms=args.pause_ms,
        )
    except StudyError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    summary = manifest["summary"]
    print()
    print(f"Study folder: {run_dir}")
    print(f"Evidence records: {summary['evidence_record_count']}/{summary['planned_request_count']}")
    print(f"HTTP responses: {summary['http_response_count']}")
    print(f"Expected symbols returned: {summary['expected_symbol_returned_count']}")
    print(f"quoteType matches: {summary['quote_type_match_count']}")
    print("Resolved definition: study-definition.resolved.json")
    print("Security-type results: comparison\\security-type-results.csv")
    print("Quote-type summary: comparison\\quote-type-summary.csv")
    return 0 if summary["no_http_response_count"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
