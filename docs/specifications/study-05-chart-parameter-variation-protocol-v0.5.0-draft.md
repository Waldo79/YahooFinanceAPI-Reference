# Study 05 Chart Parameter Variation Protocol — v0.5.0 Draft

## Status

Implementation-ready controlled protocol for varying Yahoo Finance Chart request parameters
while holding the subject, endpoint host, session mode, request order, evidence rules, and
capture date constant.

The default study performs:

```text
1 subject × 9 configured Chart requests = 9 evidence records
```

## Research question

When `AAPL`, the Chart endpoint host, and the anonymous cookie-plus-crumb session are held
constant, how do supported Chart request parameters alter response coverage, timestamp
sequences, indicator arrays, event objects, trading-period structures, and field occurrence?

## Why Chart is next

The v0.5.0 architecture lists endpoint parameter variation immediately after the completed
security-type, exchange, and market-state baselines. It also lists Chart as the first
non-Quote endpoint in the recommended adapter sequence. Study 01 established that the
Chart request pattern succeeds and returns the expected top-level `chart` object.

Study 05 therefore begins the parameter-variation stage with the endpoint whose behavior is
most directly controlled by documented range, interval, pre/post, event, and period inputs.

## Subject and session control

- Subject: `AAPL`
- Endpoint: `https://query2.finance.yahoo.com/v8/finance/chart/AAPL`
- Method: `GET`
- Session mode: anonymous cookie plus crumb
- Expected top-level object: `chart`
- Request order: configured variant order
- Normal inter-request pause: zero milliseconds unless overridden

Chart succeeded without prepared session state in Study 01. Cookie-plus-crumb is retained
here as a cross-study control, not as a claim that Chart requires a crumb.

## Request variants

| Order | Variant | Controlled purpose |
|---:|---|---|
| 1 | `baseline-5d-1d` | Establish the 5-day, daily-bar baseline |
| 2 | `events-omitted` | Remove the `events` parameter |
| 3 | `events-div-only` | Change the requested event set to dividends only |
| 4 | `range-1mo-1d` | Change only `range` from `5d` to `1mo` |
| 5 | `interval-1h-5d` | Change only `interval` from `1d` to `1h` |
| 6 | `interval-5m-5d` | Establish the five-minute intraday control |
| 7 | `include-prepost-true-5m` | Change only `includePrePost` relative to the five-minute control |
| 8 | `interval-1m-5d` | Change only `interval` from `1d` to `1m` |
| 9 | `explicit-period-5d-1d` | Replace `range` with dynamic `period1` and `period2` |

The explicit-period request is a request-form control. It intentionally changes the selector
form rather than exactly one parameter.

## Evidence requirements

For every request, preserve:

- the final response body byte-for-byte;
- raw-response SHA-256 and byte count;
- canonical parsed-JSON SHA-256 when valid JSON is returned;
- the exact non-sensitive request parameters and their canonical fingerprint;
- UTC request and response timestamps;
- HTTP status, content type, attempts, latency, and any retry or authentication refresh;
- a redacted request URL;
- the resolved study definition used for the run; and
- a metadata sidecar exactly matching the corresponding manifest entry.

Cookie and crumb values remain in memory and must never be written to evidence.

## Chart measurements

For the first returned result object, record:

- result count and Chart error presence;
- metadata field names and metadata key-set hash;
- timestamp count, first timestamp, last timestamp, and timestamp-sequence hash;
- quote indicator field names and key-set hash;
- maximum quote-array length;
- adjusted-close array length;
- null values across quote and adjusted-close arrays;
- event types, event count, and stable event-identity hash;
- current-trading-period group and period counts;
- returned `range`, `dataGranularity`, `exchangeName`, `instrumentType`, currency, and time zone;
- response size and raw/canonical hashes.

## Comparison outputs

The run produces:

```text
comparison/chart-parameter-results.csv
comparison/chart-controlled-comparison.csv
```

`chart-parameter-results.csv` contains one row per request. The controlled-comparison table pairs every variant with its configured control and
records timestamp overlap and equality flags for timestamp sequences, metadata key sets,
indicator key sets, event identities, and canonical JSON.

Canonical JSON equality is informational only. Live quote metadata may change between
sequential requests even when historical bars are otherwise equivalent.

## Acceptance criteria

The implementation run passes structural review when:

1. all nine configured requests produce evidence records;
2. every raw response and metadata sidecar is written;
3. all byte counts and SHA-256 values recompute correctly;
4. every response parses as JSON or is explicitly retained and classified as an error;
5. the expected top-level `chart` object is evaluated for every sample;
6. comparison tables contain nine result rows and nine controlled-comparison rows;
7. the resolved definition and manifest contain no absolute owner paths;
8. no cookie, crumb, authorization value, or other sensitive session value is persisted; and
9. the existing endpoint analyzer can process the run without modification.

A rejected interval or selector is still valid evidence if the complete response and metadata
are preserved and the result is accurately classified. It should not be silently removed from
the study.

## Interpretation limits

This is a one-subject, one-date baseline. Parameter effects may differ by security type,
exchange, market state, data availability, corporate events, and Yahoo backend behavior.
A later replication should add at least one ETF, index, fund, and continuous-market subject
after the AAPL parameter behavior is validated.
