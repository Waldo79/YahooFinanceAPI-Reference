# Capture format specification

## Status

Draft specification for v0.3.9. The first executable capture utility is planned for a later release.

## Objective

Capture Yahoo Finance responses in a repeatable form suitable for evidence, comparison, and later automated analysis.

## Input

The first utility should accept a table containing up to 30 symbols.

Minimum columns:

| Column | Required | Description |
|---|---|---|
| `symbol` | Yes | Exact Yahoo symbol to request |
| `enabled` | No | Whether the row should be included |
| `project_security_type` | No | Stock, ETF, CEF, mutual fund, futures, index, crypto, or forex |
| `endpoint_id` | No | Endpoint to use; otherwise use the run default |
| `notes` | No | User annotation |

## Request method

- Process symbols sequentially in a loop.
- Make one request per symbol for the first implementation.
- Preserve request order.
- Use a configurable pause between requests.
- Use conservative retry and backoff behavior.
- Do not attempt to bypass authentication, throttling, rate limits, or access controls.
- Do not claim that 30 symbols are always permitted by Yahoo; 30 is the project's input-table limit.

## Raw response preservation

The exact response body must be saved without adding a text header to it.

Adding a human-readable header directly to raw JSON would make the file invalid JSON. The symbol and UTC timestamps must instead be recorded in:

1. the filename;
2. a metadata sidecar file; and
3. the normalized human-readable output.

Example raw filename:

```text
MSFT_quote_2026-07-10T22-05-30.123Z.raw.json
```

Example metadata filename:

```text
MSFT_quote_2026-07-10T22-05-30.123Z.meta.json
```

## Run folder

```text
captures/
  2026-07-10T22-05-00.000Z_run-0001/
    run-manifest.json
    raw/
    metadata/
    normalized/
    errors/
```

## Mandatory per-request metadata

```json
{
  "capture_schema_version": "0.3.9",
  "run_id": "2026-07-10T22-05-00.000Z_run-0001",
  "sequence": 1,
  "utility_version": "TBD",
  "endpoint_id": "quote",
  "requested_symbol": "MSFT",
  "returned_symbol": "MSFT",
  "project_security_type": "Stock",
  "requested_at_utc": "2026-07-10T22:05:30.000Z",
  "response_received_at_utc": "2026-07-10T22:05:30.123Z",
  "elapsed_ms": 123,
  "http_status": 200,
  "content_type": "application/json",
  "response_bytes": 0,
  "raw_response_file": "raw/MSFT_quote_2026-07-10T22-05-30.123Z.raw.json",
  "raw_response_sha256": "",
  "result_classification": "SUCCESS_RESULT_RETURNED",
  "error_message": null,
  "request_url_redacted": ""
}
```

## Security and privacy

- Do not write cookies, crumbs, authorization values, or personal session data into public evidence.
- Redact sensitive query parameters and headers.
- Preserve enough non-sensitive request detail to reproduce the observation.
- Clearly label evidence that cannot be publicly reproduced without session data.

## Error handling

An error record should still include the symbol, endpoint, timestamps, elapsed time, HTTP status when available, and the exact error text.

Do not replace failed captures with an empty JSON object.

## Run manifest

The run manifest should list:

- run ID;
- utility version;
- capture schema version;
- input filename;
- endpoint defaults;
- symbol count requested;
- request order;
- pause and retry settings;
- run start and completion UTC;
- total successes, empty results, missing symbols, HTTP errors, parse errors, and other classifications; and
- one entry for every attempted request.

## Integrity

Calculate SHA-256 for each raw response after writing it. The hash allows later verification that the evidence was not altered.
