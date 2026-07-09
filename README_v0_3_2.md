# YahooFinanceAPI-Reference v0.3.2 draft

This release continues the master field database work and adds the first capture-harness routing layer.

## Why v0.3.2 exists

The July 8 capture logs showed two different issues that should not be combined:

1. A batched Quote endpoint request included `AAPL`, but the returned `quoteResponse.result[].symbol` list did not include `AAPL`. This is now classified as `REQUESTED_SYMBOL_MISSING_FROM_RESULT`.
2. Search, Screener, and Chart full URLs were routed through Quote/Fundamentals-style routines. This produced `quoteResponse.result=[]` and QuoteSummary errors where the full URL was treated as the ticker symbol. This is now classified as `PARSER_ENDPOINT_MISMATCH` or `FULL_URL_AS_SYMBOL`.

## Major additions

- Master field database expanded from 158 to 178 rows.
- New capture-routing fields:
  - `input_type`
  - `endpoint_type`
  - `parser_selected`
  - `parser_expected`
  - `route_valid`
  - `requested_symbols`
  - `returned_symbols`
  - `missing_requested_symbols`
  - `full_url_passed_as_symbol`
- New data tables:
  - `capture_router_rules_v0_3_2.csv`
  - `capture_run_findings_v0_3_2.csv`
  - `endpoint_failure_modes_v0_3_2.csv`
  - `capture_observations_v0_3_2.csv`
  - `capture_tasks_v0_3_2.csv`
  - `endpoint_health_urls_v0_3_2.csv`
- New scripts:
  - `scripts/endpoint_router_reference.py`
  - `scripts/analyze_capture_log.py`
- New tests:
  - `tests/test_v0_3_2_router.py`

## Operating rule

Before parsing financial data, the capture harness must classify the input and choose the parser from the endpoint URL path. Full URLs must never be passed as ticker symbols.
