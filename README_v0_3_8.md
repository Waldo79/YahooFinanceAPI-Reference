# YahooFinanceAPI-Reference

A public reference and change-tracking project for observed Yahoo Finance API behavior.

## Current release: v0.3.8 — Observation Review and Change Classification

This project documents observed Yahoo Finance API endpoint behavior, field/schema changes, symbol coverage, market-state behavior, data timing, and data-quality anomalies over time.

It is not an official Yahoo Finance project. It is also not primarily an application-development project. Scripts, templates, validators, and workbooks are support tools for repeatable public observation and documentation.

## What this release adds

v0.3.8 adds a review layer so public reports are handled consistently before they are accepted as real Yahoo API changes.

A report now moves through a controlled workflow:

```text
New observation
→ Evidence review
→ False-positive checks
→ Retest if needed
→ Classification
→ Confirmed change record or rejected/duplicate/app-specific note
```

## Important principle

A user report should start as an observation, not immediately as a confirmed Yahoo Finance API change.

This protects the project from promoting temporary outages, app-specific behavior, symbol-entry mistakes, rate limits, or wrapper/parser behavior as Yahoo API changes.

## Main files

- `data/master_field_database.csv` — observed Yahoo API field database
- `data/review_status_categories.csv` — review status definitions
- `data/evidence_quality_levels.csv` — evidence quality scale
- `data/change_classification_rules.csv` — change type rules
- `data/false_positive_checks.csv` — checks before confirming a change
- `data/retest_workflow.csv` — repeat-test process
- `data/change_promotion_gates.csv` — gates for confirmed records
- `.github/ISSUE_TEMPLATE/` — public report forms
- `docs/` — project guidance and release notes

## Public users

You do not need to be a programmer to contribute. A useful report should include:

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
