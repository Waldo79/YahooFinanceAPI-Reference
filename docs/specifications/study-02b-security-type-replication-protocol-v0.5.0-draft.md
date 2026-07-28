# Study 02B Security-Type Replication — v0.5.0 Draft

## Status

Implementation-ready replication protocol for testing whether Study 02A Quote field
patterns recur in a second representative of each reviewed project security type.

The study performs exactly 24 controlled single-symbol requests:

```text
12 security-type pairs × 2 representatives × 1 Quote request pattern
```

## Objective

Study 02A was a one-symbol-per-category pilot. Study 02B keeps the same endpoint,
session mode, and request parameters while adding one independent representative per
category. This directly tests whether observed fields are more likely to be:

- security-type characteristics;
- issuer- or instrument-specific fields;
- event-time fields;
- market-state effects; or
- transient Yahoo backend variation.

## Controlled design

Every request uses:

- the Quote endpoint;
- one symbol per request;
- `formatted=false`;
- `lang=en-US` and `region=US`;
- the prepared anonymous `cookie-crumb` session mode;
- sequential configured order; and
- a project-added pause of 0 ms unless overridden.

Configured order is pair-by-pair, with the Study 02A baseline first and the new
replication subject second.

## Replication panel

| Project security type | Study 02A baseline | Study 02B replicate | Expected Yahoo `quoteType` |
|---|---|---|---|
| Common Stock | `AAPL` | `MSFT` | `EQUITY` |
| REIT | `PSA` | `O` | `EQUITY` |
| MLP / Special Equity | `PAA` | `EPD` | `EQUITY` |
| Special Share Class | `BRK-B` | `BF-B` | `EQUITY` |
| Broad-Market ETF | `SPY` | `VTI` | `ETF` |
| Bond ETF | `SHY` | `BND` | `ETF` |
| Closed-End Fund | `PDI` | `UTF` | `EQUITY` |
| Mutual Fund | `VTSAX` | `FXAIX` | `MUTUALFUND` |
| Market Index | `^GSPC` | `^DJI` | `INDEX` |
| Currency Pair | `EURUSD=X` | `GBPUSD=X` | `CURRENCY` |
| Cryptocurrency | `BTC-USD` | `ETH-USD` | `CRYPTOCURRENCY` |
| Futures Contract | `CL=F` | `GC=F` | `FUTURE` |

International exchange suffixes remain reserved for Study 03 so region and exchange
are not introduced as a second controlled variable.

## Study definition

```text
config/studies/study-02b-security-type-replication.json
```

Each subject records `pair_id` and `representative_role`. The definition must contain
exactly 12 pairs, and every pair must contain one `baseline` and one `replication`
subject with the same project security type and expected Yahoo `quoteType`.

Every run writes a portable `study-definition.resolved.json`, along with source and
resolved-definition SHA-256 values in the manifest.

## Evidence structure

```text
captures/local/
  <UTC>_study-02b-security-type-replication/
    run-manifest.json
    study-definition.resolved.json
    raw/
    metadata/
    errors/
    comparison/
      security-type-results.csv
      quote-type-summary.csv
      security-type-pair-summary.csv
```

The pair summary records returned quote-type agreement, field-count differences,
top-level field intersections, baseline-only fields, replication-only fields, a
Jaccard overlap value, and the two observed `marketState` values. The endpoint analyzer
remains the authority for the complete normalized JSON-path inventory.

## Result classification

The response classifications remain unchanged from Study 02A:

- `EXPECTED_SYMBOL_RETURNED`
- `EMPTY_RESULT`
- `REQUESTED_SYMBOL_MISSING_FROM_RESULT`
- `QUOTE_RESPONSE_ERROR`
- `EXPECTED_TOP_LEVEL_MISSING`
- `HTTP_ERROR_JSON_RETURNED`
- `HTTP_SUCCESS_JSON_PARSE_ERROR`

A quote-type or field-occurrence disagreement is evidence for review and never causes
the raw response to be discarded.

## Commands

From repository root:

```bat
rmdir /s /q .pytest-temp 2>nul
py -m pytest -q tests\test_security_type_replication_study.py --basetemp=".pytest-temp"
```

Review the 24-request plan:

```bat
py tools\security-type-study\run_security_type_replication_study.py --dry-run
```

Run the live study:

```bat
py tools\security-type-study\run_security_type_replication_study.py
```

Analyze the completed run:

```bat
py tools\endpoint-analysis\analyze_endpoint_captures.py "<study run folder>"
```

## Acceptance criteria

1. All 24 planned evidence records are written.
2. All 12 configured pairs are complete.
3. Every raw byte count, raw SHA-256, and canonical JSON SHA-256 validates.
4. Every manifest request exactly matches its metadata sidecar.
5. The resolved study definition exists, is portable, and matches its manifest hash.
6. No cookie, crumb, authorization value, or token is persisted.
7. All three comparison CSV files are complete and internally consistent.
8. The endpoint analyzer processes all 24 samples deterministically.
9. Pair-level field similarities and differences are reviewed before any field is
   promoted from an observation to a stable security-type rule.
