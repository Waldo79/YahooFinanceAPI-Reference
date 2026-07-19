#!/usr/bin/env python3
r"""Convert a validated Yahoo endpoint capture run into deterministic analysis tables.

This is an offline analyzer. It never contacts Yahoo Finance and never modifies
raw evidence or metadata.

Examples, from repository root:

    py tools\endpoint-analysis\analyze_endpoint_captures.py ^
      captures\local\2026-07-19T01-35-54.972Z_seven-endpoint-verification

Analyze the newest local capture run:

    py tools\endpoint-analysis\analyze_endpoint_captures.py

On Windows, run the unit tests with a repository-local temporary directory:

    rmdir /s /q .pytest-temp 2>nul
    py -m pytest -q tests\test_endpoint_analysis.py --basetemp=".pytest-temp"

Generated files are written to the run's ``analysis`` directory unless
``--output-dir`` is supplied.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlsplit

ANALYZER_VERSION = "0.1.1"
ANALYSIS_SCHEMA_VERSION = "0.5.0"

ENDPOINT_ORDER = (
    "quote",
    "chart",
    "quote-summary",
    "search",
    "screener",
    "fundamentals-timeseries",
    "options",
)
ENDPOINT_RANK = {name: index for index, name in enumerate(ENDPOINT_ORDER)}

PRESENT_STATES = {
    "PRESENT_VALUE",
    "PRESENT_ZERO",
    "PRESENT_FALSE",
    "PRESENT_EMPTY_STRING",
    "PRESENT_EMPTY_ARRAY",
    "PRESENT_EMPTY_OBJECT",
    "PRESENT_EXPLICIT_NULL",
    "PRESENT_MIXED",
}
EMPTY_STATES = {
    "PRESENT_EMPTY_STRING",
    "PRESENT_EMPTY_ARRAY",
    "PRESENT_EMPTY_OBJECT",
}
STATE_CODES = {
    "PRESENT_VALUE": "V",
    "PRESENT_ZERO": "0",
    "PRESENT_FALSE": "F",
    "PRESENT_EMPTY_STRING": "S",
    "PRESENT_EMPTY_ARRAY": "A",
    "PRESENT_EMPTY_OBJECT": "O",
    "PRESENT_EXPLICIT_NULL": "N",
    "PRESENT_MIXED": "*",
    "MISSING_EXPECTED_PATH": "M",
    "NOT_EXPECTED_FOR_ENDPOINT_OR_TYPE": "X",
    "NOT_EVALUATED": "?",
}

SIMPLE_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
CRUMB_VALUE_RE = re.compile(r"(?i)(?:[?&]|\\u0026)crumb=([^&\"\'\s]+)")
SENSITIVE_HEADER_RE = re.compile(r"(?i)\b(?:set-cookie|cookie|authorization)\s*:")
YAHOO_COOKIE_RE = re.compile(r"(?i)(?:^|[;\s])(?:A3|T|Y)=")

TIMESTAMP_EXACT_NAMES = {
    "timestamp",
    "firstTradeDate",
    "regularMarketTime",
    "postMarketTime",
    "preMarketTime",
    "lastTradeDate",
    "expirationDate",
    "earningsTimestamp",
    "earningsTimestampStart",
    "earningsTimestampEnd",
    "dividendDate",
    "exDividendDate",
    "epochGradeDate",
    "start",
    "end",
}
TIMESTAMP_EXCLUDED_NAMES = {
    "gmtOffset",
    "maxAge",
    "age",
    "duration",
    "timeZoneShortName",
}

SAMPLES_COLUMNS = [
    "sample_id",
    "run_id",
    "sequence",
    "endpoint_id",
    "request_id",
    "request_subject",
    "requested_symbols_json",
    "returned_symbols_json",
    "project_security_type",
    "expected_exchange",
    "market_state_target",
    "requested_at_utc",
    "response_received_at_utc",
    "elapsed_ms",
    "http_status",
    "content_type",
    "response_bytes",
    "raw_response_sha256",
    "result_classification",
    "parse_status",
    "raw_response_file",
    "metadata_file",
    "request_parameters_json",
    "request_url_redacted",
]

FIELDS_COLUMNS = [
    "field_order",
    "run_id",
    "sample_id",
    "request_id",
    "endpoint_id",
    "request_subject",
    "returned_entity",
    "evidence_json_path",
    "comparison_json_path",
    "field_name",
    "array_identity",
    "json_type",
    "presence_state",
    "raw_value_json",
    "raw_value_text",
    "decoded_utc",
    "master_field_status",
    "notes",
]

OCCURRENCE_COLUMNS = [
    "sample_id",
    "endpoint_id",
    "comparison_json_path",
    "field_name",
    "presence_state",
    "presence_states",
    "json_types",
    "observed_count",
    "value_fingerprint_sha256",
]

CATALOG_COLUMNS = [
    "endpoint_id",
    "comparison_json_path",
    "field_name",
    "observed_json_types",
    "interpreted_type",
    "units",
    "working_definition",
    "first_seen_utc",
    "last_seen_utc",
    "sample_count",
    "present_count",
    "null_count",
    "empty_count",
    "observed_value_count",
    "project_security_types_seen",
    "exchanges_seen",
    "market_states_seen",
    "review_status",
    "confidence_level",
    "notes",
]

TYPE_CONFLICT_COLUMNS = [
    "endpoint_id",
    "comparison_json_path",
    "field_name",
    "observed_json_types",
    "sample_ids_by_type_json",
]


class AnalysisError(RuntimeError):
    """Raised when source evidence is incomplete or internally inconsistent."""


@dataclass
class Sample:
    sample_id: str
    run_id: str
    sequence: int
    endpoint_id: str
    request_id: str
    request_subject: str
    metadata: dict[str, Any]
    raw_bytes: bytes
    parsed_json: Any
    response_received_at_utc: str


@dataclass(frozen=True)
class Identity:
    label: str
    value: str

    def render(self) -> str:
        return f"{self.label}:{self.value}"


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=False)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_csv(path: Path, columns: list[str], rows: Iterable[dict[str, Any]]) -> int:
    count = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="raise")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})
            count += 1
    return count


def endpoint_sort_key(endpoint_id: str) -> tuple[int, str]:
    return ENDPOINT_RANK.get(endpoint_id, len(ENDPOINT_ORDER)), endpoint_id


def resolve_evidence_path(run_dir: Path, relative_name: str, label: str) -> Path:
    candidate = Path(relative_name)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise AnalysisError(f"{label} must be a safe run-relative path: {relative_name!r}")
    resolved = (run_dir / candidate).resolve()
    try:
        resolved.relative_to(run_dir.resolve())
    except ValueError as exc:
        raise AnalysisError(f"{label} escapes the run directory: {relative_name!r}") from exc
    return resolved


def locate_manifest(run_dir: Path) -> Path:
    candidates = [
        run_dir / "verification-manifest.json",
        run_dir / "run-manifest.json",
    ]
    found = [path for path in candidates if path.is_file()]
    if len(found) != 1:
        names = ", ".join(path.name for path in found) or "none"
        raise AnalysisError(
            "Run directory must contain exactly one supported manifest "
            f"(verification-manifest.json or run-manifest.json); found {names}."
        )
    return found[0]


def find_latest_run(captures_root: Path) -> Path:
    if not captures_root.is_dir():
        raise AnalysisError(f"Capture root does not exist: {captures_root}")
    candidates = []
    for child in captures_root.iterdir():
        if not child.is_dir():
            continue
        if (child / "verification-manifest.json").is_file() or (child / "run-manifest.json").is_file():
            candidates.append(child)
    if not candidates:
        raise AnalysisError(f"No capture run with a supported manifest was found under {captures_root}")
    return sorted(candidates, key=lambda path: path.name)[-1]


def json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    raise AnalysisError(f"Unsupported parsed JSON value type: {type(value).__name__}")


def presence_state(value: Any) -> str:
    if value is None:
        return "PRESENT_EXPLICIT_NULL"
    if value is False:
        return "PRESENT_FALSE"
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0:
        return "PRESENT_ZERO"
    if value == "":
        return "PRESENT_EMPTY_STRING"
    if value == []:
        return "PRESENT_EMPTY_ARRAY"
    if value == {}:
        return "PRESENT_EMPTY_OBJECT"
    return "PRESENT_VALUE"


def render_key(parent: str, key: str) -> str:
    if SIMPLE_KEY_RE.fullmatch(key):
        return f"{parent}.{key}" if parent else key
    encoded = json.dumps(key, ensure_ascii=False)
    return f"{parent}[{encoded}]" if parent else f"[{encoded}]"


def spreadsheet_safe_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        if value.startswith(("=", "+", "-", "@")):
            return "'" + value
        return value
    if isinstance(value, int):
        text = str(value)
        return "'" + text if len(text.lstrip("-")) > 15 else text
    if isinstance(value, float):
        return repr(value)
    return compact_json(value)


def decode_timestamp(field_name: str, value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ""
    if field_name in TIMESTAMP_EXCLUDED_NAMES:
        return ""
    lower = field_name.lower()
    likely = (
        field_name in TIMESTAMP_EXACT_NAMES
        or "timestamp" in lower
        or lower.endswith("markettime")
        or lower.endswith("tradedate")
        or lower.endswith("expirationdate")
    )
    if not likely:
        return ""

    seconds = float(value)
    if 10_000_000_000 <= abs(seconds) <= 4_102_444_800_000:
        seconds /= 1000.0
    if not 0 <= seconds <= 4_102_444_800:
        return ""
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError):
        return ""


def identity_value(value: Any) -> str:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return spreadsheet_safe_text(value)
    return compact_json(value)


def identity_for_item(
    endpoint_id: str,
    comparison_item_path: str,
    item: Any,
) -> Identity | None:
    if not isinstance(item, dict):
        return None

    if endpoint_id == "options":
        if ".calls[]" in comparison_item_path or ".puts[]" in comparison_item_path:
            if item.get("contractSymbol") is not None:
                return Identity("contractSymbol", identity_value(item["contractSymbol"]))
        if comparison_item_path.endswith(".options[]") and item.get("expirationDate") is not None:
            return Identity("expirationDate", identity_value(item["expirationDate"]))
        if comparison_item_path.endswith(".result[]"):
            quote = item.get("quote")
            if isinstance(quote, dict) and quote.get("symbol") is not None:
                return Identity("symbol", identity_value(quote["symbol"]))

    if endpoint_id == "fundamentals-timeseries":
        if comparison_item_path.endswith(".result[]"):
            meta = item.get("meta")
            if isinstance(meta, dict) and meta.get("type") is not None:
                return Identity("metric", identity_value(meta["type"]))
            if item.get("type") is not None:
                return Identity("metric", identity_value(item["type"]))
        if item.get("asOfDate") is not None:
            return Identity("asOfDate", identity_value(item["asOfDate"]))

    if endpoint_id == "chart" and comparison_item_path.endswith(".result[]"):
        meta = item.get("meta")
        if isinstance(meta, dict) and meta.get("symbol") is not None:
            return Identity("symbol", identity_value(meta["symbol"]))

    for key in (
        "symbol",
        "contractSymbol",
        "uuid",
        "id",
        "ticker",
        "asOfDate",
        "date",
        "title",
        "name",
        "shortname",
        "longname",
    ):
        if key in item and item[key] not in (None, ""):
            return Identity(key, identity_value(item[key]))
    return None


def flatten_json(
    value: Any,
    *,
    endpoint_id: str,
    evidence_path: str = "",
    comparison_path: str = "",
    field_name: str = "$",
    identities: tuple[Identity, ...] = (),
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []

    if isinstance(value, dict) and value:
        for key, child in value.items():
            child_evidence = render_key(evidence_path, str(key))
            child_comparison = render_key(comparison_path, str(key))
            rows.extend(
                flatten_json(
                    child,
                    endpoint_id=endpoint_id,
                    evidence_path=child_evidence,
                    comparison_path=child_comparison,
                    field_name=str(key),
                    identities=identities,
                )
            )
        return rows

    if isinstance(value, list) and value:
        for index, child in enumerate(value):
            child_evidence = f"{evidence_path}[{index}]" if evidence_path else f"[{index}]"
            child_comparison = f"{comparison_path}[]" if comparison_path else "[]"
            identity = identity_for_item(endpoint_id, child_comparison, child)
            child_identities = identities + ((identity,) if identity else ())
            rows.extend(
                flatten_json(
                    child,
                    endpoint_id=endpoint_id,
                    evidence_path=child_evidence,
                    comparison_path=child_comparison,
                    field_name=field_name,
                    identities=child_identities,
                )
            )
        return rows

    evidence = evidence_path or "$"
    comparison = comparison_path or "$"
    value_json = compact_json(value)
    last_identity = identities[-1] if identities else None

    rows.append(
        {
            "returned_entity": last_identity.value if last_identity else "",
            "evidence_json_path": evidence,
            "comparison_json_path": comparison,
            "field_name": field_name,
            "array_identity": "|".join(identity.render() for identity in identities),
            "json_type": json_type(value),
            "presence_state": presence_state(value),
            "raw_value_json": value_json,
            "raw_value_text": spreadsheet_safe_text(value),
            "decoded_utc": decode_timestamp(field_name, value),
            "master_field_status": "UNREVIEWED",
            "notes": "",
        }
    )
    return rows


def request_subject(metadata: dict[str, Any]) -> str:
    params = metadata.get("request_parameters") or metadata.get("request_parameters_canonical") or {}
    if isinstance(params, dict):
        for key in ("symbols", "symbol", "q", "scrIds", "query"):
            value = params.get(key)
            if value not in (None, ""):
                return str(value)

    subject = metadata.get("request_subject") or metadata.get("requested_symbol")
    if subject not in (None, ""):
        return str(subject)

    redacted_url = metadata.get("request_url_redacted") or ""
    if redacted_url:
        split = urlsplit(redacted_url)
        query = dict(parse_qsl(split.query))
        for key in ("symbols", "symbol", "q", "scrIds"):
            if query.get(key):
                return query[key]
        if split.path:
            final = split.path.rstrip("/").rsplit("/", 1)[-1]
            if final and final not in {"quote", "search", "saved", "timeseries"}:
                return final
    return ""


def final_attempt_time(metadata: dict[str, Any]) -> str:
    if metadata.get("response_received_at_utc"):
        return str(metadata["response_received_at_utc"])
    attempts = metadata.get("attempts") or []
    if attempts and isinstance(attempts[-1], dict):
        return str(attempts[-1].get("response_received_at_utc") or "")
    return ""


def first_attempt_time(metadata: dict[str, Any]) -> str:
    if metadata.get("requested_at_utc"):
        return str(metadata["requested_at_utc"])
    attempts = metadata.get("attempts") or []
    if attempts and isinstance(attempts[0], dict):
        return str(attempts[0].get("requested_at_utc") or "")
    return ""


def final_elapsed_ms(metadata: dict[str, Any]) -> Any:
    if metadata.get("elapsed_ms") is not None:
        return metadata["elapsed_ms"]
    attempts = metadata.get("attempts") or []
    if attempts and isinstance(attempts[-1], dict):
        return attempts[-1].get("elapsed_ms", "")
    return ""


def scan_sensitive_text(label: str, text: str, errors: list[str]) -> None:
    for match in CRUMB_VALUE_RE.finditer(text):
        value = match.group(1).rstrip("\\")
        if value.upper() != "REDACTED":
            errors.append(f"Potential unredacted crumb found in {label}")
            break
    if SENSITIVE_HEADER_RE.search(text):
        errors.append(f"Potential sensitive HTTP header found in {label}")
    if YAHOO_COOKIE_RE.search(text):
        errors.append(f"Potential Yahoo cookie value found in {label}")


def load_samples(run_dir: Path, manifest_path: Path) -> tuple[dict[str, Any], list[Sample], list[str]]:
    manifest_bytes = manifest_path.read_bytes()
    try:
        manifest = json.loads(manifest_bytes)
    except json.JSONDecodeError as exc:
        raise AnalysisError(f"Manifest is not valid JSON: {exc}") from exc

    requests = manifest.get("requests")
    if not isinstance(requests, list) or not requests:
        raise AnalysisError("Manifest must contain a nonempty requests array.")

    run_id = str(manifest.get("run_id") or run_dir.name)
    errors: list[str] = []
    samples: list[Sample] = []
    seen_sample_ids: set[str] = set()

    scan_sensitive_text(manifest_path.name, manifest_bytes.decode("utf-8", errors="replace"), errors)

    for default_sequence, request_entry in enumerate(requests, 1):
        if not isinstance(request_entry, dict):
            errors.append(f"Manifest request {default_sequence} is not an object")
            continue

        sequence = int(request_entry.get("sequence") or default_sequence)
        endpoint_id = str(request_entry.get("endpoint_id") or "")
        if not endpoint_id:
            errors.append(f"Manifest request {sequence} has no endpoint_id")
            continue

        metadata_name = request_entry.get("metadata_file")
        raw_name = request_entry.get("raw_response_file")
        if not metadata_name or not raw_name:
            errors.append(f"{endpoint_id}: metadata_file or raw_response_file is missing")
            continue

        metadata_path = resolve_evidence_path(run_dir, str(metadata_name), "metadata_file")
        raw_path = resolve_evidence_path(run_dir, str(raw_name), "raw_response_file")
        if not metadata_path.is_file():
            errors.append(f"{endpoint_id}: metadata file is missing: {metadata_name}")
            continue
        if not raw_path.is_file():
            errors.append(f"{endpoint_id}: raw response is missing: {raw_name}")
            continue

        metadata_bytes = metadata_path.read_bytes()
        raw_bytes = raw_path.read_bytes()
        try:
            metadata = json.loads(metadata_bytes)
        except json.JSONDecodeError as exc:
            errors.append(f"{endpoint_id}: metadata is invalid JSON: {exc}")
            continue

        scan_sensitive_text(
            str(metadata_name),
            metadata_bytes.decode("utf-8", errors="replace"),
            errors,
        )

        if request_entry != metadata:
            errors.append(f"{endpoint_id}: manifest request entry differs from metadata sidecar")

        expected_bytes = metadata.get("response_bytes")
        if expected_bytes is not None and int(expected_bytes) != len(raw_bytes):
            errors.append(
                f"{endpoint_id}: response byte count mismatch "
                f"(metadata {expected_bytes}, actual {len(raw_bytes)})"
            )

        expected_hash = metadata.get("raw_response_sha256")
        actual_hash = sha256_bytes(raw_bytes)
        if expected_hash and str(expected_hash).lower() != actual_hash:
            errors.append(f"{endpoint_id}: raw response SHA-256 mismatch")

        try:
            parsed = json.loads(raw_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            errors.append(f"{endpoint_id}: raw response is not valid JSON: {exc}")
            continue

        sample_id = str(
            metadata.get("sample_id")
            or f"{run_id}_{sequence:06d}_{endpoint_id}"
        )
        if sample_id in seen_sample_ids:
            errors.append(f"Duplicate sample_id: {sample_id}")
            continue
        seen_sample_ids.add(sample_id)

        samples.append(
            Sample(
                sample_id=sample_id,
                run_id=run_id,
                sequence=sequence,
                endpoint_id=endpoint_id,
                request_id=str(metadata.get("request_id") or endpoint_id),
                request_subject=request_subject(metadata),
                metadata=metadata,
                raw_bytes=raw_bytes,
                parsed_json=parsed,
                response_received_at_utc=final_attempt_time(metadata),
            )
        )

    if errors:
        raise AnalysisError("Source evidence validation failed:\n- " + "\n- ".join(errors))

    samples.sort(key=lambda sample: (sample.sequence, endpoint_sort_key(sample.endpoint_id)))
    return manifest, samples, []


def sample_row(sample: Sample) -> dict[str, Any]:
    metadata = sample.metadata
    params = metadata.get("request_parameters") or metadata.get("request_parameters_canonical") or {}
    return {
        "sample_id": sample.sample_id,
        "run_id": sample.run_id,
        "sequence": sample.sequence,
        "endpoint_id": sample.endpoint_id,
        "request_id": sample.request_id,
        "request_subject": sample.request_subject,
        "requested_symbols_json": canonical_json(metadata.get("requested_symbols") or []),
        "returned_symbols_json": canonical_json(metadata.get("returned_symbols") or []),
        "project_security_type": metadata.get("project_security_type") or "",
        "expected_exchange": metadata.get("expected_exchange") or "",
        "market_state_target": metadata.get("market_state_target") or "",
        "requested_at_utc": first_attempt_time(metadata),
        "response_received_at_utc": sample.response_received_at_utc,
        "elapsed_ms": final_elapsed_ms(metadata),
        "http_status": metadata.get("http_status", ""),
        "content_type": metadata.get("content_type") or "",
        "response_bytes": len(sample.raw_bytes),
        "raw_response_sha256": sha256_bytes(sample.raw_bytes),
        "result_classification": metadata.get("result_classification") or "",
        "parse_status": metadata.get("parse_status") or metadata.get("json_parse_status") or "",
        "raw_response_file": metadata.get("raw_response_file") or "",
        "metadata_file": metadata.get("metadata_file") or "",
        "request_parameters_json": canonical_json(params),
        "request_url_redacted": metadata.get("request_url_redacted") or "",
    }


def build_field_rows(samples: list[Sample]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    field_order = 0
    for sample in samples:
        flattened = flatten_json(sample.parsed_json, endpoint_id=sample.endpoint_id)
        for item in flattened:
            field_order += 1
            row = {
                "field_order": field_order,
                "run_id": sample.run_id,
                "sample_id": sample.sample_id,
                "request_id": sample.request_id,
                "endpoint_id": sample.endpoint_id,
                "request_subject": sample.request_subject,
                **item,
            }
            rows.append(row)
    return rows


def aggregate_presence(states: set[str]) -> str:
    if not states:
        return "MISSING_EXPECTED_PATH"
    if len(states) == 1:
        return next(iter(states))
    return "PRESENT_MIXED"


def fingerprint_field_values(rows: list[dict[str, Any]]) -> str:
    material = [
        {
            "evidence_json_path": row["evidence_json_path"],
            "json_type": row["json_type"],
            "presence_state": row["presence_state"],
            "raw_value_json": row["raw_value_json"],
        }
        for row in sorted(rows, key=lambda row: row["evidence_json_path"])
    ]
    return sha256_bytes(canonical_json(material).encode("utf-8"))


def build_occurrences(
    samples: list[Sample],
    field_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    by_sample_path: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    endpoint_paths: dict[str, set[str]] = defaultdict(set)
    field_names: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)

    for row in field_rows:
        key = (row["sample_id"], row["comparison_json_path"])
        by_sample_path[key].append(row)
        endpoint_paths[row["endpoint_id"]].add(row["comparison_json_path"])
        field_names[(row["endpoint_id"], row["comparison_json_path"])][row["field_name"]] += 1

    samples_by_endpoint: dict[str, list[Sample]] = defaultdict(list)
    for sample in samples:
        samples_by_endpoint[sample.endpoint_id].append(sample)

    occurrence_rows: list[dict[str, Any]] = []
    occurrence_lookup: dict[tuple[str, str], dict[str, Any]] = {}

    for endpoint_id in sorted(endpoint_paths, key=endpoint_sort_key):
        for comparison_path in sorted(endpoint_paths[endpoint_id]):
            field_name = field_names[(endpoint_id, comparison_path)].most_common(1)[0][0]
            for sample in samples_by_endpoint[endpoint_id]:
                observed = by_sample_path.get((sample.sample_id, comparison_path), [])
                if observed:
                    states = {row["presence_state"] for row in observed}
                    types = {row["json_type"] for row in observed}
                    aggregate = aggregate_presence(states)
                    fingerprint = fingerprint_field_values(observed)
                else:
                    states = {"MISSING_EXPECTED_PATH"}
                    types = set()
                    aggregate = "MISSING_EXPECTED_PATH"
                    fingerprint = ""

                row = {
                    "sample_id": sample.sample_id,
                    "endpoint_id": endpoint_id,
                    "comparison_json_path": comparison_path,
                    "field_name": field_name,
                    "presence_state": aggregate,
                    "presence_states": ";".join(sorted(states)),
                    "json_types": ";".join(sorted(types)),
                    "observed_count": len(observed),
                    "value_fingerprint_sha256": fingerprint,
                }
                occurrence_rows.append(row)
                occurrence_lookup[(sample.sample_id, comparison_path)] = row

    sample_ids = [sample.sample_id for sample in samples]
    matrix_rows: list[dict[str, Any]] = []
    sample_endpoint = {sample.sample_id: sample.endpoint_id for sample in samples}

    for endpoint_id in sorted(endpoint_paths, key=endpoint_sort_key):
        for comparison_path in sorted(endpoint_paths[endpoint_id]):
            row: dict[str, Any] = {
                "endpoint_id": endpoint_id,
                "comparison_json_path": comparison_path,
                "field_name": field_names[(endpoint_id, comparison_path)].most_common(1)[0][0],
            }
            for sample_id in sample_ids:
                if sample_endpoint[sample_id] != endpoint_id:
                    row[sample_id] = STATE_CODES["NOT_EXPECTED_FOR_ENDPOINT_OR_TYPE"]
                else:
                    occurrence = occurrence_lookup[(sample_id, comparison_path)]
                    row[sample_id] = STATE_CODES[occurrence["presence_state"]]
            matrix_rows.append(row)

    return occurrence_rows, matrix_rows, sample_ids


def build_catalog(
    samples: list[Sample],
    field_rows: list[dict[str, Any]],
    occurrence_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    fields_by_path: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    occurrences_by_path: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    sample_by_id = {sample.sample_id: sample for sample in samples}
    samples_by_endpoint: dict[str, list[Sample]] = defaultdict(list)

    for sample in samples:
        samples_by_endpoint[sample.endpoint_id].append(sample)
    for row in field_rows:
        fields_by_path[(row["endpoint_id"], row["comparison_json_path"])].append(row)
    for row in occurrence_rows:
        occurrences_by_path[(row["endpoint_id"], row["comparison_json_path"])].append(row)

    catalog: list[dict[str, Any]] = []
    for key in sorted(fields_by_path, key=lambda item: (endpoint_sort_key(item[0]), item[1])):
        endpoint_id, path = key
        fields = fields_by_path[key]
        occurrences = occurrences_by_path[key]
        names = Counter(row["field_name"] for row in fields)
        observed_sample_ids = {
            row["sample_id"] for row in occurrences if row["presence_state"] != "MISSING_EXPECTED_PATH"
        }
        observed_times = sorted(
            sample_by_id[sample_id].response_received_at_utc
            for sample_id in observed_sample_ids
            if sample_by_id[sample_id].response_received_at_utc
        )
        presence_state_sets = [
            set(filter(None, row["presence_states"].split(";"))) for row in occurrences
        ]

        security_types = sorted(
            {
                str(sample.metadata.get("project_security_type"))
                for sample in samples_by_endpoint[endpoint_id]
                if sample.metadata.get("project_security_type")
            }
        )
        exchanges = sorted(
            {
                str(sample.metadata.get("expected_exchange"))
                for sample in samples_by_endpoint[endpoint_id]
                if sample.metadata.get("expected_exchange")
            }
        )
        market_states = sorted(
            {
                str(sample.metadata.get("market_state_target"))
                for sample in samples_by_endpoint[endpoint_id]
                if sample.metadata.get("market_state_target")
            }
        )

        catalog.append(
            {
                "endpoint_id": endpoint_id,
                "comparison_json_path": path,
                "field_name": names.most_common(1)[0][0],
                "observed_json_types": ";".join(sorted({row["json_type"] for row in fields})),
                "interpreted_type": "",
                "units": "",
                "working_definition": "",
                "first_seen_utc": observed_times[0] if observed_times else "",
                "last_seen_utc": observed_times[-1] if observed_times else "",
                "sample_count": len(samples_by_endpoint[endpoint_id]),
                "present_count": len(observed_sample_ids),
                "null_count": sum(
                    "PRESENT_EXPLICIT_NULL" in states for states in presence_state_sets
                ),
                "empty_count": sum(bool(states & EMPTY_STATES) for states in presence_state_sets),
                "observed_value_count": len(fields),
                "project_security_types_seen": ";".join(security_types),
                "exchanges_seen": ";".join(exchanges),
                "market_states_seen": ";".join(market_states),
                "review_status": "UNREVIEWED",
                "confidence_level": "OBSERVED",
                "notes": "",
            }
        )
    return catalog


def build_type_conflicts(field_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in field_rows:
        grouped[(row["endpoint_id"], row["comparison_json_path"])].append(row)

    conflicts: list[dict[str, Any]] = []
    for key in sorted(grouped, key=lambda item: (endpoint_sort_key(item[0]), item[1])):
        rows = grouped[key]
        types = sorted({row["json_type"] for row in rows})
        if len(types) <= 1:
            continue
        samples_by_type: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            samples_by_type[row["json_type"]].add(row["sample_id"])
        conflicts.append(
            {
                "endpoint_id": key[0],
                "comparison_json_path": key[1],
                "field_name": Counter(row["field_name"] for row in rows).most_common(1)[0][0],
                "observed_json_types": ";".join(types),
                "sample_ids_by_type_json": canonical_json(
                    {kind: sorted(sample_ids) for kind, sample_ids in sorted(samples_by_type.items())}
                ),
            }
        )
    return conflicts


def analyze_capture_run(run_dir: Path, output_dir: Path | None = None) -> dict[str, Any]:
    run_dir = run_dir.expanduser().resolve()
    if not run_dir.is_dir():
        raise AnalysisError(f"Run directory does not exist: {run_dir}")

    manifest_path = locate_manifest(run_dir)
    manifest, samples, source_warnings = load_samples(run_dir, manifest_path)
    output_dir = (output_dir or run_dir / "analysis").expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    sample_rows = [sample_row(sample) for sample in samples]
    field_rows = build_field_rows(samples)
    occurrence_rows, matrix_rows, sample_ids = build_occurrences(samples, field_rows)
    catalog_rows = build_catalog(samples, field_rows, occurrence_rows)
    conflict_rows = build_type_conflicts(field_rows)

    output_paths = {
        "samples.csv": output_dir / "samples.csv",
        "fields-long.csv": output_dir / "fields-long.csv",
        "field-catalog.csv": output_dir / "field-catalog.csv",
        "field-occurrence-long.csv": output_dir / "field-occurrence-long.csv",
        "field-occurrence-matrix.csv": output_dir / "field-occurrence-matrix.csv",
        "type-conflicts.csv": output_dir / "type-conflicts.csv",
        "validation.json": output_dir / "validation.json",
    }

    row_counts = {
        "samples.csv": write_csv(output_paths["samples.csv"], SAMPLES_COLUMNS, sample_rows),
        "fields-long.csv": write_csv(output_paths["fields-long.csv"], FIELDS_COLUMNS, field_rows),
        "field-catalog.csv": write_csv(output_paths["field-catalog.csv"], CATALOG_COLUMNS, catalog_rows),
        "field-occurrence-long.csv": write_csv(
            output_paths["field-occurrence-long.csv"], OCCURRENCE_COLUMNS, occurrence_rows
        ),
        "field-occurrence-matrix.csv": write_csv(
            output_paths["field-occurrence-matrix.csv"],
            ["endpoint_id", "comparison_json_path", "field_name", *sample_ids],
            matrix_rows,
        ),
        "type-conflicts.csv": write_csv(
            output_paths["type-conflicts.csv"], TYPE_CONFLICT_COLUMNS, conflict_rows
        ),
    }

    manifest_bytes = manifest_path.read_bytes()
    generated_file_hashes = {
        name: sha256_bytes(path.read_bytes())
        for name, path in output_paths.items()
        if name != "validation.json"
    }

    validation = {
        "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
        "analyzer_version": ANALYZER_VERSION,
        "run_id": manifest.get("run_id") or run_dir.name,
        "source_manifest_file": manifest_path.name,
        "source_manifest_sha256": sha256_bytes(manifest_bytes),
        "source_run_completed_at_utc": manifest.get("run_completed_at_utc"),
        "source_validation": {
            "status": "PASS",
            "sample_count": len(samples),
            "raw_json_count": len(samples),
            "warnings": source_warnings,
        },
        "outputs": {
            name: {
                "file": name,
                "row_count": row_counts[name],
                "sha256": generated_file_hashes[name],
            }
            for name in generated_file_hashes
        },
        "summary": {
            "endpoint_count": len({sample.endpoint_id for sample in samples}),
            "sample_count": len(samples),
            "flattened_field_row_count": len(field_rows),
            "catalog_path_count": len(catalog_rows),
            "occurrence_row_count": len(occurrence_rows),
            "matrix_row_count": len(matrix_rows),
            "type_conflict_count": len(conflict_rows),
        },
    }
    write_json(output_paths["validation.json"], validation)
    return validation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert one Yahoo capture run into deterministic long-form analysis CSV files."
    )
    parser.add_argument(
        "run_dir",
        nargs="?",
        type=Path,
        help="Capture run directory. When omitted, analyze the newest directory under captures/local.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory. Default: <run_dir>/analysis",
    )
    parser.add_argument(
        "--captures-root",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "captures" / "local",
        help="Root searched when run_dir is omitted.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        run_dir = args.run_dir or find_latest_run(args.captures_root.expanduser().resolve())
        validation = analyze_capture_run(run_dir, args.output_dir)
    except AnalysisError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    summary = validation["summary"]
    output_dir = (args.output_dir or Path(run_dir) / "analysis").expanduser().resolve()
    print(f"Analysis complete: {output_dir}")
    print(f"  endpoints: {summary['endpoint_count']}")
    print(f"  samples: {summary['sample_count']}")
    print(f"  flattened field rows: {summary['flattened_field_row_count']}")
    print(f"  catalog paths: {summary['catalog_path_count']}")
    print(f"  occurrence rows: {summary['occurrence_row_count']}")
    print(f"  type conflicts: {summary['type_conflict_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
