# YahooFinanceAPI-Reference

A public reference and change-tracking project for observed Yahoo Finance API behavior.

## Current release

v0.3.9 — Reference Pages and Capture Format Specification

This project documents observed Yahoo Finance API endpoint behavior, field/schema changes, symbol coverage, market-state behavior, data timing, and data-quality anomalies over time.

This is not an official Yahoo Finance project. It is also not primarily an application-development project. Scripts, templates, validators, and workbooks are support tools for repeatable public observation and documentation.

## What this release adds

v0.3.9 adds human-readable reference pages and formal capture and normalization specifications for repeatable Yahoo Finance API observations.

The release defines:

- reference pages for market states, security types, endpoints, timestamps, symbol formats, and data-delay behavior;
- sequential capture of up to 30 table-selected symbols, using one request per symbol in the first implementation;
- unchanged raw JSON with UTC metadata sidecars and SHA-256 integrity;
- deterministic normalized output with full JSON paths; and
- separate classifications for missing fields, explicit nulls, empty results, and requested symbols omitted from results.

```text
Reference definitions → Structured capture → Raw evidence preservation → Normalized output → Review and confirmation
```

## Important principles

Raw Yahoo responses should remain unchanged and valid. Capture context belongs in filenames, metadata sidecars, and normalized output rather than being inserted into the raw JSON.

A user report should still start as an observation, not immediately as a confirmed Yahoo Finance API change.

## Main files

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
