# Changelog

## v0.3.1-draft — 2026-07-09

### Added
- Response-health diagnostic fields in the master field database.
- Capture observations table for the user-provided run results and AAPL null report.
- Endpoint failure mode reference table.
- Endpoint health-check URL list.
- AAPL null-result triage document.
- Result-state enumeration.

### Changed
- Capture tasks now require HTTP status, content type, result count, and result-state logging before field extraction.
- AAPL search/chart and predefined screener checks are marked `needs_retest` rather than treated as invalid symbol evidence.

### Interpretation
- AAPL nulls from search/chart plus null predefined screener results are most consistent with a capture/access/rate-limit/schema condition until HTTP diagnostics prove otherwise.

# Changelog

## v0.3.0-draft — 2026-07-09

### Added
- Master field database starter with 142 rows.
- Column dictionary for the master field database.
- Representative symbol set for focused capture coverage.
- Enumeration table for marketState, quoteType/instrumentType, contractSize, and common currencies.
- Capture task queue for quote/chart/options/quoteSummary/search/screener/spark endpoints.
- JSON schema and offline validator.

### Design
- Keeps dynamic-schema approach: endpoint-first routing, quoteType/instrumentType secondary routing, raw unknown-field preservation, and append-only CSV columns.
