#!/usr/bin/env python3
"""Validate v0.3.8 observation review and change-classification tables."""
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "data/review_status_categories.csv",
    "data/evidence_quality_levels.csv",
    "data/change_classification_rules.csv",
    "data/false_positive_checks.csv",
    "data/duplicate_handling_rules.csv",
    "data/retest_workflow.csv",
    "data/change_promotion_gates.csv",
    "data/review_queue_template.csv",
    "data/review_labels.csv",
    "docs/observation_review_and_change_classification_v0_3_8.md",
]

EXPECTED_STATUSES = {
    "NEW_OBSERVATION",
    "NEEDS_CLARIFICATION",
    "NEEDS_RETEST",
    "UNDER_REVIEW",
    "LIKELY_APP_SPECIFIC",
    "LIKELY_TEMPORARY_YAHOO_ISSUE",
    "DUPLICATE",
    "CONFIRMED_YAHOO_API_CHANGE",
    "REJECTED_FALSE_POSITIVE",
    "DEFERRED_MONITORING",
}

EXPECTED_CLASSIFICATIONS = {
    "FIELD_ADDED",
    "FIELD_REMOVED_OR_MISSING",
    "FIELD_TYPE_CHANGED",
    "JSON_PATH_CHANGED",
    "ENUM_VALUE_ADDED",
    "SYMBOL_COVERAGE_CHANGE",
    "ENDPOINT_AVAILABILITY_CHANGE",
    "SCHEMA_DRIFT",
    "DATA_STALENESS_OR_TIMING",
    "APP_OR_TOOL_BEHAVIOR",
    "NO_CHANGE_FALSE_POSITIVE",
}

def read_csv(rel):
    path = ROOT / rel
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

def test_required_files_exist():
    missing = [rel for rel in REQUIRED_FILES if not (ROOT / rel).exists()]
    assert not missing, f"Missing required files: {missing}"

def test_review_statuses_complete():
    rows = read_csv("data/review_status_categories.csv")
    values = {r["review_status"] for r in rows}
    assert EXPECTED_STATUSES.issubset(values)
    assert all(r["status_id"].startswith("RS") for r in rows)

def test_evidence_levels_ordered():
    rows = read_csv("data/evidence_quality_levels.csv")
    ids = [r["evidence_id"] for r in rows]
    assert ids == ["E0", "E1", "E2", "E3", "E4", "E5"]
    assert rows[0]["evidence_level"] == "UNUSABLE"
    assert rows[-1]["evidence_level"] == "RELEASE_RECORD"

def test_classification_rules_complete():
    rows = read_csv("data/change_classification_rules.csv")
    values = {r["classification"] for r in rows}
    assert EXPECTED_CLASSIFICATIONS.issubset(values)
    assert all(r["minimum_evidence"] for r in rows)

def test_false_positive_checks_include_app_and_rate_limit():
    rows = read_csv("data/false_positive_checks.csv")
    types = {r["false_positive_type"] for r in rows}
    assert "APP_OR_WRAPPER_LIMITATION" in types
    assert "FULL_URL_AS_SYMBOL" in types
    assert "RATE_LIMIT_OR_BLOCK" in types

def test_promotion_gates_require_evidence_and_false_positive_checks():
    rows = read_csv("data/change_promotion_gates.csv")
    text = "\n".join(r["promotion_gate"] + " " + r["required_before_confirming"] for r in rows)
    assert "Raw output" in text
    assert "False-positive" in text
    assert "Evidence level" in text

if __name__ == "__main__":
    test_required_files_exist()
    test_review_statuses_complete()
    test_evidence_levels_ordered()
    test_classification_rules_complete()
    test_false_positive_checks_include_app_and_rate_limit()
    test_promotion_gates_require_evidence_and_false_positive_checks()
    print("OK: validated v0.3.8 review-classification tables")
