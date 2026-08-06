# Yahoo Long-History v0.1.0 candidate.12

- Added a public `Long-history subsystem` section to the root README.
- Added `docs/high-volume/LONG_HISTORY_OVERVIEW.md` as the navigation and safety
  entry point for baseline capture, compact rebuild and Sync, reviewed exclusions,
  and read-only XLSX export.
- Distinguished Fast-mode current snapshots, Long-history persistent archives, and
  the separate v0.5.0 comparative-study line.
- Documented that the original `history.sqlite` and verified
  `history_compact.sqlite` remain in their existing external locations and must not
  be moved, replaced, renamed, or deleted.
- Linked the detailed capture guide, XLSX guide, exclusion policy, and principal
  Long-history utilities from public repository navigation.
- Added five offline documentation tests.
- Changed no capture, synchronization, exclusion, SQLite, network, or XLSX behavior.
