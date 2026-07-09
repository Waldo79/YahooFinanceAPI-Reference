# YahooFinanceAPI-Reference v0.3.1 Draft Package

This package continues the master field database phase and adds response-health diagnostics after the reported AAPL null-result run.

## What changed from v0.3.0

- Expands `data/master_field_database.csv` from 142 to 158 rows.
- Adds request/endpoint diagnostic fields:
  - HTTP status
  - request URL
  - request timestamp
  - requested symbol
  - result state
  - result count
  - Yahoo error fields
- Adds `data/capture_observations_v0_3_1.csv`.
- Adds `data/endpoint_failure_modes_v0_3_1.csv`.
- Adds `data/endpoint_health_urls_v0_3_1.csv`.
- Updates capture tasks to mark AAPL search/chart and predefined screeners as `needs_retest`.
- Adds `docs/aapl-null-result-triage-v0.3.1.md`.

## Key conclusion

AAPL should not be marked invalid from the reported nulls. A predefined screener URL has no AAPL symbol parameter, so a screener null result is endpoint/request/access evidence, not AAPL-specific evidence.

A cross-endpoint failure must first be classified using HTTP status and `result_state`.
