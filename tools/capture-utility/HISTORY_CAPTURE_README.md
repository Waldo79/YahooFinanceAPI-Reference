# Yahoo Finance Long-History Capture and Synchronizer

Capture engine: `0.1.0-candidate.2`
SQLite audit add-on: `0.1.0-candidate.3`

This is a separate component from the Fast-mode snapshot engine. It downloads
long Chart history once, stores compressed raw JSON outside the synchronized
repository, and maintains a persistent SQLite archive for incremental updates
and revision detection.

This candidate supports daily, weekly, and monthly history. Intraday
history and XLSX exports remain separate later components.

## Storage design

For a repository located at:

```text
C:\Users\<name>\Downloads\YAHOO\Code\YahooFinanceAPI-Reference-main
```

the safe default archive is:

```text
C:\Users\<name>\Downloads\YAHOO\Captures\long-history
```

The archive contains:

```text
long-history\
  history.sqlite
  runs\
    <timestamp>_history-run\
      run-plan.json
      run-manifest.json
      run-summary.txt
      checkpoint.jsonl
      raw\chart\*.json.gz
      metadata\chart\*.metadata.json
      summary\symbol-results.csv
      summary\bar-revisions.csv
      summary\event-revisions.csv
```

The SQLite database is the normalized authoritative working archive. The
compressed raw JSON is the original evidence used to reconstruct or reparse
all Yahoo response fields.

## Install

Extract the candidate ZIP directly into the repository root, preserving
folders and replacing the listed files.

## Configure the external archive

```cmd
py tools\capture-utility\yahoo_history_capture.py --configure-output-root "%USERPROFILE%\Downloads\YAHOO\Captures\long-history"
```

This creates the ignored machine-local file:

```text
config\local\history_capture_local.json
```

Verify the destination without contacting Yahoo:

```cmd
py tools\capture-utility\yahoo_history_capture.py --show-output-root
```

Resolution order:

1. `--output-root PATH`
2. `YAHOO_HISTORY_CAPTURE_ROOT`
3. ignored local configuration
4. safe external default

Any output location inside the synchronized repository is rejected.

## Offline verification

```cmd
py -m py_compile tools\capture-utility\yahoo_history_capture.py && py -m pytest -q && py tools\capture-utility\yahoo_history_capture.py --mode baseline --input data\high-volume\fast_mode_request_list_1547.csv --smoke --dry-run
```

The dry run sends no network requests and plans the first five unique symbols.

## Candidate.1 interrupted-run recovery

Do not resume a candidate.1 baseline. Preserve its external archive under a
diagnostic name, create a fresh `long-history` archive, and start candidate.2
from the beginning. Candidate.1 used Yahoo `range=max`, which may silently
return coarser bars, and Windows stopped on a valid pre-1970 timestamp.

Candidate.2 uses explicit period bounds beginning at 1900-01-01 and refuses to
insert a response whose `meta.dataGranularity` differs from the requested
interval.

## First five-symbol baseline

```cmd
py tools\capture-utility\yahoo_history_capture.py --mode baseline --input data\high-volume\fast_mode_request_list_1547.csv --smoke
```

This requests daily history from 1900-01-01 through the selected end date for five symbols and creates the
initial `history.sqlite` database.

## Incremental synchronization

```cmd
py tools\capture-utility\yahoo_history_capture.py --mode sync --input data\high-volume\fast_mode_request_list_1547.csv --smoke
```

For each symbol already in the database, Sync requests from 30 calendar days
before the latest stored bar through the current date. A symbol without a
baseline automatically receives a complete history request.

Change handling:

- new bars are inserted;
- revised bars replace the current row while old and new values are recorded;
- bars missing from a returned comparison range are reported but not deleted;
- corporate-action additions or revisions flag the symbol for a complete
  refresh;
- adjusted-close revisions during Sync also flag the symbol.

## Refresh only flagged symbols

```cmd
py tools\capture-utility\yahoo_history_capture.py --mode refresh-flagged --input data\high-volume\fast_mode_request_list_1547.csv
```

Only symbols whose SQLite state has `full_refresh_required=1` receive a full
history request. A successful full comparison clears the flag unless the full
response still contains a missing-bar condition.

## Full baseline for the prepared universe

```cmd
py tools\capture-utility\yahoo_history_capture.py --mode baseline --input data\high-volume\fast_mode_request_list_1547.csv
```

The input contains duplicate controls for Fast mode. Long History deduplicates
symbols before planning requests, so the prepared file produces 1,537 history
tasks rather than 1,547.

## Weekly or monthly history

```cmd
py tools\capture-utility\yahoo_history_capture.py --mode baseline --interval 1wk --input data\high-volume\fast_mode_request_list_1547.csv
```

```cmd
py tools\capture-utility\yahoo_history_capture.py --mode baseline --interval 1mo --input data\high-volume\fast_mode_request_list_1547.csv
```

Each interval is stored separately in the same database.

## Resume an interrupted run

Use the exact external run folder and the same input, mode, interval, overlap,
and end-date settings:

```cmd
py tools\capture-utility\yahoo_history_capture.py --mode baseline --input data\high-volume\fast_mode_request_list_1547.csv --resume-run "%USERPROFILE%\Downloads\YAHOO\Captures\long-history\runs\<run-folder>"
```

`run-plan.json` contains a task-plan SHA-256. Resume is rejected when the
settings or symbol plan do not match the original run.

## End-date control

The default request includes the current UTC date. A reproducible inclusive
end date can be supplied:

```cmd
py tools\capture-utility\yahoo_history_capture.py --mode baseline --through-date 2026-08-05 --smoke
```

## SQLite tables

- `bars` — current normalized OHLC, adjusted close, and volume rows.
- `bar_revisions` — prior and replacement values for changed bars plus
  missing-from-refresh observations.
- `events` — dividends, splits, and capital-gain events.
- `event_revisions` — old and new corporate-action JSON.
- `symbol_state` — latest bar and full-refresh flags per symbol and interval.
- `runs` — run-level lifecycle records.
- `symbol_runs` — one result row per symbol request.

Stored bars use a unique key of symbol, interval, and UTC timestamp.

## Safety and privacy

- Raw HTTP bodies are compressed as deterministic `.json.gz` files.
- Cookie and crumb values stay in memory.
- Request URLs in metadata have sensitive query values redacted.
- Manifests record folder names but not absolute local paths.
- Missing historical bars are never automatically deleted.
- The database and raw files are blocked from repository-contained paths.

## Read-only SQLite size audit

Candidate.3 adds a separate offline diagnostic utility. It opens the configured
`history.sqlite` with SQLite URI `mode=ro` plus `PRAGMA query_only=ON`. It does
not contact Yahoo and does not run VACUUM, REINDEX, ANALYZE, a WAL checkpoint,
or any schema/data migration.

Run from the repository root after the baseline database is closed:

```cmd
py tools\capture-utility\history_sqlite_audit.py
```

By default, reports are written under the external archive:

```text
...\YAHOO\Captures\long-history\audits\<timestamp>_sqlite-audit\
  audit_report.txt
  audit_manifest.json
  database_summary.csv
  schema_objects.csv
  object_sizes.csv
  tables.csv
  indexes.csv
  columns.csv
  text_storage_sample.csv
```

The default audit performs exact table row counts, samples repeated text
columns, uses SQLite `dbstat` when available for table/index page sizes, and
runs `PRAGMA quick_check`. A complete integrity scan can be requested with
`--integrity-check full`; row-count scans can be skipped with
`--skip-exact-row-counts`.

The audit verifies that the main database file's size and modification time are
unchanged before it writes any reports. Review the report before creating an
optimized copy; the reference archive is never modified by this utility.

## Compact-schema prototype

Candidate.4 adds a copy-based offline experiment that compares the current row layout with a normalized compact layout. It never changes `history.sqlite` and never contacts Yahoo. By default it chooses 100 symbols distributed across the alphabetic source universe, writes two independent subset databases, and verifies ordered SHA-256 values for every copied bar and event.

Run from the repository root after closing capture or SQLite programs:

```cmd
py tools\capture-utility\history_sqlite_compact_prototype.py
```

Output is written under the external archive:

```text
...\YAHOO\Captures\long-history\prototypes\<timestamp>_compact-schema-prototype\
  prototype_report.txt
  prototype_manifest.json
  selected_symbols.csv
  size_comparison.csv
  verification.csv
  legacy_subset.sqlite
  compact_subset.sqlite
```

The compact copy normalizes symbol, interval, run, event-type, source-file, and source-hash values; stores the source hash once as a 32-byte value; derives `datetime_utc` from the integer timestamp; and uses `WITHOUT ROWID` composite-key tables. The date-first bars index is retained for future cross-symbol exports.

Explicit symbols can be tested with `--symbols AAPL,^GSPC,SPY`; the alphabetically distributed subset size can be changed with `--symbol-limit`, up to 500. These databases are disposable comparisons, not replacement archives.

## Candidate boundary

Candidate.2 establishes reliable baseline, Sync, revision, corporate-action, flagged-refresh, checkpoint, and resume behavior. Candidate.3 adds the read-only SQLite audit. Candidate.4 adds only the separate legacy-versus-compact subset experiment. It does not change capture behavior, migrate the authoritative database, delete any data, or build the history XLSX exporter.

## Full compact rebuild

Candidate.5 builds a complete normalized copy of the existing archive after the
candidate.4 sample demonstrated a 72.39% reduction. It opens `history.sqlite`
read-only, copies all logical tables into a separate `history_compact.sqlite`,
checkpoints after each symbol, and verifies ordered counts and SHA-256 values for
every table.

Run from the repository root:

```cmd
py tools\capture-utility\history_sqlite_compact_rebuild.py
```

An interrupted build can be resumed with the exact external folder:

```cmd
py tools\capture-utility\history_sqlite_compact_rebuild.py --resume-dir "<full-compact-rebuild-folder>"
```

The resulting compact database remains a parallel verified archive. Candidate.5
does not switch the incremental capture engine to the compact schema and does
not delete or replace `history.sqlite`.

## Updated candidate boundary

Candidate.5 adds only the full copy-based compact rebuild, restart checkpoints,
and complete logical verification. The original archive remains authoritative
until a later candidate adapts and validates incremental updates against the
compact schema.

## Candidate.7 compact incremental updates

`history_compact_incremental.py` synchronizes the candidate.5 compact schema.
Validation mode creates and updates `history_compact_validation.sqlite`; direct
updates require both `--in-place` and `--acknowledge-in-place-update`. The
legacy `history.sqlite` database is never opened or changed.

Five-symbol validation:

```cmd
py tools\capture-utility\history_compact_incremental.py --mode sync --smoke
```

Routine console output is now limited to the first symbol, every 25 symbols,
the final symbol, and exceptional classifications. Use `--progress-every N` to
change that interval or `--verbose-progress` to print every symbol. Complete
per-symbol results remain in `symbol-results.csv`.

The run checks SQLite integrity and foreign keys, verifies every returned bar
and event against the captured raw JSON, and writes sanitized non-success
details to `error-classification-review.csv`. Yahoo HTTP 422 descriptions such
as `Data doesn't exist` are classified as `NO_CHART_HISTORY_AVAILABLE`.

The final safety conclusion is mode-specific: validation reports confirm that
the verified source remained unchanged, while acknowledged in-place reports
state that `history_compact.sqlite` was updated directly.

Saved error responses from an earlier run can be reclassified without network
or database access:

```cmd
py tools\capture-utility\history_compact_incremental.py --review-run "C:\path\to\compact-history-run"
```

## Candidate.10 browser-confirmed Long-history exclusions

Candidate.10 installs `data/high-volume/long_history_exclusions_v0_1.csv`.
The file records 12 symbols for which Yahoo exposed no downloadable historical
data during manual browser review and the Chart API returned either a range
rejection or only a current-session bar.

Both `yahoo_history_capture.py` and `history_compact_incremental.py` load this
file before task planning. By default these symbols create zero Long-history
network tasks in baseline, sync, and refresh-flagged modes. Fast-mode quote,
metadata, and snapshot capture is unchanged. Existing database rows, raw
responses, and prior error evidence are preserved; candidate.10 deletes
nothing.

Dry runs and completed run reports record the skipped symbols. Each actual run
also writes `excluded-history-symbols.csv`. A diagnostic override exists as
`--include-history-excluded`, but routine use should leave the exclusion policy
enabled.
