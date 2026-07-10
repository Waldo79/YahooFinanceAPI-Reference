# Changelog

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
