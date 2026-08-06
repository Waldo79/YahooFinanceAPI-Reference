# Yahoo Long-History Compact XLSX Export

Candidate.11 adds a read-only, no-network XLSX exporter for the verified compact
long-history database. It is additive and does not alter the baseline capture,
compact synchronization, exclusion policy, or Fast-mode behavior.

## Safety boundary

The exporter:

- locates the latest verified `history_compact.sqlite`, unless `--database` or
  `--rebuild-dir` is supplied;
- opens the compact database with SQLite URI `mode=ro` and
  `PRAGMA query_only=ON`;
- requires compact schema version 1 with build status `VERIFIED_COMPLETE` or
  `ACTIVE_COMPACT`;
- performs no Yahoo or other network request;
- never opens the original `history.sqlite`;
- writes only a new external export folder; and
- fingerprints the compact database before and after export.

The verified compact database and original `history.sqlite` must remain in their
existing external locations. Do not move, replace, rename, or delete either one.

## Dry-run inspection

From the repository root:

```cmd
py -m py_compile tools\capture-utility\history_compact_xlsx_export.py && py tools\capture-utility\history_compact_xlsx_export.py --dry-run --smoke
```

The dry run resolves the verified compact database, validates its schema and
quick check, selects the first five symbols, reports planned row counts, sends
no network requests, and writes no files.

## Five-symbol validation export

```cmd
py tools\capture-utility\history_compact_xlsx_export.py --smoke --include-revisions
```

Default output:

```text
...\YAHOO\Captures\long-history\exports\<timestamp>_compact-xlsx-export\
  Yahoo_Long_History.xlsx
  export-manifest.json
  export-report.txt
```

## Full export

```cmd
py tools\capture-utility\history_compact_xlsx_export.py
```

The workbook contains:

- `Summary` — source, filter, row-count, and safety information;
- `Symbols` — per-symbol/interval coverage and state;
- `Bars` — normalized OHLC, adjusted close, volume, run provenance, and source
  provenance;
- `Events` — corporate actions and source provenance; and
- optional `BarRevisions` and `EventRevisions` sheets when
  `--include-revisions` is used.

Large datasets split automatically into numbered sheets before Excel's row
limit. The writer streams worksheet XML and does not hold all bars in memory.
It uses only the Python standard library.

## Filters

Examples:

```cmd
py tools\capture-utility\history_compact_xlsx_export.py --symbols AAPL,MSFT,SPY
```

```cmd
py tools\capture-utility\history_compact_xlsx_export.py --interval 1d --start-date 2020-01-01 --through-date 2026-08-06
```

```cmd
py tools\capture-utility\history_compact_xlsx_export.py --symbols-file data\my_history_symbols.csv --include-revisions
```

`--start-date` and `--through-date` are inclusive UTC dates. `--symbols-file`
accepts a `symbol` header or a one-column list.

## Candidate boundary

Candidate.11 exports a verified compact database to a new XLSX workbook. It does
not update either SQLite database, contact Yahoo, change exclusions, create a
release tag, or replace any prior capture or evidence file.
