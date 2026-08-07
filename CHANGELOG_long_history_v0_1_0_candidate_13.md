# Yahoo Long-History v0.1.0 candidate.13

- Added immediate phase messages for compact-database resolution, read-only opening, row counting, worksheet generation, XLSX finalization, ZIP verification, source fingerprint verification, hashing, and report completion.
- Added periodic processed/total row progress with a default cadence of 250,000 rows.
- Added worksheet-split and per-worksheet packaging messages so large exports no longer appear idle.
- Added `--progress-every ROWS` and `--quiet-progress` controls.
- Sent progress to standard error so dry-run JSON and final result output on standard output remain machine-readable.
- Preserved workbook contents, query ordering, read-only database access, no-network behavior, and external-output safeguards.
- Added four offline tests for row cadence, finalization phases, quiet mode, positive cadence validation, and dry-run stream separation.
