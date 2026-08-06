# Long History v0.1.0-candidate.2

## Corrected after the first live baseline

- Replaced Windows-dependent `datetime.fromtimestamp` conversion with portable
  Unix-epoch arithmetic so valid pre-1970 market dates do not raise
  `OSError: [Errno 22] Invalid argument`.
- Replaced Yahoo `range=max` baseline requests with explicit bounds beginning
  at 1900-01-01 and ending at the selected exclusive through-date boundary.
- Added strict validation that Yahoo `meta.dataGranularity` matches the
  requested interval before bars are inserted into SQLite.
- Added offline tests for pre-1970 timestamps, baseline URL bounds, and silent
  interval downgrade rejection.

## Recovery policy

The interrupted candidate.1 archive should be preserved for diagnosis but not
resumed, because its completed symbols may contain coarser-than-requested bars.
Start candidate.2 with a fresh external archive.

## Deliberately excluded

- Intraday history.
- XLSX history exports; these follow live baseline and Sync validation.
