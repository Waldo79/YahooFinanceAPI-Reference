# Yahoo Finance Long-History Subsystem

The Long-history subsystem is separate from both the Fast-mode snapshot engine and
the v0.5.0 comparative-study line. It is an additive workflow for preserving and
maintaining daily, weekly, and monthly Yahoo Chart history outside the synchronized
repository.

## Component map

- `tools/capture-utility/yahoo_history_capture.py` creates baseline history captures,
  preserves compressed raw Chart responses, and maintains the original
  `history.sqlite` archive.
- `tools/capture-utility/history_sqlite_audit.py` inspects the original archive
  read-only and writes external audit evidence.
- `tools/capture-utility/history_sqlite_compact_rebuild.py` builds and verifies a
  separate normalized `history_compact.sqlite` copy.
- `tools/capture-utility/history_compact_incremental.py` validates or performs
  explicitly acknowledged incremental synchronization against the compact schema.
- `data/high-volume/long_history_exclusions_v0_1.csv` records reviewed symbols that
  are skipped by routine Long-history planning while remaining available through a
  diagnostic override.
- `tools/capture-utility/history_compact_xlsx_export.py` streams the verified compact
  archive into a new XLSX workbook without contacting Yahoo.

Fast mode remains the high-volume current-snapshot workflow. Long History remains the
persistent historical-archive workflow. Neither changes the formal v0.4.3 release or
the separate v0.5.0-draft study status.

## External storage boundary

The default archive is outside the repository:

```text
C:\Users\<name>\Downloads\YAHOO\Captures\long-history
```

The original `history.sqlite`, verified `history_compact.sqlite`, raw responses,
capture runs, compact rebuilds, incremental runs, audits, and exports stay under that
external archive. Do not move, replace, rename, or delete either database.

Repository-contained output paths are rejected by the Long-history utilities. Database
files and raw captures are evidence and are not committed to Git.

## Read and write boundaries

The baseline utility writes the original archive as part of an intentional capture or
Sync run. The compact incremental utility can update `history_compact.sqlite` only
through its explicit in-place acknowledgement controls.

The XLSX exporter is different: it opens only `history_compact.sqlite` through SQLite
URI `mode=ro`, enables `PRAGMA query_only=ON`, never opens `history.sqlite`, performs
no network requests, fingerprints the compact source before and after, and writes only
a new external export folder.

## Safe inspection commands

Show the configured Long-history archive without contacting Yahoo:

```cmd
py tools\capture-utility\yahoo_history_capture.py --show-output-root
```

Inspect the verified compact database and a five-symbol export plan without writing
files:

```cmd
py tools\capture-utility\history_compact_xlsx_export.py --dry-run --smoke
```

Create a new five-symbol validation workbook:

```cmd
py tools\capture-utility\history_compact_xlsx_export.py --smoke
```

Create a full workbook from the verified compact database:

```cmd
py tools\capture-utility\history_compact_xlsx_export.py
```

## Detailed documentation

- `tools/capture-utility/HISTORY_CAPTURE_README.md` documents baseline capture,
  resume, Sync, audits, compact rebuilds, compact incremental validation, and the
  Long-history exclusion policy. It is chronological: earlier candidate-boundary
  statements describe those historical candidates, not current component
  availability.
- `docs/high-volume/LONG_HISTORY_XLSX_EXPORT.md` documents exporter filters,
  worksheet layout, row splitting, manifests, reports, and source verification.
- `data/high-volume/long_history_exclusions_v0_1.csv` is the reviewed exclusion
  policy consumed before Long-history task planning.

## Candidate boundary

Candidate.12 integrates the completed Long-history components into public repository
navigation and locks the documentation safety boundary with offline tests. It changes
no capture, synchronization, exclusion, SQLite, network, or XLSX behavior; opens no
database; sends no request; and creates no release tag.
