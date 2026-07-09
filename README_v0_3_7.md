# YahooFinanceAPI-Reference

A public reference and change-tracking project for **observed Yahoo Finance API behavior**.

This project helps users document how Yahoo Finance data responses change over time, including fields, schemas, endpoint outputs, market states, symbol coverage, null or missing results, and security-type differences.

## Current release

**v0.3.6-draft — Initial Public README and GitHub Landing Page**

This release improves the GitHub front page and first-time-user guidance. The master field database remains unchanged at **198 rows**.

## What this project tracks

- Yahoo Finance response fields and JSON paths
- Endpoint output behavior across Quote, Search, Screener, Chart, QuoteSummary, Options, and related captures
- Security-type differences for stocks, ETFs, closed-end funds, mutual funds, futures, indexes, cryptocurrencies, and forex
- `marketState` and quote timing behavior
- Null results, empty results, partial symbol results, missing requested symbols, and schema drift
- Public observations that can later become confirmed change records

## What this project does not do

This is **not** an official Yahoo Finance project.

This is also not primarily an app-development or app-debugging project. Some scripts, templates, and validators are included, but they exist to support repeatable public observation and documentation.

The project does not provide investment advice, guarantee endpoint availability, or promise that any unofficial Yahoo Finance endpoint will remain stable.

## Quick start for public users

1. Download the latest release ZIP.
2. Open the workbook or CSV files.
3. Start with `data/master_field_database.csv` and `data/result_interpretation_rules.csv`.
4. If you use a third-party app with a **Symbol Look Up** feature, test symbols or symbol lists only unless the app explicitly supports direct URL capture.
5. Record what you requested, what was returned, and what was missing.
6. Use the observation templates before reporting a possible Yahoo API change.

## Recommended first files

| File | Purpose |
|---|---|
| `data/master_field_database.csv` | Main field/reference database |
| `data/representative_symbols.csv` | Standard symbols used for repeatable captures |
| `data/result_interpretation_rules.csv` | How to classify success, nulls, missing symbols, and schema drift |
| `data/user_accessible_test_plan.csv` | Tests public users can run without app source-code access |
| `templates/symbol_lookup_run_log_template.csv` | Template for recording symbol lookup tests |
| `templates/change_record_template.csv` | Template for documenting confirmed changes |

## Repository map

| Folder | Purpose |
|---|---|
| `data/` | Current CSV reference tables and public tracking data |
| `docs/` | Human-readable documentation and workflow notes |
| `templates/` | CSV templates for observations, run logs, and change records |
| `observations/raw/` | User-submitted raw observations before review |
| `observations/reviewed/` | Observations that have been checked for clarity and evidence |
| `observations/confirmed/` | Repeatable or well-supported findings promoted to change records |
| `scripts/` | Optional validation/support scripts |
| `tests/` | Lightweight checks for package integrity and required files |
| `releases/` | Versioned release snapshots when used by maintainers |

## How to report a Yahoo Finance API change

A useful observation should include:

- date and time of the test
- timezone
- symbol or symbol list tested
- endpoint, URL, or app feature used
- expected result
- actual result
- raw JSON, CSV, screenshot, or copied error text when available
- whether the test was repeated
- result classification, if known

Do not include private account data, cookies, credentials, or proprietary app source code.

## App developers

App developers are welcome as contributors or users of the public reference project. Their role is secondary to the main mission: documenting Yahoo Finance API behavior for public users over time.

## Release status

- Current release: **v0.3.6-draft**
- Master field database rows: **198**
- Primary deliverable: public README / landing-page guidance
- Package type: normal commit-ready ZIP first


## v0.3.7 — Issue Templates and Observation Forms

This release adds GitHub-ready issue forms so public users can report Yahoo Finance API observations consistently.

The forms cover field changes, missing symbols, empty/null endpoint results, marketState observations, schema drift, stale quote data, mutual fund/NAV timing, special-symbol problems, and documentation corrections.

The forms are designed for public users and black-box app users. Source-code access is not required.
