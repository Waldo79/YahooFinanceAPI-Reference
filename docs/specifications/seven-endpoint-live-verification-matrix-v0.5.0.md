# v0.5.0 Seven-Endpoint Live-Verification Matrix

## Status

Source-confirmed draft. Live execution from the project owner's normal internet connection is still required.

The request patterns below were checked against the current maintained yfinance source. Yahoo Finance endpoints are unofficial and may change without notice. A source-confirmed pattern is not yet a project-confirmed live result.

## Verification objective

For each endpoint family:

1. send one minimal controlled request;
2. preserve the final response body byte-for-byte;
3. record UTC request and response timestamps;
4. record HTTP status, content type, byte count, and SHA-256;
5. record a redacted reproducible request;
6. classify whether valid JSON and a nonempty endpoint result were returned;
7. determine whether anonymous cookie-and-crumb state was accepted;
8. later repeat without crumb to measure whether the endpoint actually requires it; and
9. retain the evidence for adapter design.

## Initial session rule

Use the project's existing anonymous Yahoo cookie-and-crumb procedure for all seven first-pass requests.

This does **not** assert that every endpoint requires a crumb. It ensures that the first comparison does not fail merely because an endpoint expects session state. A later controlled test will compare:

- anonymous cookie plus crumb;
- anonymous cookie without crumb; and
- no prepared Yahoo session.

Sensitive cookie and crumb values must remain in memory and must never be written to evidence.

## Matrix

| Endpoint ID | Method | Current source-confirmed path | Minimal subject and parameters | Expected top-level response object | First-pass session mode | Live status |
|---|---|---|---|---|---|---|
| `quote` | GET | `https://query1.finance.yahoo.com/v7/finance/quote` | `symbols=AAPL`, `formatted=false`, `lang=en-US`, `region=US` | `quoteResponse` | Anonymous cookie + crumb | Pending owner-system run |
| `chart` | GET | `https://query2.finance.yahoo.com/v8/finance/chart/AAPL` | `range=5d`, `interval=1d`, `includePrePost=false`, `events=div,splits,capitalGains` | `chart` | Anonymous cookie + crumb | Pending owner-system run |
| `quote-summary` | GET | `https://query2.finance.yahoo.com/v10/finance/quoteSummary/AAPL` | `modules=price,summaryDetail`, `formatted=false`, `corsDomain=finance.yahoo.com`, `lang=en-US`, `region=US` | `quoteSummary` | Anonymous cookie + crumb | Pending owner-system run |
| `search` | GET | `https://query2.finance.yahoo.com/v1/finance/search` | `q=AAPL`, `quotesCount=5`, `newsCount=0`, plus stable query-control parameters | Search result object containing arrays such as `quotes` and `news` | Anonymous cookie + crumb | Pending owner-system run |
| `screener` | GET | `https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved` | `scrIds=day_gainers`, `count=5`, `offset=0`, `formatted=false`, `lang=en-US`, `region=US` | `finance` | Anonymous cookie + crumb | Pending owner-system run |
| `fundamentals-timeseries` | GET | `https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/AAPL` | `symbol=AAPL`, `type=quarterlyMarketCap`, dynamic `period1` and `period2` | `timeseries` | Anonymous cookie + crumb | Pending owner-system run |
| `options` | GET | `https://query2.finance.yahoo.com/v7/finance/options/AAPL` | No expiration for the first request; later use `date=<Unix expiration>` | `optionChain` | Anonymous cookie + crumb | Pending owner-system run |

## Endpoint-specific observations to record

### Quote

- number of requested and returned symbols;
- returned symbol order;
- missing requested symbols;
- market-state fields;
- exchange, quote type, currency, and applicable-time fields;
- fields that appear only in premarket or postmarket periods.

### Chart

- `meta`;
- `timestamp`;
- `indicators.quote`;
- `indicators.adjclose`;
- event objects;
- trading-period objects;
- null bars and unequal array lengths;
- effects of range, interval, and pre/post parameters.

### QuoteSummary

- requested module list;
- modules returned;
- module-level error or omission;
- nested `raw` and `fmt` representations;
- module differences by security type.

### Search

- exact query text;
- quote, news, list, research, and navigation result arrays;
- result ordering;
- exact versus fuzzy matching;
- symbol, exchange, and quote-type metadata.

### Screener

- predefined screener ID;
- start/count behavior;
- result total versus returned count;
- sort fields;
- quote records returned;
- later comparison with custom POST screeners.

### Fundamentals Timeseries

- exact metric names;
- exact date range;
- result blocks by metric;
- timestamps and `asOfDate`;
- `reportedValue.raw` and formatted values;
- differences among annual, quarterly, trailing, and valuation metrics;
- behavior when long metric lists require chunking.

### Options

- available expiration dates;
- underlying quote object;
- option-chain result;
- calls and puts;
- contract symbol identity;
- expiration, strike, last trade date, prices, volume, open interest, and implied volatility;
- behavior for symbols without listed options and invalid expiration dates.

## Evidence directory produced by the verification script

```text
captures/local/
  <UTC>_seven-endpoint-verification/
    verification-manifest.json
    raw/
      quote.raw.json
      chart.raw.json
      quote-summary.raw.json
      search.raw.json
      screener.raw.json
      fundamentals-timeseries.raw.json
      options.raw.json
    metadata/
      quote.meta.json
      chart.meta.json
      quote-summary.meta.json
      search.meta.json
      screener.meta.json
      fundamentals-timeseries.meta.json
      options.meta.json
    errors/
```

A response is saved even when it is an HTTP error or malformed JSON. The `.raw.json` filename describes the intended content; the metadata records the actual content type and parse status.

## First-pass acceptance criteria

An endpoint passes the first live-verification stage when:

- an HTTP response is received;
- the response body is saved unchanged;
- SHA-256 and byte count are recorded;
- no cookie or crumb is persisted;
- the response parses as JSON;
- the expected top-level response object exists; and
- the endpoint-specific result is nonempty or is clearly classified as a legitimate empty result.

A 401, 403, or 429 is evidence, not a reason to discard the sample. It should trigger review of session strategy, request parameters, and retry behavior.

## Follow-on controlled tests

After all seven first-pass requests are captured:

1. repeat each endpoint without adding the crumb;
2. repeat each endpoint without prepared Yahoo cookies;
3. test query1 versus query2 where both hosts appear usable;
4. vary one parameter at a time;
5. establish endpoint-specific empty-result and invalid-request behavior;
6. begin security-type and exchange coverage; and
7. then begin scheduled market-state transition studies.
