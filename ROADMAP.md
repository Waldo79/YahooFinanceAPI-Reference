# Roadmap

## Completed

### v0.1.x — Foundation

Established the repository structure, documentation approach, and initial public reference files.

### v0.2.x — Capture and market-state groundwork

Added the initial capture prototype, representative symbol set, endpoint-routing concepts, and market-state documentation groundwork.

### v0.3.x — Public reference, evidence, and review framework

Built the master field database, CSV mapping guidance, parser/result diagnostics, public contribution workflow, observation review rules, change classification, reference pages, and formal capture/normalization specifications.

### v0.4.0 — First working capture utility

Implemented sequential Quote capture with a symbol table, unchanged raw evidence, SHA-256, metadata sidecars, normalized text, retry controls, and a run manifest.

### v0.4.1 — Anonymous Yahoo session support

Added anonymous in-memory cookie-and-crumb setup, Query2 fallback, one refresh after HTTP 401/403, and secret redaction after a bare live request returned HTTP 401.

### v0.4.2 — Capture validation and path hardening

Added completed-run validation, hash and file-set checking, privacy scans, mapped/unmapped path reporting, portable manifest paths, fixed repository-root output, and per-symbol progress.

### v0.4.3 — Zero-pause capture baseline

Set the normal inter-symbol pause to 0 milliseconds after repeated successful live stopwatch runs, while preserving explicit pacing overrides, retry delays, timeout behavior, and anonymous-session refresh safeguards.

## Planned

### v0.4.x — Additional capture hardening

Use additional real-world runs to refine diagnostics and compatibility without weakening raw-evidence or privacy rules.

### v0.5.0 — Additional endpoint families

Extend structured capture to Chart first, followed by QuoteSummary, Search, Screener, Options, and other verified endpoint families.

### v0.6.0 — Comparison and field-discovery utilities

Compare captures, identify new/missing/type-changed paths, create unmapped-field reports, and feed reviewed findings back into the master field database.

### v0.7.0 — CSV and workbook output

Generate stable CSV mappings, field occurrence matrices, security-type matrices, and an updated Excel reference workbook from reviewed source data.

### v1.0.0 — Initial stable public reference

Publish a durable workflow with validated captures, field occurrence and security-type matrices, comparison tooling, validation suites, generated workbooks, and documented release procedures.
