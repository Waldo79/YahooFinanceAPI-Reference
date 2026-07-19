from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "endpoint-analysis"
    / "analyze_endpoint_captures.py"
)
SPEC = importlib.util.spec_from_file_location("analyze_endpoint_captures", MODULE_PATH)
assert SPEC and SPEC.loader
analysis = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = analysis
SPEC.loader.exec_module(analysis)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_run(tmp_path: Path, payloads: list[tuple[str, dict]]) -> Path:
    run_dir = tmp_path / "2026-07-19T00-00-00.000Z_test-run"
    raw_dir = run_dir / "raw"
    metadata_dir = run_dir / "metadata"
    raw_dir.mkdir(parents=True)
    metadata_dir.mkdir()

    requests = []
    for sequence, (endpoint_id, payload) in enumerate(payloads, 1):
        raw = json.dumps(payload, separators=(",", ":")).encode()
        raw_name = f"raw/{endpoint_id}-{sequence}.raw.json"
        meta_name = f"metadata/{endpoint_id}-{sequence}.meta.json"
        (run_dir / raw_name).write_bytes(raw)
        metadata = {
            "sequence": sequence,
            "endpoint_id": endpoint_id,
            "request_parameters": {"symbol": "AAPL"},
            "request_url_redacted": f"https://example.test/{endpoint_id}?symbol=AAPL&crumb=REDACTED",
            "http_status": 200,
            "content_type": "application/json",
            "response_bytes": len(raw),
            "raw_response_sha256": hashlib.sha256(raw).hexdigest(),
            "parse_status": "VALID_JSON",
            "result_classification": "EXPECTED_TOP_LEVEL_PRESENT",
            "raw_response_file": raw_name,
            "metadata_file": meta_name,
            "attempts": [
                {
                    "requested_at_utc": f"2026-07-19T00:00:0{sequence}.000Z",
                    "response_received_at_utc": f"2026-07-19T00:00:0{sequence}.100Z",
                    "elapsed_ms": 100,
                    "http_status": 200,
                    "error": None,
                }
            ],
        }
        (run_dir / meta_name).write_text(
            json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
        )
        requests.append(metadata)

    manifest = {
        "run_id": run_dir.name,
        "run_started_at_utc": "2026-07-19T00:00:00.000Z",
        "run_completed_at_utc": "2026-07-19T00:00:09.000Z",
        "requests": requests,
    }
    (run_dir / "verification-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return run_dir


def test_flatten_preserves_indexes_normalizes_arrays_and_distinguishes_states():
    payload = {
        "result": [
            {
                "symbol": "AAPL",
                "nullValue": None,
                "zeroValue": 0,
                "falseValue": False,
                "emptyString": "",
                "emptyArray": [],
                "emptyObject": {},
            }
        ]
    }
    fields = {
        row["evidence_json_path"]: row
        for row in analysis.flatten_json(payload, endpoint_id="quote")
    }

    assert fields["result[0].symbol"]["comparison_json_path"] == "result[].symbol"
    assert fields["result[0].symbol"]["array_identity"] == "symbol:AAPL"
    assert fields["result[0].nullValue"]["presence_state"] == "PRESENT_EXPLICIT_NULL"
    assert fields["result[0].zeroValue"]["presence_state"] == "PRESENT_ZERO"
    assert fields["result[0].falseValue"]["presence_state"] == "PRESENT_FALSE"
    assert fields["result[0].emptyString"]["presence_state"] == "PRESENT_EMPTY_STRING"
    assert fields["result[0].emptyArray"]["presence_state"] == "PRESENT_EMPTY_ARRAY"
    assert fields["result[0].emptyObject"]["presence_state"] == "PRESENT_EMPTY_OBJECT"


def test_flatten_uses_bracket_notation_for_non_identifier_keys():
    payload = {"a.b": {"space key": 1}}
    rows = analysis.flatten_json(payload, endpoint_id="quote")
    assert rows[0]["evidence_json_path"] == '["a.b"]["space key"]'
    assert rows[0]["comparison_json_path"] == '["a.b"]["space key"]'


def test_options_contract_symbol_becomes_array_identity():
    payload = {
        "optionChain": {
            "result": [
                {
                    "quote": {"symbol": "AAPL"},
                    "options": [
                        {
                            "expirationDate": 1784246400,
                            "calls": [
                                {
                                    "contractSymbol": "AAPL260717C00100000",
                                    "strike": 100,
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    }
    rows = analysis.flatten_json(payload, endpoint_id="options")
    strike = next(row for row in rows if row["field_name"] == "strike")
    assert "symbol:AAPL" in strike["array_identity"]
    assert "expirationDate:1784246400" in strike["array_identity"]
    assert "contractSymbol:AAPL260717C00100000" in strike["array_identity"]
    assert strike["returned_entity"] == "AAPL260717C00100000"


def test_analysis_creates_missing_occurrence_and_type_conflict(tmp_path: Path):
    run_dir = write_run(
        tmp_path,
        [
            (
                "quote",
                {"quoteResponse": {"result": [{"symbol": "AAPL", "value": 1, "onlyFirst": 9}]}},
            ),
            (
                "quote",
                {"quoteResponse": {"result": [{"symbol": "AAPL", "value": "one"}]}},
            ),
        ],
    )

    validation = analysis.analyze_capture_run(run_dir)
    output = run_dir / "analysis"

    assert validation["summary"]["sample_count"] == 2
    assert validation["summary"]["type_conflict_count"] == 1

    occurrence = read_csv(output / "field-occurrence-long.csv")
    missing = [
        row
        for row in occurrence
        if row["comparison_json_path"] == "quoteResponse.result[].onlyFirst"
        and row["presence_state"] == "MISSING_EXPECTED_PATH"
    ]
    assert len(missing) == 1

    conflicts = read_csv(output / "type-conflicts.csv")
    assert conflicts[0]["comparison_json_path"] == "quoteResponse.result[].value"
    assert conflicts[0]["observed_json_types"] == "number;string"

    matrix = read_csv(output / "field-occurrence-matrix.csv")
    only_first = next(
        row for row in matrix
        if row["comparison_json_path"] == "quoteResponse.result[].onlyFirst"
    )
    sample_columns = [column for column in only_first if column.startswith(run_dir.name)]
    assert sorted(only_first[column] for column in sample_columns) == ["M", "V"]


def test_analysis_is_deterministic(tmp_path: Path):
    run_dir = write_run(
        tmp_path,
        [("search", {"quotes": [{"symbol": "AAPL"}, {"shortname": "No Symbol"}]})],
    )
    output_one = tmp_path / "one"
    output_two = tmp_path / "two"

    analysis.analyze_capture_run(run_dir, output_one)
    analysis.analyze_capture_run(run_dir, output_two)

    for name in (
        "samples.csv",
        "fields-long.csv",
        "field-catalog.csv",
        "field-occurrence-long.csv",
        "field-occurrence-matrix.csv",
        "type-conflicts.csv",
        "validation.json",
    ):
        assert (output_one / name).read_bytes() == (output_two / name).read_bytes()


def test_hash_mismatch_stops_analysis(tmp_path: Path):
    run_dir = write_run(
        tmp_path,
        [("quote", {"quoteResponse": {"result": [{"symbol": "AAPL"}]}})],
    )
    raw_path = next((run_dir / "raw").glob("*.json"))
    raw_path.write_bytes(b'{"changed":true}')

    with pytest.raises(analysis.AnalysisError, match="SHA-256 mismatch"):
        analysis.analyze_capture_run(run_dir)
