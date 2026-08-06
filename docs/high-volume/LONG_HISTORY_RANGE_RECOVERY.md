# Long-history split-window range recovery

Candidate.8 recovers the 12 symbols whose explicit 1900-through-current daily
Chart requests returned `REQUEST_RANGE_NOT_SUPPORTED`. Yahoo's saved response
states that one daily request cannot span more than 100 years.

The utility uses two explicit windows by default:

- 1900-01-01 through 1990-01-01, exclusive
- 1989-12-01 through the selected ending date, exclusive

The 31-day overlap is used only for verification and deduplication. Identical
overlap bars and events are stored once. Conflicting overlap values produce
`RECOVERY_OVERLAP_CONFLICT`; that symbol is not applied.

## Safety model

The default run copies the verified `history_compact.sqlite` to
`history_compact_validation.sqlite` and updates only the copy. The legacy
`history.sqlite` is not opened. Direct compact-database updates require both
`--in-place` and `--acknowledge-in-place-update`.

The utility is limited by the tracked file:

```text
data/history_range_recovery_symbols_12.csv
```

It does not repeat the other 1,525 baseline symbols.

## Validate first

Run from the repository root:

```cmd
py tools\capture-utility\history_range_recovery.py
```

The run makes 24 requests: two windows for each of 12 symbols. Outputs are
written under the external compact rebuild folder in
`range-recovery-validations`. The main report is
`range-recovery-report.txt`.

Review these fields before any in-place update:

- `Recovery complete`
- `Overall verification`
- overlap conflict counts
- returned-value mismatch counts
- per-symbol classifications

## In-place update

Run only after the validation copy passes:

```cmd
py tools\capture-utility\history_range_recovery.py --in-place --acknowledge-in-place-update
```

No database promotion, replacement, rename, or deletion is performed.

## Dry run

```cmd
py tools\capture-utility\history_range_recovery.py --dry-run
```

The dry run prints the exact windows and request count and sends no network
requests.

## Review a completed recovery without network or database access

```cmd
py tools\capture-utility\history_range_recovery.py --review-run "<completed range-recovery folder>"
```

This writes `range-recovery-bar-review.txt` and `range-recovery-bar-review.csv` inside the selected run folder. The review reads only the derived merged JSON files; it does not open a database or send Yahoo requests.
