# Long-History candidate.5 changelog

- Added `history_sqlite_compact_rebuild.py` for a full copy-based compact rebuild.
- Normalized repeated symbol, interval, run, event-type, and source provenance values across all history tables.
- Added per-symbol committed checkpoints and `--resume-dir` recovery.
- Added ordered row-count and SHA-256 verification for all eight logical tables.
- Added SQLite quick-check and foreign-key verification.
- Preserved the authoritative `history.sqlite` as read-only and unchanged.
- Did not switch the capture engine, replace the source database, delete files, or contact Yahoo.
