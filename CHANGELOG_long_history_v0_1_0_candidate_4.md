# Long-History v0.1.0 candidate.4

- Adds `history_sqlite_compact_prototype.py`, an offline copy-based comparison
  between the current row layout and a normalized compact SQLite layout.
- Keeps the authoritative `history.sqlite` in URI read-only/query-only mode and
  verifies its file fingerprint is unchanged.
- Defaults to 100 alphabetically distributed symbols; supports explicit symbols
  and subsets up to 500.
- Writes independent legacy and compact subset databases outside the repository.
- Verifies complete ordered SHA-256 values and row counts for bars and events,
  validates UTC datetime derivation, and runs `PRAGMA quick_check` on both copies.
- Normalizes symbols, intervals, runs, sources, event types, and SHA-256 storage;
  removes repeated `datetime_utc`; uses `WITHOUT ROWID` composite-key tables.
- Adds nine offline tests and documentation. Capture behavior is unchanged and
  no migration, deletion, optimization of the source, or Yahoo request occurs.
