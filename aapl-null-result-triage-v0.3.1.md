# AAPL null-result triage — v0.3.1

Date: 2026-07-09

## Observation

The user reported that the URLs from the prior capture session returned null results for:

- `search?q=AAPL`
- `chart/AAPL`
- predefined screener URLs

This should **not** be treated as proof that `AAPL` is an invalid Yahoo symbol.

## Key interpretation

`AAPL` is symbol-specific for quote, chart, options, and quoteSummary.

The predefined screener URLs are **not** symbol-specific. A screener null result therefore points to endpoint/request/access behavior, not an AAPL symbol-recognition failure.

A same-session failure across search, chart, and screener should be classified first as a capture health issue, access issue, throttling issue, or response-shape anomaly until HTTP status and raw response diagnostics prove otherwise.

## Required capture diagnostics

Every capture should now record:

1. request timestamp in UTC
2. final encoded request URL
3. requested symbol/query
4. HTTP status code
5. response Content-Type
6. parsed Yahoo error object, if present
7. result count for the expected response path
8. normalized `result_state`

## Decision tree

1. If HTTP status is `429`, classify as `HTTP_ERROR_429`.
   - Do not mark the symbol invalid.
   - Back off and retry later.

2. If HTTP status is `401` or `403`, classify as access/session failure.
   - Do not emit financial rows.
   - Preserve content type and safe body prefix.

3. If HTTP status is 2xx and Yahoo error object is populated:
   - Store the error code and description.
   - Cross-check with quote/search before deciding whether the symbol is invalid.

4. If HTTP status is 2xx, error is null, and expected result count is zero:
   - Classify as `EMPTY_RESULT`.
   - Cross-check `quote?symbols=AAPL`.
   - For search, retry with `q=Apple`.
   - For chart, capture `chart.error.code` and `chart.error.description`.

5. If response parses but expected top-level result is null:
   - Classify as `NULL_RESULT`.
   - Preserve raw JSON.
   - Queue schema/access review.

## Recommended AAPL re-test sequence

1. `quote?symbols=AAPL`
2. `search?q=AAPL`
3. `search?q=Apple`
4. `chart/AAPL?range=1mo&interval=1d&events=div%2Csplits`
5. one predefined screener ID only, such as `most_actives`

If quote fails with 429/403 or non-JSON response, stop and classify the whole run as access/rate-limited.
