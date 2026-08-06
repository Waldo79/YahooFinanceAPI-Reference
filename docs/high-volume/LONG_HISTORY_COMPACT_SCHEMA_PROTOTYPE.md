# Long-History Compact-Schema Prototype

Utility version: `0.1.0-candidate.4`

## Purpose

The read-only candidate.3 audit found that the 3.34 GB `history.sqlite` contains
11,034,124 bar rows and repeats symbol, interval, run identifiers, source paths,
source SHA-256 text, and `datetime_utc` across those rows. Candidate.4 measures
how much of that storage can be avoided without touching the authoritative
archive or downloading Yahoo data again.

## Copy-based comparison

The utility opens `history.sqlite` with SQLite URI `mode=ro` and
`PRAGMA query_only=ON`, selects a bounded symbol subset, and creates two new
external databases:

- `legacy_subset.sqlite` reproduces the current `bars` and `events` layouts and
  indexes for the selected symbols.
- `compact_subset.sqlite` stores the same logical rows using normalized lookup
  tables and compact composite-key tables.

The default is 100 symbols distributed across the alphabetically sorted source
universe. This avoids choosing only young or only old securities. The limit can
be changed up to 500, or explicit symbols can be supplied.

## Compact layout

The prototype:

- replaces repeated symbol and interval text with integer identifiers;
- replaces repeated first/last run text with integer run keys;
- stores each source file and source SHA-256 only once;
- stores a valid SHA-256 as 32 bytes instead of 64 hexadecimal characters;
- derives `datetime_utc` from `timestamp_utc` rather than storing both;
- normalizes event-type text;
- uses `WITHOUT ROWID` for composite-key `bars` and `events`; and
- retains a date-first bars index for cross-symbol time-series and XLSX export.

It does not yet redesign revision tables, symbol state, run summaries, or the
capture engine. Those are later migration decisions.

## Verification

For both bars and events, the utility streams rows in a deterministic order and
computes SHA-256 over the complete logical record. The compact query joins its
lookup tables and derives `datetime_utc`, reconstructing the original logical
row before hashing. A successful prototype requires:

- identical source, legacy-copy, and compact-copy row counts;
- identical ordered hashes for every selected bar and event;
- zero derived-datetime mismatches;
- `PRAGMA quick_check` equal to `ok` for both new databases; and
- unchanged source database size and modification time.

The utility stops with an error if any of these checks fail.

## Default command

From the repository root:

```cmd
py tools\capture-utility\history_sqlite_compact_prototype.py
```

Output defaults to:

```text
<external-long-history-root>\prototypes\<timestamp>_compact-schema-prototype
```

An output path inside the synchronized repository is rejected.

## Optional controls

Test 250 alphabetically distributed symbols:

```cmd
py tools\capture-utility\history_sqlite_compact_prototype.py --symbol-limit 250
```

Test an explicit group:

```cmd
py tools\capture-utility\history_sqlite_compact_prototype.py --symbols AAPL,^GSPC,SPY,PDI,VTSAX
```

Use explicit source and output paths:

```cmd
py tools\capture-utility\history_sqlite_compact_prototype.py --database "D:\Yahoo\long-history\history.sqlite" --output-dir "D:\Yahoo\long-history\prototypes\manual-compact-test"
```

The output directory must not already exist.

## Reports

- `prototype_report.txt` gives the human-readable result.
- `prototype_manifest.json` records safety, counts, hashes, integrity, and size
  results without persisting the absolute source path.
- `selected_symbols.csv` records the exact prototype universe.
- `size_comparison.csv` compares both new SQLite files.
- `verification.csv` records row counts and ordered hashes.
- `legacy_subset.sqlite` and `compact_subset.sqlite` are disposable test copies.

## Decision boundary

A favorable size result does not authorize replacing `history.sqlite`. The next
step after a successful representative run is to review the measured reduction,
query needs, migration time, and update behavior. A full rebuild must be made
from the existing authoritative archive or compressed raw evidence into a new
file, followed by complete verification before any archive switch.
