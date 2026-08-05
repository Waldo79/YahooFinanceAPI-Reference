# Yahoo Finance Fast-mode Capture Engine

Version: `0.5.0-candidate.2`

This is a separate high-volume candidate utility. It does **not** replace or
modify `tools/capture-utility/yahoo_capture.py` v0.4.3.

## Purpose

The candidate processes thousands of prepared symbol rows through the fastest
request shape appropriate to each supported Yahoo endpoint family:

- **Quote** — multiple symbols per request, initially 100 per batch.
- **QuoteSummary / Fundamental** — concurrent one-symbol requests.
- **Chart snapshot** — concurrent one-symbol requests using `5d` / `1d`.
- **Options snapshot** — concurrent one-symbol requests using Yahoo's default
  or nearest chain response.

Long History is intentionally excluded because years of bars would dominate
run time and storage. Search and Screener are discovery operations and are not
repeated for every known symbol.

## Prepared input

The included request list contains:

- 1,537 unique symbols;
- 10 intentional second occurrences used as duplicate controls; and
- 1,547 total request rows.

The duplicate controls are AAPL, SPY, PDI, VTSAX, ^GSPC, GC=F, EURUSD=X,
BTC-USD, RY.TO, and INGA.AS.

## Install the candidate

Extract the ZIP directly into the repository root, preserving folders.
Existing v0.4.3 files are not replaced.

## Offline verification and dry run

Run this one command from the repository root:

```text
py -m py_compile tools\capture-utility\yahoo_fast_capture.py && py -m pytest -q && py tools\capture-utility\yahoo_fast_capture.py --input data\high-volume\fast_mode_request_list_1547.csv --dry-run
```

Expected Fast-mode dry-run totals:

```text
Input rows:                    1,547
Unique symbols:               1,537
Intentional duplicates:          10
Initial Quote tasks:              16
Initial Fundamental tasks:     1,547
Initial Chart tasks:           1,547
Initial Options tasks:         1,547
Initial total tasks:           4,657
```

Individual Quote retests are added only when a batched response omits a
requested symbol.

## Candidate.2 confirmation test: 30-row smoke run

Candidate.1 completed the 30-row live smoke run successfully. Candidate.2
changes reporting and classification logic, so repeat the same short smoke run
once before the complete capture.

The smoke selector includes both occurrences of all 10 duplicate-control
symbols, plus 10 additional symbols. It generates 91 initial tasks before any
Quote retests.

```text
py tools\capture-utility\yahoo_fast_capture.py --input data\high-volume\fast_mode_request_list_1547.csv --smoke
```

Do not begin the complete 1,547-row run until the candidate.2 smoke summary
shows separate **Tasks** and **Per-symbol results** totals and the run completes
without transport failures.

## Complete Fast-mode run

```text
py tools\capture-utility\yahoo_fast_capture.py --input data\high-volume\fast_mode_request_list_1547.csv
```

Initial defaults:

```text
Quote batch size:             100 symbols
Quote concurrency:              4 workers
Fundamental concurrency:       10 workers
Chart concurrency:             10 workers
Options concurrency:            5 workers
Timeout:                       20 seconds per attempt
Maximum attempts:               3
Retry delays:                   1 and 3 seconds
Chart range / interval:        5d / 1d
```

These are experimental starting values, not documented Yahoo limits.

## Run one endpoint family

Examples:

```text
py tools\capture-utility\yahoo_fast_capture.py --input data\high-volume\fast_mode_request_list_1547.csv --smoke --endpoints quote
```

```text
py tools\capture-utility\yahoo_fast_capture.py --input data\high-volume\fast_mode_request_list_1547.csv --smoke --endpoints quoteSummary,chart
```

Endpoint identifiers are case-sensitive:

```text
quote
quoteSummary
chart
options
```

## Resume an interrupted run

Every completed task is appended to `checkpoint.jsonl`. Resume using the exact
existing run folder:

```text
py tools\capture-utility\yahoo_fast_capture.py --input data\high-volume\fast_mode_request_list_1547.csv --resume-run captures\local\fast-mode\<run-folder>
```

The same input file and endpoint settings should be used when resuming.

## Symbol-level results do not stop the run

The engine distinguishes HTTP/network failure from a normal symbol-level
outcome. Examples include:

- `SYMBOL_NOT_AVAILABLE`
- `NO_FUNDAMENTALS_AVAILABLE`
- `NOT_OPTIONABLE_OR_NO_CHAIN`
- `REQUESTED_SYMBOL_MISSING_FROM_RESULT`
- `BATCH_OMISSION_INDIVIDUAL_SUCCESS`

An unavailable symbol, a symbol without fundamental data, or a symbol without
an option chain is recorded and processing continues.

Quote batches are checked row by row. Any omitted symbol is automatically
requested individually. A successful individual retest is classified as
`BATCH_OMISSION_INDIVIDUAL_SUCCESS` rather than being silently treated as an
invalid symbol.

## Output layout

```text
captures/local/fast-mode/
  <timestamp>_fast-run/
    run-manifest.json
    run-summary.txt
    checkpoint.jsonl
    raw/
      quote/
      quoteSummary/
      chart/
      options/
    metadata/
      quote/
      quoteSummary/
      chart/
      options/
    summary/
      request-results.csv
```

Raw HTTP response bytes are retained. Request URLs are stored only with the
crumb redacted.

The text summary reports two separate totals:

- **Tasks** — actual HTTP/network operations, such as one batched Quote task.
- **Per-symbol results** — endpoint outcomes assigned to each input row.

For Options, a valid response containing only a quote object but no expiration
dates or option sets is recorded as `NOT_OPTIONABLE_OR_NO_CHAIN`, not as a
successful option-chain result.

## Privacy

Cookie and crumb values remain in memory and are not written to raw files,
metadata, checkpoints, manifests, summaries, or request-result CSV files.
The authentication-bearing APP logs are not included in this package.

## Adaptive behavior

The candidate uses an endpoint-wide shared backoff gate after HTTP 429 and
retries temporary HTTP/network failures. This candidate does not yet resize a
running thread pool dynamically; it pauses workers collectively and continues
with the configured concurrency after the backoff period.

## Candidate status and limitations

- All included tests are offline and use simulated Yahoo responses.
- The candidate has not been live-tested by the model environment.
- Yahoo endpoints are unofficial and may change.
- Long History is not included.
- Full option chains across every expiration are not included.
- Search, Screener, market summaries, sectors, industries, news, and WebSocket
  streaming are not included in this first high-volume candidate.
- The existing v0.4.3 validator does not validate this new Fast-mode run format.
  The candidate writes its own manifest, checkpoint, metadata, hashes, and
  request-result accounting; a dedicated v0.5 validator is a later step.

## Exit codes

- `0` — completed with no review-classified request results.
- `2` — completed, but one or more request results require review.
- `1` — input, session, or operating error.
- `130` — interrupted by the user; resume from the run folder.
