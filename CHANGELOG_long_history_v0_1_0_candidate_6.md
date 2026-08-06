# Long-History v0.1.0 candidate.6

- Added compact-schema incremental synchronization.
- Defaulted the first live run to a separate validation copy.
- Added full returned-value, integrity, foreign-key, accounting, and symbol-state checks.
- Added checkpoint/resume support and an explicit two-switch safeguard for in-place updates.
- Added sanitized Yahoo HTTP error-object classification for history failures.
- Left both `history.sqlite` and the verified source `history_compact.sqlite` untouched in validation mode.
