# Master Field Database Design

Release: `v0.3.1-draft`  
Date: `2026-07-09`

This begins the master field database for the `YahooFinanceAPI-Reference` project.

## Purpose

The master field database is the source-of-truth table for Yahoo Finance JSON fields, CSV names, parser behavior, and validation rules. It is designed to support the dynamic-schema decision already made for the project:

- route first by endpoint;
- then use `quoteType` / `instrumentType`;
- never rely only on symbol suffixes;
- preserve unknown/raw fields;
- append new CSV columns rather than reordering existing columns.

## Files

| File | Purpose |
|---|---|
| `data/master_field_database.csv` | Main database; 142 starter rows. |
| `data/master_field_database_columns.csv` | Column definitions for the database. |
| `data/representative_symbols.csv` | Two-symbol-per-security-type capture set plus user-confirmed symbols. |
| `data/enumerations.csv` | Known enum values; parser should warn on unknown values, not fail. |
| `data/capture_tasks_v0_3_0.csv` | Initial endpoint/market-state capture queue. |
| `schemas/master_field_database.schema.json` | Basic JSON schema for row structure. |
| `scripts/validate_master_field_database.py` | Offline structural validator. |
| `tests/test_master_field_database.py` | Pytest wrapper for CI. |

## Row status workflow

Use `status` values as follows:

- `draft`: seeded from prior workbook/reference knowledge; not yet confirmed against new raw JSON capture.
- `active`: observed in current raw capture and reviewed.
- `needs_review`: conflicting value/type/path evidence or unclear meaning.
- `deprecated`: known removed/replaced field; keep the row ID but stop exporting it by default.

## Capture workflow

1. Capture raw JSON for the representative symbol set.
2. Store the full raw response unchanged.
3. Extract all paths from the raw JSON.
4. Match known paths to `master_field_database.csv`.
5. Log unknown paths as candidate fields.
6. Promote fields from `draft` to `active` only after observation.

## Recommended next commit

```bash
git checkout -b v0.3.0-master-field-database
git add data docs schemas scripts tests CHANGELOG.md README_v0_3_0.md
python scripts/validate_master_field_database.py
git commit -m "Start master field database"
```


## v0.3.1 diagnostic field extension

The master database now includes request and endpoint diagnostic fields. These are not financial Yahoo fields; they are capture-run fields that make field extraction safer.

Reason: a null or empty Yahoo response can mean several different things:

- invalid symbol
- unsupported endpoint for that symbol
- Yahoo error object
- HTTP 429 rate limit
- 401/403 access/session problem
- schema drift
- non-JSON/HTML response

The parser should therefore classify the response before flattening financial data.
