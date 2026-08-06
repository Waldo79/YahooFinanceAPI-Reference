# Yahoo Long-History v0.1.0 candidate.11

- Added a standard-library streaming XLSX exporter for the verified compact database.
- Opened `history_compact.sqlite` read-only with `PRAGMA query_only=ON` and verified its schema and quick check.
- Added symbol, interval, inclusive UTC date, smoke, and optional revision filters.
- Added automatic worksheet splitting before Excel's row limit.
- Added workbook, JSON manifest, text report, SHA-256, and before/after source fingerprint verification.
- Kept the legacy `history.sqlite` unopened and preserved both databases without move, replacement, rename, or deletion.
- Added nine offline tests for read-only enforcement, valid XLSX generation, filtering, date-filtered coverage summaries, row splitting, portable pre-1970 dates, XML sanitization, dry-run behavior, verified-status enforcement, and external-output enforcement.
