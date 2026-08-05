# Yahoo Finance Fast-mode Capture Engine

Version: `0.5.0-candidate.3`

This is a separate high-volume utility. It does **not** replace or modify
`tools/capture-utility/yahoo_capture.py` v0.4.3.

Candidate.3 keeps raw capture runs outside the synchronized Git repository by
default and blocks repository-contained output paths before network access.

## Purpose

The utility processes thousands of prepared symbol rows through the fastest
request shape appropriate to each supported Yahoo endpoint family:

- **Quote** — multiple symbols per request, initially 100 per batch.
- **QuoteSummary / Fundamental** — concurrent one-symbol requests.
- **Chart snapshot** — concurrent one-symbol requests using `5d` / `1d`.
- **Options snapshot** — concurrent one-symbol requests using Yahoo's default
  or nearest chain response.

Long History remains a separate resumable archive workload because years of
bars would dominate run time and storage. Search and Screener are discovery
operations and are not repeated for every known symbol.

## Prepared input

The included request list contains:

- 1,537 unique symbols;
- 10 intentional second occurrences used as duplicate controls; and
- 1,547 total request rows.

The duplicate controls are AAPL, SPY, PDI, VTSAX, ^GSPC, GC=F, EURUSD=X,
BTC-USD, RY.TO, and INGA.AS.

## Install candidate.3

Extract the ZIP directly into the repository root, preserving folders. It
replaces the candidate Fast-mode utility, its tests, README, and `.gitignore`,
and adds external-storage documentation and a migration script.

## Configure external storage

For a repository located at:

```text
C:\Users\<name>\Downloads\YAHOO\Code\YahooFinanceAPI-Reference-main
```

the safe automatic default is:

```text
C:\Users\<name>\Downloads\YAHOO\Captures\fast-mode
```

Make the destination explicit on this computer:

```text
py tools\capture-utility\yahoo_fast_capture.py --configure-output-root "%USERPROFILE%\Downloads\YAHOO\Captures\fast-mode"
```

This creates the ignored machine-local file:

```text
config\local\fast_mode_local.json
```

Verify the resolved destination without reading the symbol input or contacting
Yahoo:

```text
py tools\capture-utility\yahoo_fast_capture.py --show-output-root
```

Resolution order:

1. `--output-root PATH`;
2. `YAHOO_FAST_CAPTURE_ROOT` environment variable;
3. ignored local config;
4. safe external default.

`--outdir` remains an alias for backward compatibility. Any destination inside
the synchronized repository is rejected.

## Migrate existing local runs safely

The included script copies the legacy ignored capture folder to the external
archive, verifies every copied file by size and SHA-256, and deletes nothing:

```text
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\migrate-fast-mode-captures.ps1
```

Review the external copy before manually removing the old source. See
`docs/high-volume/EXTERNAL_CAPTURE_STORAGE.md`.

## Offline verification and dry run

Run this one command from the repository root:

```text
py -m py_compile tools\capture-utility\yahoo_fast_capture.py && py -m pytest -q && py tools\capture-utility\yahoo_fast_capture.py --show-output-root && py tools\capture-utility\yahoo_fast_capture.py --input data\high-volume\fast_mode_request_list_1547.csv --dry-run
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

The dry-run JSON also reports the external output root, its resolution source,
and `repository_output_allowed: false`.

Individual Quote retests are added only when a batched response omits a
requested symbol.

## Smoke run

The deterministic smoke selector includes both occurrences of all 10 duplicate
controls plus 10 additional symbols. It generates 91 initial tasks before any
Quote retests.

```text
py tools\capture-utility\yahoo_fast_capture.py --input data\high-volume\fast_mode_request_list_1547.csv --smoke
```

Before network work starts, the utility displays the resolved external capture
root and write-tests that directory.

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

These are experimental operating values, not documented Yahoo limits.

## Override storage for one run

```text
py tools\capture-utility\yahoo_fast_capture.py --input data\high-volume\fast_mode_request_list_1547.csv --output-root "D:\Yahoo-Captures\fast-mode"
```

The override must remain outside the repository.

## Run one endpoint family

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

## Resume an interrupted external run

Every completed task is appended to `checkpoint.jsonl`. Resume using the exact
external run folder:

```text
py tools\capture-utility\yahoo_fast_capture.py --input data\high-volume\fast_mode_request_list_1547.csv --resume-run "%USERPROFILE%\Downloads\YAHOO\Captures\fast-mode\<run-folder>"
```

The resume folder must be outside the repository and must contain
`checkpoint.jsonl`. Use the same input file and endpoint settings.

## Symbol-level results do not stop the run

The engine distinguishes HTTP/network failure from normal symbol-level
outcomes, including:

- `SYMBOL_NOT_AVAILABLE`
- `NO_FUNDAMENTALS_AVAILABLE`
- `NOT_OPTIONABLE_OR_NO_CHAIN`
- `REQUESTED_SYMBOL_MISSING_FROM_RESULT`
- `BATCH_OMISSION_INDIVIDUAL_SUCCESS`

An unavailable symbol, a symbol without fundamental data, or a symbol without
an option chain is recorded and processing continues.

Quote batches are checked row by row. Any omitted symbol is automatically
requested individually. A successful individual retest is classified as
`BATCH_OMISSION_INDIVIDUAL_SUCCESS`.

## External output layout

```text
...\YAHOO\Captures\fast-mode\
  <timestamp>_fast-run\
    run-manifest.json
    run-summary.txt
    checkpoint.jsonl
    raw\
      quote\
      quoteSummary\
      chart\
      options\
    metadata\
      quote\
      quoteSummary\
      chart\
      options\
    summary\
      request-results.csv
```

Raw HTTP response bytes are retained externally. Request URLs are stored only
with the crumb redacted. Checkpoints remain with the external run so resume is
self-contained.

The manifest records the storage policy and run-folder name but does not
persist the absolute local output path.

## Reporting

The text summary reports separate totals:

- **Tasks** — actual HTTP/network operations, such as one batched Quote task.
- **Per-symbol results** — endpoint outcomes assigned to each input row.

For Options, a valid response containing only a quote object but no expiration
dates or option sets is recorded as `NOT_OPTIONABLE_OR_NO_CHAIN`.

## Privacy

Cookie and crumb values remain in memory and are not written to raw files,
metadata, checkpoints, manifests, summaries, or request-result CSV files.
The authentication-bearing APP logs are not included in the repository.

The real `config/local/fast_mode_local.json` is ignored because it can contain
a local username or drive path. The tracked example contains placeholders only.

## Adaptive behavior

The utility uses an endpoint-wide shared backoff gate after HTTP 429 and retries
temporary HTTP/network failures. It does not yet resize a running thread pool
dynamically; it pauses workers collectively and continues with the configured
concurrency after the backoff period.

## Validation status and limitations

- The 1,547-row universe completed a live validation with 4,657 initial tasks.
- Five Chart requests that remained HTTP 429 after the main run were recovered
  in a sequential five-symbol retry run.
- Candidate.3 storage changes are covered by offline tests; no new Yahoo request
  behavior is introduced.
- Long History is not included.
- Full option chains across every expiration are not included.
- Search, Screener, market summaries, sectors, industries, news, and WebSocket
  streaming are not included.
- The existing v0.4.3 validator does not validate this Fast-mode run format.

## Exit codes

- `0` — completed with no review-classified request results.
- `2` — completed, but one or more request results require review.
- `1` — input, session, storage, or operating error.
- `130` — interrupted by the user; resume from the external run folder.
