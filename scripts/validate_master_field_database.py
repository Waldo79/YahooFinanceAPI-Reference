#!/usr/bin/env python3
"""
Validate data/master_field_database.csv for the YahooFinanceAPI-Reference project.

This script intentionally validates structure and consistency only.
It does not call Yahoo endpoints.
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

REQUIRED_COLUMNS = [
    "field_id","field_name","canonical_csv_name","endpoint","json_path","parent_object",
    "yahoo_type","csv_type","applies_to","field_category","description","nullable",
    "presence","evidence_status","parser_action","first_seen_release","last_reviewed_date","status"
]

ALLOWED_STATUS = {"draft", "active", "deprecated", "needs_review"}
ALLOWED_NULLABLE = {"Yes", "No"}
FIELD_ID_RE = re.compile(r"^YF\d{5}$")
CANONICAL_RE = re.compile(r"^[a-z][a-z0-9_]*$")

def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)

def warn(message: str) -> None:
    print(f"WARN: {message}", file=sys.stderr)

def main(path: str = "data/master_field_database.csv") -> None:
    csv_path = Path(path)
    if not csv_path.exists():
        fail(f"{csv_path} does not exist")

    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            fail(f"Missing required columns: {', '.join(missing)}")

        ids = set()
        csv_names_by_endpoint = set()
        count = 0
        for row_num, row in enumerate(reader, start=2):
            count += 1
            field_id = row["field_id"].strip()
            if not FIELD_ID_RE.match(field_id):
                fail(f"Row {row_num}: invalid field_id {field_id!r}")
            if field_id in ids:
                fail(f"Row {row_num}: duplicate field_id {field_id}")
            ids.add(field_id)

            if row["nullable"] not in ALLOWED_NULLABLE:
                fail(f"Row {row_num}: nullable must be Yes or No")

            if row["status"] not in ALLOWED_STATUS:
                fail(f"Row {row_num}: invalid status {row['status']!r}")

            if not CANONICAL_RE.match(row["canonical_csv_name"]):
                fail(f"Row {row_num}: invalid canonical_csv_name {row['canonical_csv_name']!r}")

            if not row["json_path"].strip():
                fail(f"Row {row_num}: blank json_path")

            key = (row["endpoint"], row["canonical_csv_name"])
            if key in csv_names_by_endpoint:
                warn(f"Row {row_num}: duplicate canonical_csv_name within endpoint: {key}")
            csv_names_by_endpoint.add(key)

    print(f"OK: validated {count} master field rows in {csv_path}")

if __name__ == "__main__":
    main(*sys.argv[1:])
