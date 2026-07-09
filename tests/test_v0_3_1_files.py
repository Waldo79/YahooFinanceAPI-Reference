from pathlib import Path
import csv

def test_v0_3_1_diagnostic_files_exist():
    root = Path(__file__).resolve().parents[1]
    required = [
        "data/capture_observations_v0_3_1.csv",
        "data/endpoint_failure_modes_v0_3_1.csv",
        "data/endpoint_health_urls_v0_3_1.csv",
        "docs/aapl-null-result-triage-v0.3.1.md",
        "scripts/classify_yahoo_response.py",
    ]
    for rel in required:
        assert (root / rel).exists(), rel

def test_result_state_enumeration_present():
    root = Path(__file__).resolve().parents[1]
    enum_path = root / "data" / "enumerations.csv"
    rows = list(csv.DictReader(enum_path.open(newline="", encoding="utf-8")))
    states = {r["value"] for r in rows if r["enum_group"] == "result_state"}
    assert "HTTP_ERROR_429" in states
    assert "NULL_RESULT" in states
    assert "EMPTY_RESULT" in states

def test_capture_observations_include_aapl_retest():
    root = Path(__file__).resolve().parents[1]
    obs_path = root / "data" / "capture_observations_v0_3_1.csv"
    rows = list(csv.DictReader(obs_path.open(newline="", encoding="utf-8")))
    aapl = [r for r in rows if r["symbol_or_query"] == "AAPL"]
    assert aapl
    assert any(r["status"] == "needs_retest" for r in aapl)
