# Changelog

## v0.3.2 draft — 2026-07-09

### Added
- Capture-harness routing fields to the master field database.
- Router rules for Quote, Search, Screener, Chart, QuoteSummary, and Options endpoint URL patterns.
- Run findings from the July 8 user capture logs.
- Failure modes for `FULL_URL_AS_SYMBOL`, `PARSER_ENDPOINT_MISMATCH`, `REQUESTED_SYMBOL_MISSING_FROM_RESULT`, and `SPECIAL_SYMBOLS_NOT_RETURNED`.
- Endpoint health checks for AAPL single-symbol quote and special-character symbols.
- Reference router script and capture-log analyzer script.
- Pytest coverage for endpoint detection and symbol comparison.

### Changed
- AAPL batch omission is no longer grouped with null results.
- Search/Screener/Chart null or empty results from the captured app run are classified as routing/parser mismatch until retested with endpoint-specific parsers.
- Screener documentation now explicitly states that predefined screeners are not AAPL-specific.

### Validation
- `scripts/validate_master_field_database.py` validates 178 master field rows.
- Router tests validate URL classification, requested/returned symbol comparison, and full URL-as-symbol detection.
