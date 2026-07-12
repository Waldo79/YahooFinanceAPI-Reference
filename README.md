# YahooFinanceAPI-Reference

A public reference and change-tracking project for observed Yahoo Finance API behavior.

## Current release

v0.4.1 — Anonymous Yahoo Session Support

This project documents observed Yahoo Finance API endpoint behavior, field/schema changes, symbol coverage, market-state behavior, data timing, and data-quality anomalies over time.

This is not an official Yahoo Finance project. It is also not primarily an application-development project. Scripts, templates, validators, and workbooks are support tools for repeatable public observation and documentation.

## What this release adds

v0.4.1 hardens the first capture utility after the initial live run showed that bare Quote requests returned HTTP 401 Unauthorized.

The utility now provides:

- anonymous Yahoo cookie-and-crumb session setup using only the Python standard library;
- one automatic session refresh after HTTP 401 or 403;
- no persistence of cookie or crumb values;
- redacted request URLs in public evidence;
- a user-editable CSV table with the 16 representative symbols;
- sequential one-symbol Quote requests, with a project maximum of 30 enabled rows;
- byte-for-byte preservation of the final response body for each symbol;
- UTC timing, HTTP status, content type, response size, SHA-256, sidecars, normalized text, and a run manifest.

```text
Anonymous session → Symbol table → Sequential Quote requests → Raw evidence + SHA-256 → Metadata + normalized text → Run manifest → Review
```

## Important principles

Raw Yahoo responses remain unchanged. Capture context belongs in filenames, metadata sidecars, normalized output, and the run manifest rather than being inserted into raw JSON.

A user report should still start as an observation, not immediately as a confirmed Yahoo Finance API change.

## Run the capture utility

Validate the default table without contacting Yahoo:

```bash
python tools/capture-utility/yahoo_capture.py --dry-run
```

Run a capture:

```bash
python tools/capture-utility/yahoo_capture.py
```

See `tools/capture-utility/README.md` for the complete command reference and output layout.

## Main files

- `tools/capture-utility/yahoo_capture.py` — first working Quote evidence-capture utility
- `tools/capture-utility/symbols.csv` — user-editable representative-symbol table
- `tests/test_capture_utility.py` — offline tests using simulated HTTP responses
- `data/master_field_database.csv` — observed Yahoo API field database
- `data/review_status_categories.csv` — review status definitions
- `data/evidence_quality_levels.csv` — evidence quality scale
- `data/change_classification_rules.csv` — change type rules
- `data/false_positive_checks.csv` — checks before confirming a change
- `data/retest_workflow.csv` — repeat-test process
- `data/change_promotion_gates.csv` — gates for confirmed records
- `docs/reference/` — human-readable API reference pages
- `docs/specifications/` — capture and normalized-output specifications
- `templates/capture_run_manifest_template.json` — capture-run metadata template
- `templates/reference_evidence_record_template.json` — observation evidence template
- `.github/ISSUE_TEMPLATE/` — public report forms
- `docs/` — project guidance and release notes

## Current utility scope

v0.4.1 implements the Quote endpoint with anonymous cookie-and-crumb session support. Chart, QuoteSummary, Search, Screener, Options, comparison, scheduled capture, and workbook export remain later stages.

## Public users

You do not need to be a programmer to contribute.

A useful report should include:

- the symbol or endpoint tested,
- date and time of the observation,
- market state if known,
- exact output, log, screenshot, or raw JSON if available,
- whether the result was from raw Yahoo access or a third-party app/tool,
- what you expected to happen, and
- what actually happened.

## Review outcomes

Reports may be classified as:

- confirmed Yahoo API change,
- needs retest,
- needs clarification,
- likely temporary Yahoo issue,
- likely app-specific behavior,
- duplicate,
- rejected / false positive, or
- deferred for monitoring.

## Disclaimer

Yahoo Finance is a third-party service. Observed behavior may change without notice. This project is a public reference and tracking effort, not an official specification.
