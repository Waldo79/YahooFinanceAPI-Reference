# Long-history request exclusions

Candidate.10 separates current-data capture from Long-history eligibility.
Yahoo browser review found no downloadable history for the 12 symbols listed in
`data/high-volume/long_history_exclusions_v0_1.csv`. Saved Chart responses also
showed either a request-range rejection or only one current-session bar.

## Policy

- Skip the listed symbols in Long-history baseline, sync, and refresh-flagged planning.
- Keep Fast-mode quote, metadata, chart-snapshot, and other current-data capture unchanged.
- Preserve all existing database rows, compressed raw responses, and error evidence.
- Do not describe the one-bar validation result as recovered Long history.
- Write the applied exclusions into each new Long-history run report and manifest.

The policy is non-destructive. It does not delete a symbol from the master input
universe or either SQLite archive.
