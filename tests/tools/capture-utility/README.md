# Yahoo Finance Capture Utility — v0.4.3

This utility captures and validates evidence from the Yahoo Finance **Quote** endpoint. It uses only Python 3.10+ standard-library modules and does not require a Yahoo username or password.

v0.4.3 sets the normal inter-symbol pause to 0 milliseconds. The v0.4.2 portable path handling and completed-run validator, and the v0.4.1 anonymous in-memory cookie-and-crumb session, remain unchanged.

## Capture workflow

For each enabled symbol, in table order, the utility:

1. establishes an anonymous Yahoo cookie session;
2. obtains a temporary Yahoo crumb;
3. sends one Quote request;
4. refreshes the anonymous session once if Yahoo returns HTTP 401 or 403;
5. preserves the final response body byte-for-byte;
6. computes a SHA-256 digest;
7. writes a metadata sidecar and normalized text;
8. updates the run manifest; and
9. displays a progress line such as:

```text
[01/16] AAPL ... HTTP 200 SUCCESS_RESULT_RETURNED
```

Requests remain sequential. By default, v0.4.3 adds no fixed delay between completed symbols.

## Session privacy

Cookie and crumb values:

- exist only in memory while the utility is running;
- are never written to the manifest, metadata, raw evidence, normalized output, or error files;
- are redacted from stored request URLs; and
- are discarded when the process ends.

## Windows: expected directory and command

For the clearest workflow, open Command Prompt from the repository root, for example:

```text
C:\Users\<name>\Downloads\YahooFinanceAPI-Reference-main
```

The file being run is expected at:

```text
tools\capture-utility\yahoo_capture.py
```

### Dry run

Run from the repository root:

```text
py tools\capture-utility\yahoo_capture.py --dry-run
```

### Full capture

Run from the repository root:

```text
py tools\capture-utility\yahoo_capture.py
```

The normal v0.4.3 pacing default is:

```text
0 milliseconds between symbols
```

The default destination is fixed at repository-root:

```text
captures\local
```

The destination does not change when the command is launched from another directory.

## Input table

Edit `tools/capture-utility/symbols.csv`. Supported columns are:

| Column | Required | Meaning |
|---|---|---|
| `symbol` | Yes | Exact Yahoo symbol |
| `enabled` | No | `yes/no`, `true/false`, `1/0`, or blank for enabled |
| `project_security_type` | No | Project category such as Stock, ETF, or Index |
| `endpoint_id` | No | Must be `quote` in v0.4.3 |
| `notes` | No | User annotation copied into metadata |

At most 30 rows may be enabled. Full URLs are rejected as symbols.

## Quick custom capture

Run from the repository root:

```text
py tools\capture-utility\yahoo_capture.py --symbols AAPL,MSFT,SPY
```

## Validate a completed run

Run from the repository root. Replace `<run-folder>` with the dated folder name:

```text
py tools\capture-utility\yahoo_capture.py --validate-run captures\local\<run-folder>
```

Validation checks include:

- manifest readability and completion state;
- request count, order, symbols, and sequences;
- metadata sidecars matching manifest entries;
- raw-response SHA-256 and byte counts;
- referenced, missing, duplicate, unsafe, and extra files;
- normalized and error-file references;
- summary counts versus request classifications;
- unredacted crumb, cookie, and authorization values;
- legacy absolute local paths; and
- known versus unmapped JSON paths.

Two reports are written into the run folder:

```text
run-validation.json
run-validation.txt
```

Validation status is:

- `PASS` — no errors or warnings;
- `PASS_WITH_WARNINGS` — no errors, but review warnings exist; or
- `FAIL` — one or more errors exist.

Unmapped JSON paths are informational observations. They are not automatically promoted into the master field database.

## Request pacing and retry controls

The normal inter-symbol pause is 0 milliseconds. To add a fixed pause, pass any nonnegative millisecond value:

```text
py tools\capture-utility\yahoo_capture.py --pause-ms 1000
```

A smaller explicit override also remains valid:

```text
py tools\capture-utility\yahoo_capture.py --pause-ms 25
```

The remaining defaults are up to three attempts, retry delays of two and five seconds, and a 30-second per-attempt timeout:

```text
py tools\capture-utility\yahoo_capture.py --pause-ms 0 --max-attempts 3 --backoff-seconds 2,5 --timeout 30
```

A 0 ms normal pause does not disable safeguards. Network failures and HTTP 429, 500, 502, 503, and 504 use the retry policy. HTTP 401 or 403 triggers at most one anonymous-session refresh for the entire run.

The July 16, 2026 16-symbol stopwatch runs completed successfully in 3.82 seconds at 0 ms, 4.31 seconds at 25 ms, and 3.65 seconds when 0 ms was repeated. These results establish the project baseline but cannot guarantee future Yahoo behavior.

## Diagnostic unauthenticated mode

```text
py tools\capture-utility\yahoo_capture.py --auth-mode none --symbols AAPL
```

This diagnostic mode may return HTTP 401.

## Development tests

`pytest` is needed only for development testing, not normal capture or validation.

Install it once:

```text
py -m pip install pytest
```

Run tests from the repository root:

```text
py -m pytest -q
```

Compile-check the utility:

```text
py -m py_compile tools\capture-utility\yahoo_capture.py
```

## Output layout

```text
captures/local/
  2026-07-11T20-00-00.000Z_run-0001/
    run-manifest.json
    run-validation.json       # after --validate-run
    run-validation.txt        # after --validate-run
    raw/
    metadata/
    normalized/
    errors/
```

Raw response files are never reformatted. Manifest input and master-database paths are stored as repository-relative references when possible. External inputs are recorded by filename only.

## Exit codes

Capture:

- `0` when every requested symbol returned a matching result;
- `2` when the run completed but one or more results require review; and
- `1` for an operating-system failure.

Validation:

- `0` when no validation errors are found, including `PASS_WITH_WARNINGS`;
- `2` when validation status is `FAIL`; and
- `1` for an operating-system failure.

## Current scope

v0.4.3 captures and validates the Quote endpoint only. Chart, QuoteSummary, Search, Screener, Options, comparison utilities, and scheduled capture remain later stages.
