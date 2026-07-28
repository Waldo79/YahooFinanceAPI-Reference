# YahooFinanceAPI-Reference

A public reference and change-tracking project for observed Yahoo Finance API behavior.

## Current release

v0.4.3 — Zero-Pause Capture Baseline

## Current development

v0.5.0-draft — Seven-Endpoint Capture and Comparative Studies

The v0.5.0 work is active development and is **not yet a formal release**. The current
development line expands the project from the Quote capture baseline into controlled
observation and comparison across seven Yahoo Finance endpoint families:

- Quote
- Chart
- QuoteSummary
- Search
- Screener
- Fundamentals Timeseries
- Options

Completed v0.5.0-draft work includes:

- the seven-endpoint capture and analysis architecture;
- live endpoint and session-mode verification;
- the reusable endpoint-capture analyzer;
- Study 01 — Session Modes, including repeat confirmation and hardened evidence;
- Study 02A — Security-Type Quote Baseline, including live validation;
- Study 02B — Security-Type Replication, including 24-symbol live validation; and
- portable resolved study definitions, canonical JSON hashes, comparison tables, and
  secret-redaction checks.

The formal release remains v0.4.3 until the broader v0.5.0 implementation and release
documentation are complete.

```text
Formal release:       v0.4.3
Development line:     v0.5.0-draft
Completed studies:    Study 01, Study 02A, and Study 02B
```

This project documents observed Yahoo Finance API endpoint behavior, field/schema changes, symbol coverage, market-state behavior, data timing, and data-quality anomalies over time.

This is not an official Yahoo Finance project. It is also not primarily an application-development project. Scripts, templates, validators, and workbooks are support tools for repeatable public observation and documentation.

## What the current release changes

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

## v0.5.0-draft study documentation

- `docs/specifications/seven-endpoint-capture-analysis-architecture-v0.5.0-draft.md`
- `docs/specifications/seven-endpoint-live-verification-matrix-v0.5.0.md`
- `docs/specifications/study-01-session-mode-protocol-v0.5.0-draft.md`
- `docs/verification/study-01-session-mode-validation-2026-07-19.md`
- `docs/specifications/study-02a-security-type-quote-protocol-v0.5.0-draft.md`
- `docs/verification/study-02a-security-type-quote-validation-2026-07-19.md`
- `docs/specifications/study-02b-security-type-replication-protocol-v0.5.0-draft.md`
- `docs/verification/study-02b-security-type-replication-validation-2026-07-28.md`

## Main files

- `tools/capture-utility/yahoo_capture.py` — Quote evidence capture and run-validation utility
- `tools/capture-utility/symbols.csv` — user-editable representative-symbol table
- `tools/endpoint-analysis/analyze_endpoint_captures.py` — deterministic endpoint-capture analyzer
- `tools/session-mode-study/run_session_mode_study.py` — Study 01 capture tool
- `tools/security-type-study/run_security_type_quote_study.py` — Study 02A capture tool
- `tools/security-type-study/run_security_type_replication_study.py` — Study 02B capture tool
- `tests/test_capture_utility.py` — offline capture and validation tests
- `tests/test_session_mode_study.py` — Study 01 tests
- `tests/test_security_type_quote_study.py` — Study 02A tests
- `tests/test_security_type_replication_study.py` — Study 02B tests
- `schemas/run-validation.schema.json` — JSON Schema for `run-validation.json`
- `data/master_field_database.csv` — observed Yahoo API field database
- `data/review_status_categories.csv` — review status definitions
- `data/evidence_quality_levels.csv` — evidence quality scale
- `data/change_classification_rules.csv` — change type rules
- `data/false_positive_checks.csv` — checks before confirming a change
- `data/retest_workflow.csv` — repeat-test process
