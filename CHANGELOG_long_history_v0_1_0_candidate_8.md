# Long-History v0.1.0 candidate.8

- Added a separate split-window recovery utility for the 12 confirmed `REQUEST_RANGE_NOT_SUPPORTED` symbols.
- Replaced one 1900-through-current daily request with two explicit windows shorter than Yahoo's 100-year limit.
- Added a 31-day overlap with exact deduplication and conflict rejection.
- Accepted an empty historical window when Yahoo returns `NO_HISTORY_DATA` or `NO_CHART_HISTORY_AVAILABLE` and another window supplies valid history.
- Limited the run to the tracked 12-symbol recovery list; the other 1,525 baseline symbols are not repeated.
- Preserved each Yahoo window response and wrote a derived merged capture with portable source references.
- Added validation-copy-first behavior, returned-value verification, SQLite integrity checks, and explicit in-place acknowledgment.
- Added offline tests for window planning, request counts, deduplication, conflict rejection, empty-window handling, and end-to-end compact validation.
