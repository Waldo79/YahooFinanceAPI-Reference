# v0.5.0 Seven-Endpoint Capture and Analysis Architecture

## Status

Initial architecture draft for review.

This document defines the next development stage of the YahooFinanceAPI-Reference project. It does not yet authorize implementation or repository upload. Endpoint URLs and parameter names must be live-verified before code is finalized.

## 1. Project objective

Build a repeatable evidence-and-analysis system that:

1. sends controlled requests to seven Yahoo Finance endpoint families;
2. preserves every returned response byte-for-byte;
3. records complete non-sensitive request and timing metadata;
4. converts valid JSON into deterministic long-form records;
5. records which JSON paths are present, absent, null, empty, or type-changed in every sample;
6. compares results across symbols, security types, exchanges, request parameters, capture dates, and market states; and
7. generates analysis-ready CSV and Excel outputs without treating the workbook as the authoritative evidence.

The authoritative evidence remains the unchanged raw response plus its SHA-256 digest and metadata sidecar.

## 2. Endpoint scope

The v0.5.0 baseline uses the six endpoint IDs already registered in the repository and adds Options as a seventh endpoint family:

| Endpoint ID | Working purpose | Principal request subject |
|---|---|---|
| `quote` | Current quote and market-summary fields | One or more symbols |
| `chart` | Historical bars, events, and chart metadata | Usually one symbol plus range/interval parameters |
| `quote-summary` | Module-based profile, statistics, and fundamentals | Usually one symbol plus module selection |
| `search` | Symbol, company, fund, and news search | Search text rather than necessarily a ticker |
| `screener` | Predefined or custom screening results | Screener definition, filters, sorting, and paging |
| `fundamentals-timeseries` | Historical fundamental metrics | Symbol(s), metric names, and date range |
| `options` | Option-chain expirations, calls, puts, contracts, strikes, pricing, volume, open interest, and volatility fields | Underlying symbol plus optional expiration date |

Options is added without removing Fundamentals Timeseries. The endpoint registry should be expanded during v0.5.0 documentation work so all seven endpoint families have the same evidence and analysis status.

## 3. Architectural principle

Do not build seven unrelated scripts.

Build one shared capture system with seven endpoint adapters:

```text
Shared command-line application
    |
    +-- Session and authentication manager
    +-- Request scheduler
    +-- HTTP capture and retry engine
    +-- Raw evidence writer
    +-- Metadata and manifest writer
    +-- JSON validator and flattener
    +-- Analysis database generator
    +-- Workbook generator
    +-- Run validator
    |
    +-- Endpoint adapters
         +-- quote
         +-- chart
         +-- quote-summary
         +-- search
         +-- screener
         +-- fundamentals-timeseries
         +-- options
```

Each adapter is responsible only for:

- validating endpoint-specific input;
- constructing the endpoint-specific request;
- redacting sensitive request components;
- identifying returned result objects;
- extracting endpoint-specific returned-symbol or returned-query identifiers;
- classifying empty and partial results; and
- supplying endpoint-specific comparison identities for arrays.

The shared engine handles everything else.

## 4. Proposed repository structure

```text
root/
  config/
    studies/
      sample-study.csv
    requests/
      quote_requests.csv
      chart_requests.csv
      quote_summary_requests.csv
      search_requests.csv
      screener_requests.csv
      fundamentals_timeseries_requests.csv
      options_requests.csv

  tools/
    yahoo-reference/
      yahoo_reference.py
      core/
        capture.py
        session.py
        requests.py
        evidence.py
        manifest.py
        flatten.py
        analysis.py
        workbook.py
        validation.py
      endpoints/
        quote.py
        chart.py
        quote_summary.py
        search.py
        screener.py
        fundamentals_timeseries.py
        options.py

  captures/
    local/
      <run-id>/
        run-manifest.json
        raw/
          quote/
          chart/
          quote-summary/
          search/
          screener/
          fundamentals-timeseries/
          options/
        metadata/
          quote/
          chart/
          quote-summary/
          search/
          screener/
          fundamentals-timeseries/
          options/
        normalized/
          fields-long.csv
          samples.csv
          requests.csv
          endpoint-results/
        analysis/
          field-catalog.csv
          field-occurrence-long.csv
          field-occurrence-matrix.csv
          field-types-long.csv
          comparison-findings.csv
          yahoo-reference-analysis.xlsx
        validation/
          run-validation.json
          run-validation.txt
        errors/

  schemas/
    run-manifest-v0.5.0.schema.json
    request-metadata-v0.5.0.schema.json
    fields-long-v0.5.0.schema.json
    field-catalog-v0.5.0.schema.json

  tests/
    test_core_capture.py
    test_flattening.py
    test_analysis_outputs.py
    test_run_validation.py
    endpoints/
      test_quote_adapter.py
      test_chart_adapter.py
      test_quote_summary_adapter.py
      test_search_adapter.py
      test_screener_adapter.py
      test_fundamentals_timeseries_adapter.py
      test_options_adapter.py
```

The exact directory names can be adjusted before implementation. The important rule is that endpoint-specific request construction is separated from shared evidence and analysis logic.

## 5. Study, run, request, and sample identifiers

Use four levels of identity.

### 5.1 Study ID

Identifies the planned experiment, such as:

```text
US_MARKET_TRANSITIONS_2026Q3
INTERNATIONAL_EXCHANGE_BASELINE_2026Q3
SECURITY_TYPE_FIELD_COVERAGE_2026Q3
```

A study can contain many runs.

### 5.2 Run ID

Identifies one execution of the capture application:

```text
2026-07-19T14-25-30.123Z_run-0001
```

### 5.3 Request ID

Stable ID from the input table:

```text
QUOTE_US_STOCK_MSFT_001
CHART_US_STOCK_MSFT_1D_1M_001
```

The same request ID can be repeated in multiple runs.

### 5.4 Sample ID

Unique ID for one attempted HTTP request and its final response:

```text
2026-07-19T14-25-30.123Z_run-0001_000001
```

Every raw response, metadata sidecar, flattened record, and analysis record must be traceable to one sample ID.

## 6. Common request-table columns

Every endpoint request table should begin with these columns:

| Column | Required | Purpose |
|---|---:|---|
| `request_id` | Yes | Stable identity for repeated comparisons |
| `enabled` | No | Include or exclude the row |
| `study_id` | Yes | Planned experiment grouping |
| `endpoint_id` | Yes | One of the seven project endpoint IDs |
| `request_subject` | Yes | Symbol, query text, screener ID, or another endpoint subject |
| `project_security_type` | No | Project classification |
| `expected_exchange` | No | Exchange expected for the subject |
| `market_state_target` | No | Intended observation window, not a claim about the response |
| `capture_label` | No | Human-readable sample grouping |
| `extra_parameters_json` | No | Forward-compatible endpoint parameters |
| `notes` | No | Human annotation |

Endpoint-specific columns follow the common columns.

## 7. Endpoint-specific request tables

Exact Yahoo URL paths and query-parameter names must be verified before implementation. The columns below describe the information the project must preserve, not yet the final Yahoo syntax.

### 7.1 Quote

```text
request_id
enabled
study_id
endpoint_id
symbol
project_security_type
expected_exchange
market_state_target
batch_group
notes
```

Initial controlled studies should use one symbol per request. Later studies may test multi-symbol batching separately because batching can change result omission and ordering behavior.

### 7.2 Chart

```text
request_id
enabled
study_id
endpoint_id
symbol
interval
range
period1_utc
period2_utc
include_prepost
events
project_security_type
expected_exchange
market_state_target
notes
```

Validation must require either a supported range or an explicit period pair, according to verified endpoint behavior.

### 7.3 QuoteSummary

```text
request_id
enabled
study_id
endpoint_id
symbol
modules
project_security_type
expected_exchange
market_state_target
notes
```

Module lists must be preserved exactly and in canonical order for comparison. Separate-module and combined-module requests should be supported.

### 7.4 Search

```text
request_id
enabled
study_id
endpoint_id
query_text
quotes_count
news_count
region
language
market_state_target
notes
```

Search studies must include exact symbols, company names, partial terms, ambiguous terms, exchange-suffixed symbols, and invalid queries.

### 7.5 Screener

```text
request_id
enabled
study_id
endpoint_id
screener_mode
screener_id
filters_json
sort_field
sort_order
offset
count
region
market_state_target
notes
```

Predefined and custom screener requests must remain distinguishable. Paging and sorting are part of the request identity.

### 7.6 Fundamentals Timeseries

```text
request_id
enabled
study_id
endpoint_id
symbols
metric_names
period1_utc
period2_utc
project_security_type
expected_exchange
market_state_target
notes
```

Metric names and date ranges must be preserved exactly. Single-metric and multi-metric requests should be tested separately.

### 7.7 Options

```text
request_id
enabled
study_id
endpoint_id
symbol
expiration_date
include_all_expirations
project_security_type
expected_exchange
market_state_target
notes
```

Options studies should distinguish:

- the default or nearest available expiration;
- explicitly requested later expirations;
- invalid, unavailable, and expired dates;
- underlyings with no listed options;
- calls and puts;
- stocks, ETFs, and indexes where supported;
- regular-session, extended-hours, closed-market, weekend, and expiration-day captures; and
- changing contract lists, strikes, bid/ask values, last prices, volume, open interest, and implied volatility.

The raw option chain must remain intact. Comparison logic should identify contracts by contract symbol when present and otherwise by a documented composite identity such as option type, expiration, and strike.

## 8. Shared capture procedure

For every enabled request:

1. Validate the input row.
2. Assign run, sequence, and sample IDs.
3. Prepare or reuse the approved anonymous Yahoo session.
4. Construct the request through the endpoint adapter.
5. Record the UTC request time.
6. Send the request.
7. Apply the existing retry and authentication-refresh rules.
8. Record the UTC response time and elapsed milliseconds.
9. Save the final response body byte-for-byte.
10. Compute SHA-256 and byte count.
11. Write the metadata sidecar.
12. Classify the response.
13. Parse valid JSON without changing the raw file.
14. Flatten the JSON deterministically.
15. Update the run manifest immediately.
16. Continue to the next request unless an unrecoverable run-level error occurs.

The normal inter-request pause remains configurable. A zero-millisecond default does not remove retry backoff, timeout handling, or rate-limit safeguards.

## 9. Raw evidence rules

The raw response is immutable evidence.

Rules:

- Do not add headers, comments, symbols, or timestamps inside the response.
- Do not pretty-print or reserialize it.
- Do not remove error payloads.
- Do not replace a failed response with `{}` or `[]`.
- Store non-JSON response bodies exactly as returned.
- Record the content type even when it conflicts with the body.
- Store request information in metadata, not in the raw body.
- Redact crumbs, cookies, authorization values, tokens, and sensitive session data.
- Never store a local absolute path in public evidence.
- Use repository-relative paths in manifests.

Proposed filename:

```text
<sequence>_<request-id-safe>_<endpoint-id>_<received-utc>.raw.json
```

Use the actual content extension when the response is not JSON.

## 10. Mandatory sample metadata

Each sample metadata record should include at least:

```json
{
  "capture_schema_version": "0.5.0",
  "study_id": "",
  "run_id": "",
  "sample_id": "",
  "sequence": 1,
  "request_id": "",
  "utility_version": "",
  "endpoint_id": "",
  "request_subject": "",
  "requested_symbols": [],
  "returned_symbols": [],
  "returned_entities": [],
  "project_security_type": "",
  "expected_exchange": "",
  "market_state_target": "",
  "request_parameters_canonical": {},
  "request_fingerprint_sha256": "",
  "requested_at_utc": "",
  "response_received_at_utc": "",
  "elapsed_ms": 0,
  "http_status": null,
  "content_type": "",
  "response_bytes": 0,
  "raw_response_file": "",
  "raw_response_sha256": "",
  "result_classification": "",
  "json_parse_status": "",
  "error_message": null,
  "request_url_redacted": "",
  "authentication": {
    "mode": "",
    "strategy": "",
    "refresh_performed": false,
    "sensitive_values_persisted": false
  }
}
```

The canonical request parameters and their SHA-256 fingerprint allow repeated samples to be compared only when the actual request is equivalent.

## 11. JSON flattening model

Generate one long-form field record per scalar leaf, explicit null, empty array, and empty object.

Required columns:

| Column | Purpose |
|---|---|
| `study_id` | Experiment grouping |
| `run_id` | Capture execution |
| `sample_id` | One attempted request |
| `request_id` | Stable repeated-request identity |
| `endpoint_id` | Endpoint family |
| `request_subject` | Symbol, query, screener, or metric subject |
| `returned_entity` | Returned symbol or endpoint-specific result identity |
| `evidence_json_path` | Full path including exact array indexes |
| `comparison_json_path` | Normalized path when safe, such as `result[].symbol` |
| `field_name` | Leaf name |
| `array_identity` | Endpoint-specific identity for an array item when available |
| `json_type` | string, number, boolean, null, array, or object |
| `presence_state` | Explicit state defined below |
| `raw_value_json` | Valid compact JSON representation of the raw value |
| `raw_value_text` | Spreadsheet-safe text representation |
| `decoded_utc` | Optional timestamp decoding |
| `master_field_status` | Known, new, conflicting, deprecated, or unreviewed |
| `notes` | Review annotation |

### 11.1 Presence states

Use distinct values:

```text
PRESENT_VALUE
PRESENT_ZERO
PRESENT_FALSE
PRESENT_EMPTY_STRING
PRESENT_EMPTY_ARRAY
PRESENT_EMPTY_OBJECT
PRESENT_EXPLICIT_NULL
MISSING_EXPECTED_PATH
NOT_EXPECTED_FOR_ENDPOINT_OR_TYPE
NOT_EVALUATED
```

Zero and false are valid values, not missing values.

### 11.2 Spreadsheet precision rule

Excel can alter long numbers and scientific notation. Therefore:

- `raw_value_json` is authoritative within normalized output;
- `raw_value_text` must be written as text;
- optional numeric helper columns may be generated for analysis;
- timestamps must retain their original raw epoch value;
- decoded dates must never replace raw values.

## 12. Analysis database

The primary analytical storage should be long-form CSV tables. Matrices and workbooks are generated views.

### 12.1 `samples.csv`

One row per sample, containing request, timing, HTTP, classification, and file references.

### 12.2 `requests.csv`

One row per canonical request definition, including its fingerprint.

### 12.3 `fields-long.csv`

One row per observed JSON path/value/state.

### 12.4 `field-catalog.csv`

One row per endpoint and comparison JSON path:

```text
endpoint_id
comparison_json_path
field_name
observed_json_types
interpreted_type
units
working_definition
first_seen_utc
last_seen_utc
sample_count
present_count
null_count
empty_count
security_types_seen
exchanges_seen
market_states_seen
review_status
confidence_level
notes
```

### 12.5 `field-occurrence-long.csv`

One row for every evaluated sample/path pair:

```text
sample_id
endpoint_id
comparison_json_path
presence_state
json_type
value_fingerprint
```

This is the authoritative source for occurrence matrices.

### 12.6 `field-occurrence-matrix.csv`

Generated pivot:

- rows: endpoint plus comparison path;
- columns: sample IDs or grouped study labels;
- values: compact state codes.

Suggested codes:

```text
V = present value
0 = present zero
F = present false
S = empty string
A = empty array
O = empty object
N = explicit null
M = missing expected
X = not expected
? = not evaluated
```

## 13. Workbook design

Generate the workbook from validated CSV tables, never directly from ad hoc spreadsheet edits.

Recommended sheets:

1. `README`
2. `Studies`
3. `Runs`
4. `Requests`
5. `Samples`
6. `Field_Catalog`
7. `Field_Values_Long`
8. `Field_Occurrence_Long`
9. `Occurrence_Matrix`
10. `Type_Changes`
11. `Missing_Null_Empty`
12. `Symbol_Comparison`
13. `Security_Type_Comparison`
14. `Exchange_Comparison`
15. `Market_State_Comparison`
16. `Endpoint_Summary`
17. `Unmapped_Fields`
18. `Validation_Findings`

The workbook is an analysis view. Raw JSON, metadata, hashes, and validated CSV tables remain the source evidence.

## 14. Comparison dimensions

The system must support grouping and comparison by:

- endpoint;
- exact request fingerprint;
- symbol;
- returned symbol;
- security type;
- exchange;
- region;
- currency;
- market state returned by Yahoo;
- intended capture window;
- regular, premarket, and postmarket periods;
- weekday, weekend, holiday, and half-day;
- request date and time;
- endpoint parameter combination;
- QuoteSummary module;
- Chart interval and range;
- Fundamentals Timeseries metric;
- Options underlying, expiration, option type, strike, and contract symbol;
- Search query type;
- Screener definition and page;
- JSON path;
- JSON type; and
- presence state.

## 15. Result classification

The existing result classifications should be retained and expanded only where endpoint behavior requires it.

Common classifications:

```text
SUCCESS_RESULT_RETURNED
SUCCESS_EMPTY_RESULT
REQUESTED_SYMBOL_MISSING_FROM_RESULT
RETURNED_SUBJECT_MISMATCH
PARTIAL_RESULT_RETURNED
HTTP_ERROR
PARSE_ERROR
RATE_LIMIT_OR_THROTTLE
AUTHENTICATION_ERROR
NETWORK_ERROR
ENDPOINT_PARAMETER_REJECTED
UNKNOWN_REQUIRES_RETEST
```

Field-level missing and null states belong in field occurrence records, not as substitutes for the overall sample result.

## 16. Validation requirements

A completed run is valid only when the validator confirms:

- manifest readability and schema;
- run completion state;
- request sequence and count;
- request-table-to-manifest consistency;
- sample ID uniqueness;
- request fingerprint consistency;
- raw file existence;
- SHA-256 and byte-count agreement;
- metadata-to-manifest agreement;
- JSON parse status;
- referenced file safety;
- no unreferenced evidence files;
- no duplicate file references;
- no absolute local paths;
- no unredacted credentials or session values;
- flattened-row reproducibility from raw JSON;
- deterministic normalized ordering;
- field occurrence completeness;
- workbook/CSV source consistency; and
- endpoint-adapter-specific result checks.

## 17. Test strategy

### 17.1 Offline tests

Use fixture responses for:

- successful populated results;
- empty results;
- omitted requested symbols;
- explicit nulls;
- empty arrays and objects;
- malformed JSON;
- non-JSON error pages;
- HTTP 401, 403, 429, and retryable 5xx;
- pagination;
- module variation;
- option expirations, calls, puts, contract identity, and symbols without options;
- array identity and ordering;
- large integer and decimal precision;
- sensitive-value redaction; and
- deterministic regeneration.

### 17.2 Live smoke tests

Begin with at least one minimal verified request per endpoint.

Do not start broad market-state testing until each endpoint adapter can:

- capture successfully;
- preserve raw evidence;
- validate the run;
- flatten deterministically; and
- regenerate identical analysis tables.

### 17.3 Controlled coverage tests

After smoke tests:

1. security-type baseline;
2. exchange baseline;
3. market-state transition baseline;
4. endpoint parameter variations;
5. repeated-day stability;
6. field occurrence and type comparison; and
7. long-term change tracking.

## 18. Implementation sequence

### Stage A — Specification and live endpoint verification

- confirm the seven endpoint families;
- verify current URL patterns;
- verify required and optional parameters;
- determine session/crumb requirements per endpoint;
- obtain one successful raw response for each endpoint;
- document endpoint-specific empty and error behavior.

### Stage B — Shared engine extraction

- preserve the tested v0.4.3 Quote capture behavior;
- separate shared HTTP/evidence logic from Quote-specific behavior;
- create the endpoint adapter interface;
- retain existing privacy and validation protections.

### Stage C — Seven endpoint adapters

Recommended order:

1. Quote migration into the adapter framework;
2. Chart;
3. QuoteSummary;
4. Fundamentals Timeseries;
5. Options;
6. Search;
7. Screener.

The first five are primarily symbol, metric, or underlying based. Search and Screener require more distinct result-identity and paging logic. Options also requires stable contract-level identities across expirations and repeated samples.

### Stage D — Long-form normalization

- generate samples, requests, and fields-long tables;
- preserve exact indexed evidence paths;
- generate safe comparison paths;
- add presence states and type tracking.

### Stage E — Field occurrence and workbook generation

- generate the field catalog;
- generate long-form occurrence data;
- generate occurrence matrices;
- generate the analysis workbook;
- validate workbook source consistency.

## 19. First concrete deliverable

The first implementation package should not yet be the full seven-endpoint downloader.

It should contain:

1. this approved architecture specification;
2. a verified endpoint registry with current URL and parameter patterns;
3. seven request-table templates;
4. a sample unified manifest schema;
5. a sample long-form field schema;
6. an endpoint-adapter interface specification; and
7. a live-verification checklist for at least one request per endpoint.

Only after those files are reviewed should the existing Quote utility be refactored.

## 20. Decisions established or requiring final review

1. Include both `fundamentals-timeseries` and `options`, producing seven v0.5.0 endpoint families.
2. Use one shared application with seven adapters rather than seven standalone implementations.
3. Use endpoint-specific CSV request tables with a forward-compatible `extra_parameters_json` column.
4. Treat long-form CSV tables as the analytical source and the workbook as a generated view.
5. Preserve exact array-indexed evidence paths and generate separate normalized comparison paths.
6. Use the four-level identity model: study, run, request, and sample.
7. Use stable endpoint-specific array identities, including option contract symbols when available.
8. Verify all live endpoint URL and parameter details before implementation.
