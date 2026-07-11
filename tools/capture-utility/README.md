# Yahoo Finance Capture Utility — v0.4.0

This is the first working evidence-capture utility for the project. It implements the v0.3.9 capture-format specification for the Yahoo Finance **Quote** endpoint.

It uses only Python 3.10+ standard-library modules.

## What it does

For each enabled symbol, in table order, the utility:

1. sends one Quote request;
2. records UTC request and response times and elapsed milliseconds;
3. preserves the response body byte-for-byte;
4. computes a SHA-256 digest;
5. writes a metadata sidecar;
6. classifies returned, empty, omitted-symbol, HTTP, rate-limit, network, and parse results;
7. writes deterministic normalized text for valid JSON; and
8. updates a run manifest.

The utility does not bypass authentication, throttling, rate limits, or other access controls.

## Input table

Edit `symbols.csv`. The supported columns are:

| Column | Required | Meaning |
|---|---|---|
| `symbol` | Yes | Exact Yahoo symbol |
| `enabled` | No | `yes/no`, `true/false`, `1/0`, or blank for enabled |
| `project_security_type` | No | Project category such as Stock, ETF, or Index |
| `endpoint_id` | No | Must be `quote` in v0.4.0 |
| `notes` | No | User annotation copied into metadata |

At most 30 rows may be enabled. Full URLs are rejected as symbols.

## Dry run

Validate the table and display request order without contacting Yahoo:

```bash
python tools/capture-utility/yahoo_capture.py --dry-run
```

## Capture the table

```bash
python tools/capture-utility/yahoo_capture.py
```

## Quick custom capture

```bash
python tools/capture-utility/yahoo_capture.py --symbols AAPL,MSFT,SPY
```

## Conservative request controls

Defaults are one second between symbols, up to three attempts, retry delays of two and five seconds, and a 30-second timeout.

```bash
python tools/capture-utility/yahoo_capture.py \
  --pause-ms 1500 \
  --max-attempts 3 \
  --backoff-seconds 3,8 \
  --timeout 30
```

Retries are limited to network failures and HTTP 429, 500, 502, 503, and 504 responses.

## Output

Each run is written under `captures/local/`:

```text
captures/local/
  2026-07-11T20-00-00.000Z_run-0001/
    run-manifest.json
    raw/
    metadata/
    normalized/
    errors/
```

Raw response files are never reformatted. Capture context is stored in the filename, sidecar, normalized output, and manifest.

The command returns:

- exit code `0` when every requested symbol returned a matching result;
- exit code `2` when the run completed but one or more results require review; and
- exit code `1` for an operating-system failure. Invalid command input is reported by the command-line parser.

## Current scope

v0.4.0 captures the Quote endpoint only. Chart, QuoteSummary, Search, Screener, Options, and comparison utilities remain later stages.
