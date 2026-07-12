# Changelog

## v0.4.2 — Capture Validation and Path Hardening

Hardened the Quote capture utility after reviewing the first successful 16-symbol live capture.

### Added

- `--validate-run` for completed run folders
- SHA-256 and response-byte-count revalidation
- Manifest-to-sidecar equality checks
- Request sequence, request-order, symbol, filename, and summary consistency checks
- Missing, duplicate, unsafe, and unreferenced-file detection
- Privacy scanning for unredacted crumbs, cookies, and authorization values
- Known and unmapped JSON-path inventory
- `run-validation.json` and `run-validation.txt` reports
- Per-symbol capture progress lines
- JSON Schema for the validation report
- Expanded offline tests for validation and path behavior

### Changed

- Default capture output now always resolves to repository-root `captures/local/`.
- Manifest input and master-database paths are now portable references rather than full local paths.
- External input paths are reduced to filenames before being stored.
- Windows instructions now state the expected working directory and command separately.

### Live-run findings addressed

- A successful 16-symbol v0.4.1 run returned HTTP 200 for all symbols.
- All raw hashes and sidecars were internally consistent.
- The v0.4.1 manifest exposed full local Windows paths; v0.4.2 removes that exposure.
- The prior default output depended on the current directory; v0.4.2 fixes the destination.

### Validation

- Fifteen capture-utility tests pass.
- Python byte-code compilation passes.
- The uploaded successful v0.4.1 run passes structural, integrity, and privacy validation with only expected warnings for its two legacy absolute manifest paths.

## v0.4.1 — Anonymous Yahoo Session Support

Added anonymous Yahoo cookie-and-crumb handling after the first live v0.4.0 capture returned HTTP 401 Unauthorized for all 16 symbols.

### Added

- In-memory anonymous Yahoo cookie jar
- Query1 crumb retrieval with a Query2 fallback strategy
- One forced session refresh after HTTP 401 or 403
- Browser-compatible default User-Agent
- `--auth-mode anonymous-crumb` default
- `--auth-mode none` diagnostic mode
- Manifest and sidecar session diagnostics without sensitive values
- Explicit `network_error` summary count
- Offline tests for crumb insertion, secret redaction, 401 refresh, and Query2 fallback

### Security and privacy

- Cookie and crumb values are never written to project output.
- Stored request URLs redact the crumb.
- Session values remain in memory only and are discarded when the process exits.
- No Yahoo account credentials are requested or used.

### Validation

- Eight offline tests pass.
- Python byte-code compilation passes.
- Default 16-symbol table passes dry-run validation.
- Live validation must be performed from a normal internet-connected user system.

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
