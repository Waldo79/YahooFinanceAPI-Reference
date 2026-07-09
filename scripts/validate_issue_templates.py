#!/usr/bin/env python3
from pathlib import Path
import csv

ROOT = Path(__file__).resolve().parents[1]
required = [
    "README.md",
    "CHANGELOG.md",
    "data/issue_template_index.csv",
    "data/issue_field_crosswalk.csv",
    "data/observation_form_index.csv",
    ".github/ISSUE_TEMPLATE/field_change.yml",
    ".github/ISSUE_TEMPLATE/symbol_missing.yml",
    ".github/ISSUE_TEMPLATE/endpoint_empty_null.yml",
    ".github/ISSUE_TEMPLATE/marketstate_observation.yml",
    ".github/ISSUE_TEMPLATE/schema_drift.yml",
    ".github/ISSUE_TEMPLATE/stale_or_bad_quote.yml",
    ".github/ISSUE_TEMPLATE/mutual_fund_nav_timing.yml",
    ".github/ISSUE_TEMPLATE/special_symbol_problem.yml",
    ".github/ISSUE_TEMPLATE/documentation_correction.yml",
]

missing = [p for p in required if not (ROOT / p).exists()]
if missing:
    raise SystemExit("Missing required files: " + ", ".join(missing))

with open(ROOT / "data/issue_template_index.csv", newline="", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))
if len(rows) < 8:
    raise SystemExit("Expected at least 8 issue templates.")

for row in rows:
    path = ROOT / row["github_file"]
    if not path.exists():
        raise SystemExit(f"Template listed in index but missing: {path}")
    text = path.read_text(encoding="utf-8")
    for term in ["name:", "description:", "body:", "Observation date/time", "Result state"]:
        if term not in text:
            raise SystemExit(f"Template {path} is missing required term: {term}")

print("OK: validated v0.3.7 issue templates and observation forms")
