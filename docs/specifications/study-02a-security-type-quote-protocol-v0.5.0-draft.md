# Study 02A Security-Type Quote Baseline — v0.5.0 Draft

## Status

Implementation-ready pilot protocol for comparing Quote endpoint field occurrence and
classification across reviewed project security types.

The study performs exactly 12 controlled single-symbol requests:

```text
12 reviewed subjects × 1 Quote request pattern × cookie-plus-crumb
```

## Controlled variable

The only intended study variable is `project_security_type`.

Every request uses:

- the same Quote endpoint;
- one symbol per request;
- `formatted=false`;
- `lang=en-US` and `region=US`;
- the prepared anonymous `cookie-crumb` mode confirmed in Study 01;
- sequential execution with no parallel requests; and
- a project-added pause of 0 ms unless deliberately overridden.

## Pilot panel

| Project security type | Symbol | Expected Yahoo `quoteType` | Selection role |
|---|---|---|---|
| Common Stock | `AAPL` | `EQUITY` | Study 01 continuity control |
| REIT | `PSA` | `EQUITY` | Equity-classified real-estate trust |
| MLP / Special Equity | `PAA` | `EQUITY` | Partnership structure |
| Special Share Class | `BRK-B` | `EQUITY` | Hyphenated share-class syntax |
| Broad-Market ETF | `SPY` | `ETF` | Liquid equity ETF |
| Bond ETF | `SHY` | `ETF` | Treasury-bond ETF |
| Closed-End Fund | `PDI` | `EQUITY` | Fund-like exchange-traded equity |
| Mutual Fund | `VTSAX` | `MUTUALFUND` | Daily NAV-style fund |
| Market Index | `^GSPC` | `INDEX` | Caret-bearing index symbol |
| Currency Pair | `EURUSD=X` | `CURRENCY` | Equals-sign FX symbol |
| Cryptocurrency | `BTC-USD` | `CRYPTOCURRENCY` | Twenty-four-hour market |
| Futures Contract | `CL=F` | `FUTURE` | Equals-sign futures symbol |

The panel is intentionally U.S.-region focused. International exchange suffixes such
as `INGA.AS` remain for Study 03 so exchange/region effects are not mixed into the
security-type pilot.

## Study definition

```text
config/studies/study-02a-security-type-quote.json
```

The configuration records each symbol's project category, expected Yahoo quote type,
broad expected exchange description, selection role, and reviewed source inventory.

Every run also writes:

```text
study-definition.resolved.json
```

The run manifest references this portable relative file and records both its SHA-256
and the source configuration SHA-256.

## Evidence structure

```text
captures/local/
  <UTC>_study-02a-security-type-quote/
    run-manifest.json
    study-definition.resolved.json
    raw/
    metadata/
    errors/
    comparison/
      security-type-results.csv
      quote-type-summary.csv
```

Each raw response remains unchanged. Metadata and comparison files contain the study
context.

## Per-subject comparison fields

The compact comparison table records:

```text
returned symbol
quoteType
quoteType expected/match state
typeDisp
exchange
fullExchangeName
currency
exchangeTimezoneName
marketState
market
regularMarketPrice
regularMarketTime
returned top-level field count
raw response SHA-256
canonical parsed-JSON SHA-256
```

The endpoint analyzer remains the authority for the complete normalized JSON-path
inventory and presence-state matrix.

## Result classification

- `EXPECTED_SYMBOL_RETURNED` — exact requested symbol found in `quoteResponse.result[]`.
- `EMPTY_RESULT` — valid Quote response with no result records.
- `REQUESTED_SYMBOL_MISSING_FROM_RESULT` — results exist but omit the requested symbol.
- `QUOTE_RESPONSE_ERROR` — Yahoo returned a Quote-level error object.
- `EXPECTED_TOP_LEVEL_MISSING` — valid JSON without `quoteResponse`.
- `HTTP_ERROR_JSON_RETURNED` — non-2xx HTTP response with JSON evidence.
- `HTTP_SUCCESS_JSON_PARSE_ERROR` — 2xx response whose body is not valid JSON.

A mismatch between expected and returned `quoteType` is evidence for review; it does
not invalidate the raw capture.

## Commands

From repository root:

```bat
rmdir /s /q .pytest-temp 2>nul
py -m pytest -q tests\test_security_type_quote_study.py --basetemp=".pytest-temp"
```

Review the 12-request plan:

```bat
py tools\security-type-study\run_security_type_quote_study.py --dry-run
```

Run the live pilot:

```bat
py tools\security-type-study\run_security_type_quote_study.py
```

Analyze the completed run:

```bat
py tools\endpoint-analysis\analyze_endpoint_captures.py "<study run folder>"
```

## Acceptance criteria

1. All 12 planned evidence records are written.
2. Every raw file matches its metadata byte count and raw SHA-256.
3. Every valid JSON body matches its canonical JSON SHA-256.
4. Every manifest request entry exactly matches its metadata sidecar.
5. `study-definition.resolved.json` exists and matches its manifest hash.
6. No cookie or crumb value is persisted.
7. Both comparison CSV files are complete and internally consistent.
8. The endpoint analyzer processes all 12 samples deterministically.
9. Returned `quoteType` and selected field occurrence are reviewed before expanding the panel.
