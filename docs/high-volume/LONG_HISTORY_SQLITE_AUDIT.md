# Long-History SQLite Read-Only Audit

Utility version: `0.1.0-candidate.3`

## Purpose

The corrected 1,537-symbol daily baseline stored 11,034,124 bars. The external
archive measured approximately 3.55 GB, of which `history.sqlite` was about
3.34 GB while compressed raw JSON was about 217 MB. Before expanding the symbol
universe or designing XLSX exports, this utility identifies which tables,
indexes, and repeated text columns account for the SQLite size.

## Safety boundary

The audit:

- opens SQLite with URI `mode=ro`;
- enables `PRAGMA query_only=ON`;
- performs no network requests;
- issues no INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, VACUUM, REINDEX,
  ANALYZE, or WAL checkpoint command;
- writes reports only to a new external directory; and
- compares the main database file size and modification time before and after
  inspection.

Close any history capture or SQLite program before running the audit. The
reference database remains the authoritative untouched archive.

## Default command

From the repository root:

```cmd
py tools\capture-utility\history_sqlite_audit.py
```

The database is resolved from the same long-history external-storage setting:

1. `--database PATH`;
2. `YAHOO_HISTORY_CAPTURE_ROOT` plus `history.sqlite`;
3. ignored `config\local\history_capture_local.json`;
4. the safe external default.

The report directory defaults to:

```text
<external-long-history-root>\audits\<timestamp>_sqlite-audit
```

An output directory inside the synchronized repository is rejected.

## Reports

- `audit_report.txt` — compact human-readable conclusions.
- `audit_manifest.json` — execution mode, integrity result, and unchanged-file
  verification without an absolute database path.
- `database_summary.csv` — page size/count, freelist, journal, and schema data.
- `schema_objects.csv` — tables, indexes, root pages, and schema SQL.
- `object_sizes.csv` — page, payload, and unused bytes from `dbstat` when
  available.
- `tables.csv` — exact row counts and table-plus-index sizes.
- `indexes.csv` — index columns, origin, uniqueness, and sizes.
- `columns.csv` — declared types, primary-key positions, and JSON/raw candidates.
- `text_storage_sample.csv` — deterministic three-window samples estimating
  repeated text and projected character volume.

## Optional controls

Full SQLite integrity scan:

```cmd
py tools\capture-utility\history_sqlite_audit.py --integrity-check full
```

Faster audit without exact `COUNT(*)` scans:

```cmd
py tools\capture-utility\history_sqlite_audit.py --skip-exact-row-counts
```

Explicit database and report folder:

```cmd
py tools\capture-utility\history_sqlite_audit.py --database "D:\Yahoo\long-history\history.sqlite" --output-dir "D:\Yahoo\long-history\audits\manual-audit"
```

The output directory must not already exist; this prevents accidental report
overwrite.

## Interpretation boundary

Text-volume projections are estimates from sampled rows, not byte-for-byte
SQLite compression measurements. `dbstat` object sizes are authoritative when
that SQLite feature is available. The audit recommends candidates for a later
copy-based optimization experiment but performs no optimization itself.
