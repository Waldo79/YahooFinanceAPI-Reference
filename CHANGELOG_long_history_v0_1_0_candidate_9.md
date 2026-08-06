# Yahoo Long-History Range Recovery — v0.1.0 candidate.9

- Adds `--review-run` for no-network, no-database inspection of merged recovery bars.
- Writes exact timestamps and OHLC/adjusted-close/volume values to `range-recovery-bar-review.csv`.
- Writes per-symbol first/last dates and flags single-bar-only results in `range-recovery-bar-review.txt`.
- Keeps candidate.8 split-window recovery behavior unchanged.
