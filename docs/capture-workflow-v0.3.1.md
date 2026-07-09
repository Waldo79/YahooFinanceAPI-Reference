# Capture Workflow v0.3.1

The previous project setup identified that manual captures take about three minutes each and that representative coverage is preferred over a large symbol universe.

## Capture priority

1. `quote` endpoint across market states for the representative symbol set.
2. `chart` endpoint for all representative symbols.
3. `options` for AAPL and SPY.
4. `quoteSummary` for equity, ETF, CEF, and mutual fund representatives.
5. `search`, `screener`, and `spark` as discovery/compact endpoints.

## Market-state targets

Capture `quote` data when Yahoo returns these states, when available:

- `PREPRE`
- `PRE`
- `REGULAR`
- `POST`
- `POSTPOST`
- `CLOSED`

Not every symbol/security type will produce every state. Missing state coverage should be recorded as missing evidence, not parser failure.

## Raw JSON storage convention

Recommended path pattern:

```text
captures/raw/{endpoint}/{symbol_safe}/{yyyy-mm-dd}/{market_state_or_na}/{timestamp_utc}.json
```

Examples:

```text
captures/raw/quote/AAPL/2026-07-09/REGULAR/20260709T170501Z.json
captures/raw/chart/NAC/2026-07-09/NA/20260709T170501Z.json
```

## Unknown-field logging

For each capture, generate a candidate list:

```text
captures/unknown_fields/{endpoint}/{capture_id}_unknown_fields.csv
```

Suggested columns:

```text
capture_id,symbol,endpoint,market_state,json_path,field_name,json_type,example_value,parent_object,first_seen_utc
```


## v0.3.1 capture-health addition

Before extracting endpoint fields, classify each response with `result_state`.

Do not let a failed request create blank financial rows that look like valid zero/null market data. First write a capture diagnostic record using:

- `http_status_code`
- `request_url`
- `requested_symbol`
- `result_state`
- `result_count`
- endpoint-specific error fields such as `chart_error_code`

AAPL null results from search/chart and null predefined screener results are queued for re-test with HTTP diagnostics.
