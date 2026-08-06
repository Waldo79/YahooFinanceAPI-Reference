# Compact Long-History Incremental Updates

Utility version: `0.1.0-candidate.7`

## Purpose

Candidate.5 created and fully verified `history_compact.sqlite`. Candidate.6
validated incremental synchronization first on a separate copy and then on the
verified compact database. Candidate.7 retains that update design while fixing
report wording, reducing console noise, and expanding Yahoo error diagnostics.

The legacy `history.sqlite` database is never opened or changed by this utility.

## Update targets

Without `--in-place`, the utility creates and updates:

```text
history_compact_validation.sqlite
```

The verified `history_compact.sqlite` remains unchanged and is fingerprinted
before and after the validation.

A direct update requires both safeguards:

```text
--in-place --acknowledge-in-place-update
```

The in-place report now states explicitly that the compact database was the
update target. It does not incorrectly repeat validation-copy conclusions.

## Verification

After each update, candidate.7 checks:

- `PRAGMA quick_check`;
- `PRAGMA foreign_key_check`;
- run-result accounting for bars and events;
- `symbol_state.last_bar_timestamp` against the actual latest stored bar;
- every returned bar value against the captured raw JSON; and
- every returned event JSON value against the compact database.

The run passes only when all checks succeed.

## Quieter progress output

Routine output is printed for:

- the first completed symbol;
- every 25th completed symbol;
- the final completed symbol; and
- exceptional classifications that need attention.

Change the interval with:

```text
--progress-every N
```

Restore one line per symbol with:

```text
--verbose-progress
```

Detailed per-symbol results remain in `symbol-results.csv` regardless of console
verbosity.

## Yahoo error classification

Candidate.7 recognizes common Yahoo Chart responses including:

- `NO_CHART_HISTORY_AVAILABLE`, including `Data doesn't exist` responses;
- `REQUEST_RANGE_NOT_SUPPORTED`;
- `YAHOO_ERROR_OBJECT`; and
- `HTTP_ERROR_<status>_UNCLASSIFIED`.

Every non-success response is written to:

```text
error-classification-review.csv
```

The file records the symbol, classification, HTTP status, sanitized Yahoo error
code and description, request range, attempts, and raw-capture reference. The
text report also groups matching responses and lists affected symbols.

An existing candidate.6 run can be reviewed without another Yahoo request or
database update:

```cmd
py tools\capture-utility\history_compact_incremental.py --review-run "C:\path\to\compact-history-run"
```

This reads only the saved `symbol-results.csv` and compressed raw response files,
then writes `error-classification-review.csv` and
`error-classification-review.txt` into that existing run folder.

## Checkpoint and resume

Completed symbol tasks are recorded in `checkpoint.jsonl`. Resume uses the same
run folder and rejects a changed task plan.

```cmd
py tools\capture-utility\history_compact_incremental.py --resume-run "C:\path\to\compact-sync-validation"
```

Candidate.7 never replaces, renames, promotes, or deletes a database.

## Candidate.10 exclusion policy

The compact incremental planner now reads:

```text
data/high-volume/long_history_exclusions_v0_1.csv
```

The 12 entries are classified `EXCLUDE_LONG_HISTORY_REQUESTS` from two evidence
sources: `BROWSER_NO_DOWNLOADABLE_HISTORY` and the saved API results
`REQUEST_RANGE_NOT_SUPPORTED|CURRENT_SESSION_BAR_ONLY`.

The exclusion is applied before smoke or limit selection, so excluded symbols
produce no Long-history requests. They remain eligible for Fast-mode current
quote and metadata capture. No existing compact or legacy database rows are
removed. Dry-run JSON, the compact manifest, the text report, and
`excluded-history-symbols.csv` record the applied policy.

The explicit diagnostic override is:

```text
--include-history-excluded
```

It should not be used for normal baseline or incremental operation.
