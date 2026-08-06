# Long-History v0.1.0-candidate.3

## Added

- Read-only `history_sqlite_audit.py` diagnostic utility.
- SQLite URI `mode=ro` and `PRAGMA query_only=ON` enforcement.
- `dbstat` table/index page-size reporting with a graceful unavailable fallback.
- Exact table row counts, schema/index inventory, freelist reporting, and
  deterministic repeated-text sampling.
- Quick, full, or skipped integrity-check modes.
- External text/CSV/JSON audit package with no absolute database path stored.
- Before/after main-database fingerprint verification.
- Fourteen offline tests for read-only behavior, reporting, and safety limits.

## Not changed

- Yahoo capture behavior remains `0.1.0-candidate.2`.
- The completed 1,537-symbol baseline is not modified or redownloaded.
- No SQLite optimization or migration is performed.
- No XLSX export is included yet.
