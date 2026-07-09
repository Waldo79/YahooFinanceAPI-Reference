# v0.3.8 — Observation Review and Change Classification

This release adds a review and classification layer for public Yahoo Finance API observations.

## Core rule

A user report starts as an **observation**, not as a confirmed Yahoo Finance API change.

Before an observation can become a confirmed project record, it should pass through:

1. evidence review,
2. false-positive checks,
3. retesting when needed,
4. duplicate handling,
5. change classification, and
6. promotion into a versioned change record.

## Why this matters

Yahoo Finance API behavior can be affected by many factors that are not true API schema changes:

- third-party app or wrapper behavior,
- symbol-only tools that do not support raw endpoint URLs,
- full URLs accidentally treated as ticker symbols,
- rate limits or HTTP 429 responses,
- market-state timing,
- delayed quotes or mutual fund NAV timing,
- special-symbol encoding problems,
- temporary Yahoo outages, and
- user-entry mistakes.

The review tables added in this release help prevent those cases from being promoted too quickly as confirmed Yahoo API changes.

## New reference tables

- `data/review_status_categories.csv`
- `data/evidence_quality_levels.csv`
- `data/change_classification_rules.csv`
- `data/false_positive_checks.csv`
- `data/duplicate_handling_rules.csv`
- `data/retest_workflow.csv`
- `data/change_promotion_gates.csv`
- `data/review_queue_template.csv`
- `data/review_labels.csv`

## Practical public workflow

1. A user submits an observation using an issue template.
2. A maintainer assigns a review status.
3. Evidence quality is recorded.
4. False-positive checks are applied.
5. The issue is retested or classified.
6. Confirmed changes are promoted into a versioned change record.
7. False positives, app-specific findings, and duplicates are closed or linked.

## Release notes

The master field database remains unchanged at 198 rows. This release adds governance and review workflow tables only.
