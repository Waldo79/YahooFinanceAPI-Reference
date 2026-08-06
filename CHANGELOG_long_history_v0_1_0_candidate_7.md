# Long-History v0.1.0 candidate.7

- Corrected the final safety conclusion for acknowledged in-place compact updates.
- Replaced the ambiguous in-place `Source compact database unchanged: None` text with an explicit not-applicable status.
- Reduced routine console output to the first symbol, every 25 symbols, the final symbol, and exceptional classifications.
- Added `--progress-every` and `--verbose-progress` controls.
- Expanded HTTP 422 no-history recognition to include Yahoo's `Data doesn't exist` wording.
- Added `error-classification-review.csv` and grouped sanitized response details to the text report.
- Added `--review-run` to reclassify an existing run from its saved raw responses with no network or database access.
- Preserved the legacy `history.sqlite` safety boundary and all candidate.6 verification checks.
