#!/usr/bin/env python3
r"""Run Study 01: seven Yahoo endpoint families under three session modes.

This tool performs 21 controlled requests:

    7 endpoint request patterns x 3 session modes

Session modes:

    cookie-crumb  Prepare anonymous cookies and a crumb, then send the crumb.
    cookie-only   Perform the same preparation, but omit the crumb parameter.
    no-session    Use a fresh opener with no prepared cookie jar or crumb.

Run from repository root:

    py tools\session-mode-study\run_session_mode_study.py --dry-run
    py tools\session-mode-study\run_session_mode_study.py

The output is compatible with the project's endpoint analyzer:

    py tools\endpoint-analysis\analyze_endpoint_captures.py "<study run folder>"

The script uses only the Python standard library. Cookie and crumb values are
never written to evidence.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import socket
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener

TOOL_VERSION = "0.1.1"
STUDY_SCHEMA_VERSION = "0.5.0"

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPOSITORY_ROOT / "config" / "studies" / "study-01-session-modes.json"
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

MODE_RESULTS_COLUMNS = [
    "sequence",
    "sample_id",
    "endpoint_id",
    "request_id",
    "session_mode",
    "http_status",
    "content_type",
    "response_bytes",
    "raw_response_sha256",
    "canonical_json_sha256",
    "parse_status",
    "result_classification",
    "expected_top_level",
    "expected_top_level_found",
    "attempt_count",
    "auth_refresh_performed",
    "session_strategy",
    "cookie_count",
    "crumb_retrieved",
    "crumb_sent",
    "requested_at_utc",
    "response_received_at_utc",
    "elapsed_ms",
    "request_parameters_sha256",
    "raw_response_file",
    "metadata_file",
]

ENDPOINT_SUMMARY_COLUMNS = [
    "endpoint_id",
    "request_id",
    "cookie_crumb_http_status",
    "cookie_crumb_classification",
    "cookie_only_http_status",
    "cookie_only_classification",
    "no_session_http_status",
    "no_session_classification",
    "successful_mode_count",
    "all_modes_received_http_response",
    "all_modes_expected_top_level",
    "response_hashes_equal",
    "canonical_json_hashes_equal",
    "response_sizes_equal",
    "review_note",
]


class StudyError(RuntimeError):
    """Raised when the study definition or run state is invalid."""


@dataclass(frozen=True)
class ModeDefinition:
    session_mode: str
    description: str
    prepare_cookie: bool
    retrieve_crumb: bool
    send_crumb: bool


@dataclass(frozen=True)
class RequestDefinition:
    request_id: str
    endpoint_id: str
    method: str
    base_url: str
    params: dict[str, str]
    expected_top_level: str


@dataclass(frozen=True)
class PlannedRequest:
    sequence: int
    sample_id: str
    request: RequestDefinition
    mode: ModeDefinition
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


def substitute_subject(value: str, subject: str) -> str:
    return value.replace("{subject}", subject)


def load_study_definition(
    path: Path,
    *,
    now: datetime | None = None,
) -> tuple[dict[str, Any], list[ModeDefinition], list[RequestDefinition]]:
    now = now or utc_now()
    try:
        definition = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise StudyError(f"Could not read study definition {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise StudyError(f"Study definition is not valid JSON: {exc}") from exc

    subject = str(definition.get("subject") or "").strip()
    if not subject:
        raise StudyError("Study definition must contain a nonempty subject.")

    raw_modes = definition.get("modes")
    mode_order = definition.get("mode_order")
    raw_requests = definition.get("requests")
    if not isinstance(raw_modes, list) or not raw_modes:
        raise StudyError("Study definition must contain a nonempty modes array.")
    if not isinstance(mode_order, list) or not mode_order:
        raise StudyError("Study definition must contain a nonempty mode_order array.")
    if not isinstance(raw_requests, list) or not raw_requests:
        raise StudyError("Study definition must contain a nonempty requests array.")

    modes_by_id: dict[str, ModeDefinition] = {}
    for raw in raw_modes:
        mode = ModeDefinition(
            session_mode=str(raw["session_mode"]),
            description=str(raw.get("description") or ""),
            prepare_cookie=bool(raw["prepare_cookie"]),
            retrieve_crumb=bool(raw["retrieve_crumb"]),
            send_crumb=bool(raw["send_crumb"]),
        )
        if mode.session_mode in modes_by_id:
            raise StudyError(f"Duplicate session mode: {mode.session_mode}")
        if mode.send_crumb and not mode.retrieve_crumb:
            raise StudyError(f"{mode.session_mode}: send_crumb requires retrieve_crumb")
        if mode.retrieve_crumb and not mode.prepare_cookie:
            raise StudyError(f"{mode.session_mode}: retrieve_crumb requires prepare_cookie")
        modes_by_id[mode.session_mode] = mode

    if set(mode_order) != set(modes_by_id):
        raise StudyError("mode_order must contain each configured session mode exactly once.")
    modes = [modes_by_id[str(mode_id)] for mode_id in mode_order]

    period2 = int(now.timestamp())
    requests: list[RequestDefinition] = []
    seen_request_ids: set[str] = set()
    seen_endpoint_ids: set[str] = set()

    for raw in raw_requests:
        request_id = str(raw["request_id"])
        endpoint_id = str(raw["endpoint_id"])
        if request_id in seen_request_ids:
            raise StudyError(f"Duplicate request_id: {request_id}")
        if endpoint_id in seen_endpoint_ids:
            raise StudyError(f"Study 01 requires one request per endpoint; duplicate: {endpoint_id}")
        seen_request_ids.add(request_id)
        seen_endpoint_ids.add(endpoint_id)

        params = {
            str(key): substitute_subject(str(value), subject)
            for key, value in dict(raw.get("params") or {}).items()
        }
        dynamic_days = raw.get("dynamic_period_days")
        if dynamic_days is not None:
            days = int(dynamic_days)
            if days <= 0:
                raise StudyError(f"{request_id}: dynamic_period_days must be positive")
            params["period1"] = str(int((now - timedelta(days=days)).timestamp()))
            params["period2"] = str(period2)

        requests.append(
            RequestDefinition(
                request_id=request_id,
                endpoint_id=endpoint_id,
                method=str(raw.get("method") or "GET").upper(),
                base_url=substitute_subject(str(raw["base_url"]), subject),
                params=params,
                expected_top_level=str(raw["expected_top_level"]),
            )
        )

    if len(requests) != 7 or len(modes) != 3:
        raise StudyError(
            f"Study 01 must define exactly seven requests and three modes; "
            f"found {len(requests)} requests and {len(modes)} modes."
        )
    return definition, modes, requests


def build_execution_plan(
    definition: dict[str, Any],
    modes: list[ModeDefinition],
    requests: list[RequestDefinition],
    *,
    run_id: str,
) -> list[PlannedRequest]:
    plan: list[PlannedRequest] = []
    sequence = 0
    for request in requests:
        request_fingerprint = {
            "method": request.method,
            "base_url": request.base_url,
            "params": request.params,
            "expected_top_level": request.expected_top_level,
        }
        fingerprint = sha256_json(request_fingerprint)
        for mode in modes:
            sequence += 1
            sample_id = (
                f"{run_id}_{sequence:06d}_{request.endpoint_id}_{mode.session_mode}"
            )
            plan.append(
                PlannedRequest(
                    sequence=sequence,
                    sample_id=sample_id,
                    request=request,
                    mode=mode,
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


class NoPreparedSession:
    def __init__(self, *, timeout: float, opener: Any | None = None):
        self.timeout = timeout
        self.opener = opener or build_opener()

    def open(self, request: Request):
        return self.opener.open(request, timeout=self.timeout)

    def prepare(self, force_refresh: bool = False) -> None:
        del force_refresh
        return None

    def public_summary(self) -> dict[str, Any]:
        return {
            "session_strategy": "no-prepared-session",
            "cookie_count": 0,
            "crumb_retrieved": False,
            "session_refresh_count": 0,
            "sensitive_values_persisted": False,
        }


def classify_json(parsed: Any, expected_top_level: str) -> tuple[str, bool]:
    if not isinstance(parsed, dict):
        return "JSON_TOP_LEVEL_NOT_OBJECT", False
    if expected_top_level not in parsed:
        return "EXPECTED_TOP_LEVEL_MISSING", False
    value = parsed[expected_top_level]
    if value in (None, [], {}):
        return "EXPECTED_TOP_LEVEL_EMPTY", True
    return "EXPECTED_TOP_LEVEL_PRESENT", True


def capture_one(
    planned: PlannedRequest,
    session: PreparedYahooSession | NoPreparedSession,
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
        crumb: str | None = None
        if planned.mode.prepare_cookie:
            prepared_crumb = session.prepare(force_refresh=refresh_on_next_attempt)
            refresh_on_next_attempt = False
            if planned.mode.send_crumb:
                crumb = prepared_crumb

        url = build_url(planned.request.base_url, planned.request.params, crumb)
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

        if (
            status in AUTH_REFRESH_STATUSES
            and planned.mode.prepare_cookie
            and not auth_refresh_used
            and attempt_number < maximum_attempts
        ):
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
    result_classification = "HTTP_OR_NETWORK_ERROR"
    expected_top_level_found = False
    parse_error = None
    parsed: Any = None
    canonical_json_sha256: str | None = None

    if body:
        try:
            parsed = json.loads(body)
        except Exception as exc:
            parse_status = "PARSE_ERROR"
            parse_error = f"{type(exc).__name__}: {exc}"
        else:
            parse_status = "VALID_JSON"
            canonical_json_sha256 = sha256_json(parsed)
            result_classification, expected_top_level_found = classify_json(
                parsed, planned.request.expected_top_level
            )

    if status is not None and 200 <= status < 300 and parse_status == "PARSE_ERROR":
        result_classification = "HTTP_SUCCESS_JSON_PARSE_ERROR"
    elif status is not None and not 200 <= status < 300 and parse_status == "VALID_JSON":
        if expected_top_level_found:
            result_classification = "HTTP_ERROR_EXPECTED_TOP_LEVEL_PRESENT"
        else:
            result_classification = "HTTP_ERROR_JSON_RETURNED"

    session_public = session.public_summary()
    first_attempt = attempts[0]
    last_attempt = attempts[-1]
    metadata = {
        "study_id": "study-01-session-mode-requirements",
        "study_version": "0.1.0",
        "study_variable": "session_mode",
        "study_condition": planned.mode.session_mode,
        "sequence": planned.sequence,
        "sample_id": planned.sample_id,
        "request_id": planned.request.request_id,
        "endpoint_id": planned.request.endpoint_id,
        "session_mode": planned.mode.session_mode,
        "session_mode_description": planned.mode.description,
        "method": planned.request.method,
        "request_parameters": dict(planned.request.params),
        "request_parameters_canonical": dict(planned.request.params),
        "request_parameters_sha256": planned.request_parameters_sha256,
        "request_url_redacted": redact_url(final_url or build_url(
            planned.request.base_url,
            planned.request.params,
            "REDACTED" if planned.mode.send_crumb else None,
        )),
        "expected_top_level": planned.request.expected_top_level,
        "expected_top_level_found": expected_top_level_found,
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
        "crumb_sent": planned.mode.send_crumb,
        "sensitive_values_persisted": False,
    }
    return body, metadata


def comparison_row(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        column: metadata.get(column, "")
        for column in MODE_RESULTS_COLUMNS
    }


def build_endpoint_summaries(
    request_definitions: list[RequestDefinition],
    metadata_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_key = {
        (row["endpoint_id"], row["session_mode"]): row
        for row in metadata_rows
    }
    summaries: list[dict[str, Any]] = []

    for request in request_definitions:
        mode_rows = {
            mode: by_key[(request.endpoint_id, mode)]
            for mode in ("cookie-crumb", "cookie-only", "no-session")
        }
        statuses = [row["http_status"] for row in mode_rows.values()]
        classifications = [row["result_classification"] for row in mode_rows.values()]
        hashes = [row["raw_response_sha256"] for row in mode_rows.values()]
        canonical_hashes = [row.get("canonical_json_sha256") for row in mode_rows.values()]
        sizes = [row["response_bytes"] for row in mode_rows.values()]
        expected_flags = [bool(row["expected_top_level_found"]) for row in mode_rows.values()]
        successful_mode_count = sum(expected_flags)

        note = ""
        if successful_mode_count == 3:
            note = "All three modes returned the expected top-level object."
        elif mode_rows["cookie-crumb"]["expected_top_level_found"] and successful_mode_count == 1:
            note = "Only cookie-plus-crumb returned the expected top-level object."
        elif successful_mode_count == 0:
            note = "No mode returned the expected top-level object; review response evidence."
        else:
            note = "Mixed session-mode result; compare metadata and raw responses."

        summaries.append(
            {
                "endpoint_id": request.endpoint_id,
                "request_id": request.request_id,
                "cookie_crumb_http_status": mode_rows["cookie-crumb"]["http_status"],
                "cookie_crumb_classification": mode_rows["cookie-crumb"]["result_classification"],
                "cookie_only_http_status": mode_rows["cookie-only"]["http_status"],
                "cookie_only_classification": mode_rows["cookie-only"]["result_classification"],
                "no_session_http_status": mode_rows["no-session"]["http_status"],
                "no_session_classification": mode_rows["no-session"]["result_classification"],
                "successful_mode_count": successful_mode_count,
                "all_modes_received_http_response": all(status is not None for status in statuses),
                "all_modes_expected_top_level": all(expected_flags),
                "response_hashes_equal": len(set(hashes)) == 1,
                "canonical_json_hashes_equal": (
                    all(canonical_hashes) and len(set(canonical_hashes)) == 1
                ),
                "response_sizes_equal": len(set(sizes)) == 1,
                "review_note": note,
            }
        )
    return summaries


def portable_source_path(definition_path: Path) -> str:
    resolved = definition_path.expanduser().resolve()
    try:
        return resolved.relative_to(REPOSITORY_ROOT.resolve()).as_posix()
    except ValueError:
        return resolved.name


def build_resolved_study_definition(
    definition: dict[str, Any],
    modes: list[ModeDefinition],
    requests: list[RequestDefinition],
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
        "resolved_at_utc": run_started_at_utc,
        "request_order": "endpoint-major, then configured session-mode order",
        "mode_order": [mode.session_mode for mode in modes],
        "modes": [asdict(mode) for mode in modes],
        "requests": [asdict(request) for request in requests],
        "sensitive_values_persisted": False,
    }


def run_study(
    *,
    definition_path: Path,
    output_parent: Path,
    timeout: float,
    maximum_attempts: int,
    pause_ms: int,
    session_factory: Callable[[ModeDefinition, float], PreparedYahooSession | NoPreparedSession] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = utc_now,
    clock: Callable[[], float] = time.perf_counter,
) -> tuple[Path, dict[str, Any]]:
    run_started = now()
    definition, modes, requests = load_study_definition(definition_path, now=run_started)
    run_id = f"{filename_utc(run_started)}_study-01-session-modes"
    run_dir = output_parent / run_id

    if run_dir.exists():
        raise StudyError(f"Run directory already exists: {run_dir}")

    for relative in ("raw", "metadata", "errors", "comparison"):
        (run_dir / relative).mkdir(parents=True, exist_ok=True)

    resolved_definition = build_resolved_study_definition(
        definition,
        modes,
        requests,
        run_started_at_utc=format_utc(run_started),
    )
    resolved_definition_relative = "study-definition.resolved.json"
    resolved_definition_path = run_dir / resolved_definition_relative
    write_json(resolved_definition_path, resolved_definition)
    resolved_definition_bytes = resolved_definition_path.read_bytes()

    plan = build_execution_plan(definition, modes, requests, run_id=run_id)

    if session_factory is None:
        def session_factory(mode: ModeDefinition, timeout_value: float):
            if mode.prepare_cookie:
                return PreparedYahooSession(timeout=timeout_value)
            return NoPreparedSession(timeout=timeout_value)

    sessions = {
        mode.session_mode: session_factory(mode, timeout)
        for mode in modes
    }

    metadata_rows: list[dict[str, Any]] = []
    for index, planned in enumerate(plan):
        print(
            f"[{planned.sequence:02d}/{len(plan)}] "
            f"{planned.request.endpoint_id} / {planned.mode.session_mode} ... ",
            end="",
            flush=True,
        )
        body, metadata = capture_one(
            planned,
            sessions[planned.mode.session_mode],
            maximum_attempts=maximum_attempts,
            sleep=sleep,
            now=now,
            clock=clock,
        )

        mode_dir = planned.mode.session_mode
        raw_relative = f"raw/{mode_dir}/{planned.request.endpoint_id}.raw.json"
        metadata_relative = f"metadata/{mode_dir}/{planned.request.endpoint_id}.meta.json"
        error_relative = f"errors/{mode_dir}/{planned.request.endpoint_id}.error.txt"

        raw_path = run_dir / raw_relative
        metadata_path = run_dir / metadata_relative
        error_path = run_dir / error_relative
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        error_path.parent.mkdir(parents=True, exist_ok=True)

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

    endpoint_summaries = build_endpoint_summaries(requests, metadata_rows)
    mode_results_path = run_dir / "comparison" / "session-mode-results.csv"
    endpoint_summary_path = run_dir / "comparison" / "endpoint-session-summary.csv"
    write_csv(mode_results_path, MODE_RESULTS_COLUMNS, map(comparison_row, metadata_rows))
    write_csv(endpoint_summary_path, ENDPOINT_SUMMARY_COLUMNS, endpoint_summaries)

    run_completed = now()
    http_response_count = sum(row["http_status"] is not None for row in metadata_rows)
    expected_count = sum(bool(row["expected_top_level_found"]) for row in metadata_rows)
    definition_bytes = definition_path.read_bytes()

    manifest_requests = []
    for metadata in metadata_rows:
        metadata_path = run_dir / metadata["metadata_file"]
        sidecar = json.loads(metadata_path.read_text(encoding="utf-8"))
        if sidecar != metadata:
            raise StudyError(f"Metadata write verification failed: {metadata_path}")
        manifest_requests.append(metadata)

    manifest = {
        "study_schema_version": STUDY_SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "run_id": run_id,
        "study_id": definition["study_id"],
        "study_version": definition["study_version"],
        "study_title": definition["study_title"],
        "study_variable": definition["study_variable"],
        "study_definition_file": resolved_definition_relative,
        "study_definition_sha256": sha256_bytes(resolved_definition_bytes),
        "study_definition_source_file": portable_source_path(definition_path),
        "study_definition_source_sha256": sha256_bytes(definition_bytes),
        "run_started_at_utc": format_utc(run_started),
        "run_completed_at_utc": format_utc(run_completed),
        "default_pause_ms": pause_ms,
        "timeout_seconds": timeout,
        "maximum_attempts": maximum_attempts,
        "request_order": "endpoint-major, then configured session-mode order",
        "session_modes": [asdict(mode) for mode in modes],
        "authentication": {
            mode.session_mode: sessions[mode.session_mode].public_summary()
            for mode in modes
        },
        "comparison_files": {
            "session_mode_results": "comparison/session-mode-results.csv",
            "endpoint_session_summary": "comparison/endpoint-session-summary.csv",
        },
        "requests": manifest_requests,
        "summary": {
            "planned_request_count": len(plan),
            "evidence_record_count": len(metadata_rows),
            "http_response_count": http_response_count,
            "no_http_response_count": len(metadata_rows) - http_response_count,
            "expected_top_level_found_count": expected_count,
            "expected_top_level_missing_count": len(metadata_rows) - expected_count,
            "endpoint_count": len(requests),
            "session_mode_count": len(modes),
            "all_evidence_records_written": len(metadata_rows) == len(plan),
            "sensitive_values_persisted": False,
        },
    }
    write_json(run_dir / "run-manifest.json", manifest)
    return run_dir, manifest


def print_dry_run(
    definition_path: Path,
    *,
    now: datetime | None = None,
) -> None:
    run_time = now or utc_now()
    definition, modes, requests = load_study_definition(definition_path, now=run_time)
    run_id = f"{filename_utc(run_time)}_study-01-session-modes"
    plan = build_execution_plan(definition, modes, requests, run_id=run_id)

    print("Study 01 session-mode dry run")
    print(f"Study: {definition['study_id']} v{definition['study_version']}")
    print(f"Planned requests: {len(plan)}")
    for planned in plan:
        crumb = "REDACTED" if planned.mode.send_crumb else None
        url = build_url(planned.request.base_url, planned.request.params, crumb)
        print(
            f"[{planned.sequence:02d}/{len(plan)}] "
            f"{planned.request.endpoint_id} / {planned.mode.session_mode}"
        )
        print(f"  {redact_url(url)}")
        print(f"  expected top level: {planned.request.expected_top_level}")
        print(f"  parameter fingerprint: {planned.request_parameters_sha256}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the controlled seven-endpoint, three-session-mode Study 01."
    )
    parser.add_argument(
        "--definition",
        type=Path,
        default=DEFAULT_CONFIG,
        help="Study definition JSON. Default: config/studies/study-01-session-modes.json",
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
    print(f"Expected top levels found: {summary['expected_top_level_found_count']}")
    print("Resolved definition: study-definition.resolved.json")
    print("Session-mode results: comparison\\session-mode-results.csv")
    print("Endpoint summary: comparison\\endpoint-session-summary.csv")

    return 0 if summary["no_http_response_count"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
