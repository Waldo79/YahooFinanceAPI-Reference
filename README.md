# YahooFinanceAPI-Reference

A public reference and change-tracking project for observed Yahoo Finance API behavior.

## Current release

v0.4.2 — Capture Validation and Path Hardening

This project documents observed Yahoo Finance API endpoint behavior, field/schema changes, symbol coverage, market-state behavior, data timing, and data-quality anomalies over time.

This is not an official Yahoo Finance project. It is also not primarily an application-development project. Scripts, templates, validators, and workbooks are support tools for repeatable public observation and documentation.

## What this release adds

v0.4.2 hardens the first working Quote capture utility after review of a successful 16-symbol live run.

The utility now provides:

- a repository-root default output directory that does not depend on the current Command Prompt directory;
- repository-relative path references in manifests, avoiding disclosure of Windows usernames and local directory layouts;
- per-symbol progress lines during capture;
- `--validate-run` for completed capture folders;
- SHA-256, byte-count, sequence, symbol, manifest, sidecar, and file-set verification;
- privacy scanning for unredacted crumb, cookie, and authorization values;
- known and unmapped JSON-path reporting; and
- machine-readable and human-readable validation reports.

The v0.4.1 anonymous Yahoo cookie-and-crumb session remains in place. Cookie and crumb values remain in memory and are never written to capture evidence.

```text
Anonymous session → Sequential Quote capture → Portable evidence paths → Run validation → Review
```

## Important principles

Raw Yahoo responses remain unchanged. Capture context belongs in filenames, metadata sidecars, normalized output, and the run manifest rather than being inserted into raw JSON.

A user report should still start as an observation, not immediately as a confirmed Yahoo Finance API change.

## Run the capture utility

For consistency on Windows, open Command Prompt from the repository root.

Validate the default table without contacting Yahoo:

```text
py tools\capture-utility\yahoo_capture.py --dry-run
```

Run a capture:

```text
py tools\capture-utility\yahoo_capture.py
```

Validate a completed run:

```text
py tools\capture-utility\yahoo_capture.py --validate-run captures\local\<run-folder>
```

The default output always resolves to the repository-root `captures/local/`, even when the command is launched from another directory.

See `tools/capture-utility/README.md` for the complete command reference, Windows instructions, and output layout.

## Main files

- `tools/capture-utility/yahoo_capture.py` — Quote evidence capture and run-validation utility
- `tools/capture-utility/symbols.csv` — user-editable representative-symbol table
- `tests/test_capture_utility.py` — offline capture and validation tests
- `schemas/run-validation.schema.json` — JSON Schema for `run-validation.json`
- `data/master_field_database.csv` — observed Yahoo API field database
- `data/review_status_categories.csv` — review status definitions
- `data/evidence_quality_levels.csv` — evidence quality scale
- `data/change_classification_rules.csv` — change type rules
- `data/false_positive_checks.csv` — checks before confirming a change
- `data/retest_workflow.csv` — repeat-test process
- `data/change_promotion_gates.csv` — gates for confirmed records
- `docs/reference/` — human-readable API reference pages
- `docs/specifications/` — capture and normalized-output specifications
- `.github/ISSUE_TEMPLATE/` — public report forms
- `docs/` — project guidance and release notes

## Current utility scope

v0.4.2 captures and validates the Quote endpoint with anonymous cookie-and-crumb session support. Chart, QuoteSummary, Search, Screener, Options, comparison, scheduled capture, and workbook export remain later stages.

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

## Disclaimer

Yahoo Finance is a third-party service. Observed behavior may change without notice. This project is a public reference and tracking effort, not an official specification.
