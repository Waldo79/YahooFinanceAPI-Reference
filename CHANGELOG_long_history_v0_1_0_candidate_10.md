# Yahoo Long-History candidate.10

- Added a browser-confirmed Long-history exclusion registry for 12 symbols with no downloadable Yahoo history.
- Applied exclusions before baseline and compact-incremental task planning, producing zero Long-history requests for those symbols by default.
- Kept Fast-mode current quote and metadata capture unchanged.
- Preserved all existing database rows, raw responses, and earlier API-error evidence.
- Added exclusion details to dry-run JSON, run manifests, text reports, and `excluded-history-symbols.csv`.
- Added an explicit diagnostic override, `--include-history-excluded`.
- Added targeted tests for parsing, filtering, non-destructive reporting, baseline dry-run planning, and compact dry-run planning.
