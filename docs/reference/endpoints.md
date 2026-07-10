# Endpoints

## Purpose

This page is an endpoint registry for observed Yahoo Finance data sources.

Yahoo endpoints can change without notice. Endpoint patterns in this project are descriptive test targets, not official or guaranteed interfaces.

## Endpoint registry

| Endpoint ID | Working purpose | Typical scope | Direct test status | Third-party app status | Notes |
|---|---|---|---|---|---|
| `quote` | Current quote and market summary fields | One or more symbols | Under observation | App supports symbol lookup | A requested symbol may be omitted from a multi-symbol result; record this separately from a returned `null`. |
| `chart` | Historical bars and chart metadata | Usually one symbol | Planned | App compatibility not established | A URL entered into a symbol box may be treated as a symbol rather than as an endpoint. |
| `quote-summary` | Module-based profile, statistics, and fundamentals | Usually one symbol | Planned | App compatibility not established | Module availability can vary by symbol and asset type. |
| `search` | Symbol and news search | Search text rather than a ticker | Planned | App compatibility not established | Do not test by pasting a search URL into a symbol-only input unless application routing is the subject of the test. |
| `screener` | Predefined or custom screening results | Multiple securities | Planned | App compatibility not established | Screener behavior is not an AAPL-specific test. |
| `fundamentals-timeseries` | Time-series fundamentals | One or more symbols/metrics | Planned | App may route symbol requests here | Preserve requested metric names and date ranges. |

## Required endpoint evidence

Every endpoint observation should identify:

- endpoint ID;
- exact request parameters;
- symbol or search text;
- request timestamp in UTC;
- response timestamp in UTC;
- HTTP status;
- content type;
- response byte count;
- exact raw response file;
- whether authentication cookies, crumbs, or other session data were involved; and
- whether the request was direct or made through a third-party tool.

## Result classifications

Use distinct result classifications:

- `SUCCESS_RESULT_RETURNED`
- `SUCCESS_EMPTY_RESULT`
- `REQUESTED_SYMBOL_MISSING_FROM_RESULT`
- `FIELD_MISSING`
- `FIELD_EXPLICIT_NULL`
- `HTTP_ERROR`
- `PARSE_ERROR`
- `RATE_LIMIT_OR_THROTTLE`
- `APP_ROUTE_MISMATCH`
- `UNKNOWN_REQUIRES_RETEST`

This prevents a missing requested symbol, an absent field, and an explicit JSON `null` from being reported as the same condition.
