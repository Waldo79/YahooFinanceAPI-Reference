# Yahoo Finance Capture Utility — v0.4.1

This utility implements the v0.3.9 evidence-capture format for the Yahoo Finance **Quote** endpoint. v0.4.1 adds an anonymous, in-memory Yahoo cookie-and-crumb session after the first live v0.4.0 run returned HTTP 401 for all 16 symbols.

It uses only Python 3.10+ standard-library modules. It does not require a Yahoo username or password.

## What it does

For each enabled symbol, in table order, the utility:

1. establishes an anonymous Yahoo cookie session;
2. obtains a temporary Yahoo crumb;
3. sends one Quote request;
4. refreshes the anonymous session once if Yahoo returns HTTP 401 or 403;
5. records UTC request and response times and elapsed milliseconds;
6. preserves the final response body byte-for-byte;
7. computes a SHA-256 digest;
8. writes a metadata sidecar;
9. classifies returned, empty, omitted-symbol, HTTP, rate-limit, network, and parse results;
10. writes deterministic normalized text for valid JSON; and
11. updates a run manifest.

The utility does not bypass authentication, throttling, rate limits, or other access controls.

## Session privacy

Cookie and crumb values:

- exist only in memory while the utility is running;
- are never written to the manifest, metadata, raw evidence, normalized output, or error files;
- are redacted from stored request URLs; and
- are discarded when the process ends.

The manifest stores only non-sensitive facts such as the session strategy, whether a crumb was present, cookie count, and refresh count.

## Input table

Edit `symbols.csv`. The supported columns are:

| Column | Required | Meaning |
|---|---|---|
| `symbol` | Yes | Exact Yahoo symbol |
| `enabled` | No | `yes/no`, `true/false`, `1/0`, or blank for enabled |
| `project_security_type` | No | Project category such as Stock, ETF, or Index |
| `endpoint_id` | No | Must be `quote` in v0.4.1 |
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

The default authentication mode is `anonymous-crumb`.

## Diagnostic unauthenticated mode

To reproduce a bare Quote request without cookie-and-crumb setup:

```bash
python tools/capture-utility/yahoo_capture.py --auth-mode none --symbols AAPL
```

This mode is for diagnosis and may return HTTP 401.

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

Network failures and HTTP 429, 500, 502, 503, and 504 use the normal retry policy. HTTP 401 or 403 triggers at most one anonymous-session refresh for the entire run.

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

v0.4.1 captures the Quote endpoint only. Chart, QuoteSummary, Search, Screener, Options, comparison utilities, and browser-login support remain later stages.
