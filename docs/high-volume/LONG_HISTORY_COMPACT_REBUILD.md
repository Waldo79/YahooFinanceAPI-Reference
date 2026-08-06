# Full Compact Long-History Rebuild

Utility version: `0.1.0-candidate.5`

## Purpose

Candidate.4 measured a 72.39% size reduction on a verified 100-symbol sample.
Candidate.5 applies the same normalization principles to every logical table in
the existing long-history archive and creates a separate full compact copy.

The authoritative `history.sqlite` is never modified. No Yahoo request is made.

## Output

The default output folder is external to the repository:

```text
<external-long-history-root>\compact-rebuilds\<timestamp>_full-compact-rebuild
```

It contains:

- `history_compact.sqlite`
- `rebuild_report.txt`
- `rebuild_manifest.json`
- `verification.csv`
- `table_counts.csv`
- `rebuild_progress.log`

## Compact layout

The full copy normalizes repeated symbol, interval, run, event-type, source-file,
and source-hash values. Source SHA-256 values are stored once as binary values,
and `datetime_utc` is derived from `timestamp_utc` rather than repeated on every
bar. The rebuild includes:

- archive metadata;
- run metadata;
- symbol state;
- bars and bar revisions;
- events and event revisions; and
- per-symbol run results.

A date-first bars index remains available for cross-symbol exports.

## Checkpoint and resume

Bars and events are committed one symbol at a time. The output database records
completed symbols in `rebuild_progress`. If the process is interrupted, rerun
with the exact folder printed by the utility:

```cmd
py tools\capture-utility\history_sqlite_compact_rebuild.py --resume-dir "C:\path\to\full-compact-rebuild"
```

Resume is rejected if the source database's name, size, or modification time no
longer matches the source used to start the rebuild.

## Full verification

After all symbols are copied, candidate.5 performs ordered row-count and SHA-256
verification for all eight logical source tables:

- `archive_meta`
- `runs`
- `symbol_state`
- `bars`
- `bar_revisions`
- `events`
- `event_revisions`
- `symbol_runs`

It also runs `PRAGMA quick_check` and `PRAGMA foreign_key_check`. Any mismatch
leaves the compact file in place for diagnosis but does not authorize use.

## Safety boundary

A verified `history_compact.sqlite` is a parallel archive. It is not made the
active incremental-update database by this candidate. Keep `history.sqlite`
until the capture engine is separately adapted and validated against the compact
schema. No source replacement or deletion is included.
