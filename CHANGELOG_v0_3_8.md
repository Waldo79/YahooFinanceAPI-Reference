# Changelog

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
