# Changelog

## v0.4.0 — First Working Capture Utility

Implemented the first executable evidence-capture workflow from the v0.3.9 specifications.

### Added

- User-editable `tools/capture-utility/symbols.csv`
- Sequential one-symbol Quote requests
- Project limit of 30 enabled symbol rows
- Byte-for-byte raw response preservation
- SHA-256 integrity calculation
- Per-request UTC timing, HTTP, content-type, response-size, and retry metadata
- Run manifest written at start and updated after each request
- Deterministic normalized text ordered by `data/master_field_database.csv`
- Timestamp decoding alongside raw epoch values
- Explicit handling of empty results, omitted requested symbols, HTTP errors, rate limits, network errors, and parse errors
- Conservative retry and pause controls
- Dry-run input validation
- Offline unit tests with simulated HTTP responses
- v0.4.0 implementation and usage documentation

### Changed

- Replaced the earlier prototype Quote batching behavior with one request per symbol.
- Raw response bodies are no longer parsed and re-serialized before storage.
- The capture utility is now Quote-focused; other endpoint families remain planned work.
- Local capture output is excluded from version control.

### Validation

- Five offline capture-utility tests pass.
- Python byte-code compilation passes.
- Default 16-symbol table passes dry-run validation.

## v0.3.9 — Reference Pages and Capture Format Specification

Added human-readable reference pages and formal specifications for future capture and normalization utilities.

### Added

- Reference index under `docs/reference/`
- Market-state reference and transition-test guidance
- Security-type coverage table
- Endpoint registry and result classifications
- UTC timestamp and time-zone rules
- Symbol-format rules
- Data-delay behavior classifications
- Capture-format specification
- Normalized-output-format specification
- Capture-run manifest JSON template
- Reference-evidence JSON template

### Defined

- Up to 30 table-selected symbols per run
- Sequential one-symbol requests for the first implementation
- Exact raw-JSON preservation
- UTC request and response timestamps
- Metadata sidecar files
- SHA-256 integrity values
- Deterministic fixed-order normalized output
- Full JSON-path preservation
- Separate handling for missing fields, explicit nulls, empty results, and omitted requested symbols

### Unchanged

- Master field database remains at 198 rows.
- Observation review and change-classification workflow remains in effect.
- No production downloader, timer, scheduler, or JSON formatter is included yet.

### Validation

- Twelve specification and template files committed
- ZIP integrity check completed before upload
- Repository hygiene corrections already merged through pull request #1

## v0.3.8 — Observation Review and Change Classification

Added an observation review and change-classification layer.

### Added

- Review status categories
- Evidence quality levels
- Change classification rules
- False-positive checks
- Duplicate handling rules
- Needs-retest workflow
- Change promotion gates
- Review queue template
- Suggested GitHub review labels
- Review/classification documentation

### Changed

- README now explains that public reports start as observations before they become confirmed Yahoo API change records.

### Unchanged

- Master field database remains at 198 rows.
- Existing issue templates remain available.

### Validation

- Release manifest validation
- Review-classification table validation
- ZIP integrity check
