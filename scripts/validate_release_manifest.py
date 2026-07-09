#!/usr/bin/env python3
"""Validate the v0.3.5 release package manifest and required files."""
import csv
import hashlib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
REQUIRED = [
    "README.md",
    "CHANGELOG.md",
    "MANIFEST.csv",
    "data/master_field_database.csv",
    "data/repository_structure.csv",
    "data/release_workflow.csv",
    "templates/release_checklist_template.csv",
]

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def main():
    missing = [p for p in REQUIRED if not (ROOT / p).exists()]
    if missing:
        print("Missing required files:", missing)
        return 1
    manifest_path = ROOT / "MANIFEST.csv"
    with open(manifest_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    bad = []
    for row in rows:
        p = ROOT / row["file_path"]
        if not p.exists():
            bad.append((row["file_path"], "missing"))
            continue
        if row.get("sha256") and sha256(p) != row["sha256"]:
            bad.append((row["file_path"], "checksum"))
    if bad:
        print("Manifest validation failed:", bad)
        return 1
    print("OK: validated v0.3.5 release package")
    return 0

if __name__ == "__main__":
    sys.exit(main())
