#!/usr/bin/env python3
r"""Capture one live verification response from seven Yahoo Finance endpoint families.

This is a pre-implementation verification tool, not the production v0.5.0
capture application. It uses only the Python standard library.

Run from repository root:

    py tools\endpoint-verification\verify_seven_endpoints.py

Dry run without contacting Yahoo:

    py tools\endpoint-verification\verify_seven_endpoints.py --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener

SCRIPT_VERSION = "0.1.0"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/150.0.0.0 Safari/537.36"
)
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTDIR = REPOSITORY_ROOT / "captures" / "local"
COOKIE_URLS = (
    ("basic-query1", "https://fc.yahoo.com", "https://query1.finance.yahoo.com/v1/test/getcrumb"),
    ("finance-query2-fallback", "https://finance.yahoo.com/quote/AAPL", "https://query2.finance.yahoo.com/v1/test/getcrumb"),
)
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
AUTH_REFRESH_STATUSES = {401, 403}
SENSITIVE_QUERY_KEYS = {"crumb", "cookie", "authorization", "token", "auth", "session"}


@dataclass(frozen=True)
class EndpointRequest:
    endpoint_id: str
    method: str
    base_url: str
    params: dict[str, str]
    expected_top_level: str


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


def redact_url(url: str) -> str:
    parts = urlsplit(url)
    query = []
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


class AnonymousYahooSession:
    def __init__(self, timeout: float):
        self.timeout = timeout
        self.cookie_jar = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookie_jar))
        self.crumb: str | None = None
        self.strategy: str | None = None
        self.refresh_count = 0

    def _open(self, request: Request):
        return self.opener.open(request, timeout=self.timeout)

    def _prime_cookie(self, url: str) -> str | None:
        try:
            with self._open(make_request(url, "text/html,application/xhtml+xml,*/*")) as response:
                response.read()
            return None
        except HTTPError as exc:
            try:
                exc.read()
            except Exception:
                pass
            if exc.code == 429:
                return "cookie bootstrap returned HTTP 429"
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
        request = make_request(url, "text/plain,*/*")
        with self._open(request) as response:
            body = response.read()
            status = int(getattr(response, "status", response.getcode()))
        if not 200 <= status < 300:
            raise RuntimeError(f"crumb endpoint returned HTTP {status}")
        crumb = self._valid_crumb(body.decode("utf-8", errors="replace"))
        if crumb is None:
            raise RuntimeError("crumb endpoint returned an invalid or empty value")
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

        errors = []
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

        raise RuntimeError("Could not establish anonymous Yahoo session: " + "; ".join(errors))

    def public_summary(self) -> dict[str, Any]:
        return {
            "mode": "anonymous-cookie-crumb",
            "strategy": self.strategy,
            "cookie_count": sum(1 for _ in self.cookie_jar),
            "crumb_present": self.crumb is not None,
            "refresh_count": self.refresh_count,
            "sensitive_values_persisted": False,
        }


def endpoint_requests(now: datetime) -> list[EndpointRequest]:
    period2 = int(now.timestamp())
    period1 = int((now - timedelta(days=730)).timestamp())

    return [
        EndpointRequest(
            "quote",
            "GET",
            "https://query1.finance.yahoo.com/v7/finance/quote",
            {"symbols": "AAPL", "formatted": "false", "lang": "en-US", "region": "US"},
            "quoteResponse",
        ),
        EndpointRequest(
            "chart",
            "GET",
            "https://query2.finance.yahoo.com/v8/finance/chart/AAPL",
            {
                "range": "5d",
                "interval": "1d",
                "includePrePost": "false",
                "events": "div,splits,capitalGains",
            },
            "chart",
        ),
        EndpointRequest(
            "quote-summary",
            "GET",
            "https://query2.finance.yahoo.com/v10/finance/quoteSummary/AAPL",
            {
                "modules": "price,summaryDetail",
                "formatted": "false",
                "corsDomain": "finance.yahoo.com",
                "lang": "en-US",
                "region": "US",
                "symbol": "AAPL",
            },
            "quoteSummary",
        ),
        EndpointRequest(
            "search",
            "GET",
            "https://query2.finance.yahoo.com/v1/finance/search",
            {
                "q": "AAPL",
                "quotesCount": "5",
                "enableFuzzyQuery": "false",
                "newsCount": "0",
                "quotesQueryId": "tss_match_phrase_query",
                "newsQueryId": "news_cie_vespa",
                "listsCount": "0",
                "enableCb": "true",
                "enableNavLinks": "false",
                "enableResearchReports": "false",
                "enableCulturalAssets": "false",
                "recommendedCount": "0",
            },
            "quotes",
        ),
        EndpointRequest(
            "screener",
            "GET",
            "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved",
            {
                "scrIds": "day_gainers",
                "count": "5",
                "offset": "0",
                "corsDomain": "finance.yahoo.com",
                "formatted": "false",
                "lang": "en-US",
                "region": "US",
            },
            "finance",
        ),
        EndpointRequest(
            "fundamentals-timeseries",
            "GET",
            "https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/AAPL",
            {
                "symbol": "AAPL",
                "type": "quarterlyMarketCap",
                "period1": str(period1),
                "period2": str(period2),
            },
            "timeseries",
        ),
        EndpointRequest(
            "options",
            "GET",
            "https://query2.finance.yahoo.com/v7/finance/options/AAPL",
            {},
            "optionChain",
        ),
    ]


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
    spec: EndpointRequest,
    session: AnonymousYahooSession,
    *,
    timeout: float,
    maximum_attempts: int,
) -> tuple[bytes, dict[str, Any]]:
    attempts: list[Attempt] = []
    auth_refresh_used = False
    body = b""
    status: int | None = None
    content_type = ""
    final_url = ""
    final_error: str | None = None

    refresh_on_next_attempt = False

    for attempt_number in range(1, maximum_attempts + 1):
        crumb = session.prepare(force_refresh=refresh_on_next_attempt)
        refresh_on_next_attempt = False
        url = build_url(spec.base_url, spec.params, crumb)
        requested_at = utc_now()
        started = time.perf_counter()

        try:
            with session._open(make_request(url)) as response:
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

        received_at = utc_now()
        elapsed_ms = round((time.perf_counter() - started) * 1000)
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

        if status in AUTH_REFRESH_STATUSES and not auth_refresh_used:
            auth_refresh_used = True
            refresh_on_next_attempt = True
            final_error = error
            continue

        if status in RETRYABLE_STATUSES and attempt_number < maximum_attempts:
            final_error = error
            time.sleep(2 if attempt_number == 1 else 5)
            continue

        final_error = error
        break

    parse_status = "NOT_JSON"
    result_classification = "HTTP_OR_NETWORK_ERROR"
    expected_top_level_found = False
    parse_error = None

    if body:
        try:
            parsed = json.loads(body)
        except Exception as exc:
            parse_status = "PARSE_ERROR"
            parse_error = f"{type(exc).__name__}: {exc}"
        else:
            parse_status = "VALID_JSON"
            result_classification, expected_top_level_found = classify_json(
                parsed, spec.expected_top_level
            )

    if status is not None and 200 <= status < 300 and parse_status == "PARSE_ERROR":
        result_classification = "HTTP_SUCCESS_JSON_PARSE_ERROR"
    elif status is not None and not 200 <= status < 300:
        result_classification = "HTTP_ERROR"
    elif status is None:
        result_classification = "NETWORK_ERROR"

    metadata = {
        "verification_schema_version": "0.1.0",
        "script_version": SCRIPT_VERSION,
        "endpoint_id": spec.endpoint_id,
        "method": spec.method,
        "expected_top_level": spec.expected_top_level,
        "request_parameters": spec.params,
        "request_url_redacted": redact_url(final_url or build_url(spec.base_url, spec.params, None)),
        "http_status": status,
        "content_type": content_type,
        "response_bytes": len(body),
        "raw_response_sha256": hashlib.sha256(body).hexdigest(),
        "parse_status": parse_status,
        "parse_error": parse_error,
        "expected_top_level_found": expected_top_level_found,
        "result_classification": result_classification,
        "error_message": final_error,
        "auth_refresh_performed": auth_refresh_used,
        "attempt_count": len(attempts),
        "attempts": [asdict(item) for item in attempts],
    }
    return body, metadata


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture one raw response from seven Yahoo Finance endpoint families."
    )
    parser.add_argument("--dry-run", action="store_true", help="Show requests without contacting Yahoo.")
    parser.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-attempts", type=int, default=3)
    args = parser.parse_args()

    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")
    if not 1 <= args.max_attempts <= 5:
        parser.error("--max-attempts must be between 1 and 5")

    specs = endpoint_requests(utc_now())

    if args.dry_run:
        print("Seven-endpoint verification dry run")
        for index, spec in enumerate(specs, 1):
            print(f"[{index}/7] {spec.endpoint_id}")
            print(f"  {redact_url(build_url(spec.base_url, spec.params, None))}")
            print(f"  expected top level: {spec.expected_top_level}")
        return 0

    started_at = utc_now()
    run_id = f"{filename_utc(started_at)}_seven-endpoint-verification"
    run_dir = args.outdir.expanduser().resolve() / run_id
    raw_dir = run_dir / "raw"
    metadata_dir = run_dir / "metadata"
    errors_dir = run_dir / "errors"
    for directory in (raw_dir, metadata_dir, errors_dir):
        directory.mkdir(parents=True, exist_ok=True)

    session = AnonymousYahooSession(timeout=args.timeout)
    manifest: dict[str, Any] = {
        "verification_schema_version": "0.1.0",
        "script_version": SCRIPT_VERSION,
        "run_id": run_id,
        "run_started_at_utc": format_utc(started_at),
        "run_completed_at_utc": None,
        "repository_root": ".",
        "default_pause_ms": 0,
        "timeout_seconds": args.timeout,
        "maximum_attempts": args.max_attempts,
        "authentication": {},
        "requests": [],
        "summary": {},
    }

    manifest_path = run_dir / "verification-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    for index, spec in enumerate(specs, 1):
        print(f"[{index}/7] {spec.endpoint_id} ...", end="", flush=True)
        try:
            body, metadata = capture_one(
                spec,
                session,
                timeout=args.timeout,
                maximum_attempts=args.max_attempts,
            )
        except Exception as exc:
            body = b""
            metadata = {
                "verification_schema_version": "0.1.0",
                "script_version": SCRIPT_VERSION,
                "endpoint_id": spec.endpoint_id,
                "result_classification": "SESSION_OR_OPERATING_ERROR",
                "error_message": f"{type(exc).__name__}: {exc}",
                "response_bytes": 0,
                "raw_response_sha256": hashlib.sha256(b"").hexdigest(),
                "parse_status": "NOT_ATTEMPTED",
                "expected_top_level_found": False,
                "attempt_count": 0,
                "attempts": [],
            }

        raw_name = f"{spec.endpoint_id}.raw.json"
        meta_name = f"{spec.endpoint_id}.meta.json"
        raw_path = raw_dir / raw_name
        meta_path = metadata_dir / meta_name
        raw_path.write_bytes(body)

        metadata.update(
            {
                "sequence": index,
                "raw_response_file": f"raw/{raw_name}",
                "metadata_file": f"metadata/{meta_name}",
            }
        )
        meta_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        manifest["requests"].append(metadata)
        manifest["authentication"] = session.public_summary()
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

        print(
            f" HTTP {metadata.get('http_status')} "
            f"{metadata.get('result_classification')}"
        )

    completed_at = utc_now()
    counts: dict[str, int] = {}
    for item in manifest["requests"]:
        classification = item.get("result_classification", "UNKNOWN")
        counts[classification] = counts.get(classification, 0) + 1

    manifest["run_completed_at_utc"] = format_utc(completed_at)
    manifest["authentication"] = session.public_summary()
    manifest["summary"] = {
        "request_count": len(manifest["requests"]),
        "classification_counts": counts,
        "all_expected_top_levels_found": all(
            bool(item.get("expected_top_level_found")) for item in manifest["requests"]
        ),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"\nVerification folder: {run_dir}")
    return 0 if manifest["summary"]["all_expected_top_levels_found"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        raise SystemExit(130)
