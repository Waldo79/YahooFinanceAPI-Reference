# YahooFinanceAPI-Reference

A public reference and change-tracking project for observed Yahoo Finance API behavior.

## Current release

v0.4.3 — Zero-Pause Capture Baseline

This project documents observed Yahoo Finance API endpoint behavior, field/schema changes, symbol coverage, market-state behavior, data timing, and data-quality anomalies over time.

This is not an official Yahoo Finance project. It is also not primarily an application-development project. Scripts, templates, validators, and workbooks are support tools for repeatable public observation and documentation.

## What this release changes

v0.4.3 changes the Quote capture utility's normal inter-symbol pause from 1,000 milliseconds to 0 milliseconds after repeated successful live stopwatch tests showed that the fixed delay was unnecessary for the tested workflow.

The utility now:

- sends sequential Quote requests without an added fixed delay by default;
- keeps `--pause-ms` as an explicit pacing override;
- preserves retry delays for HTTP 429 and retryable 5xx responses;
- preserves the 30-second default per-attempt timeout; and
- preserves the one-time anonymous-session refresh after HTTP 401 or 403.

The July 16, 2026 16-symbol stopwatch runs completed successfully in 3.82 seconds at 0 ms, 4.31 seconds at 25 ms, and 3.65 seconds when 0 ms was repeated. These observations establish the project default; they do not guarantee that Yahoo will never throttle future runs.

```text
Anonymous session → Sequential Quote capture → No added normal pause → Existing retry safeguards → Run validation
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

Run a capture with the v0.4.3 default of 0 ms between symbols:

```text
py tools\capture-utility\yahoo_capture.py
```

Run with an explicit pause when desired:

```text
py tools\capture-utility\yahoo_capture.py --pause-ms 1000
```

Validate a completed run:

```text
py tools\capture-utility\yahoo_capture.py --validate-run captures\local\<run-folder>
```

The default output always resolves to the repository-root `captures/local/`, even when the command is launched from another directory.

See `tools/capture-utility/README.md` for the complete command reference, Windows instructions, pacing controls, and output layout.

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

v0.4.3 captures and validates the Quote endpoint with anonymous cookie-and-crumb session support and a 0 ms normal inter-symbol pause. Chart, QuoteSummary, Search, Screener, Options, comparison, scheduled capture, and workbook export remain later stages.

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
