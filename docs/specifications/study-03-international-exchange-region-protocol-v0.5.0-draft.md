# Study 03 International Exchange and Region Baseline — v0.5.0 Draft

## Status

Implementation-ready protocol for observing how Yahoo Finance Quote responses vary
across primary ordinary-equity listings from twelve country and exchange markets.

The study performs exactly 12 controlled single-symbol requests:

```text
12 country/exchange panel entries × 1 ordinary-equity representative × 1 Quote pattern
```

## Research question

When the endpoint, request parameters, session mode, expected Yahoo `quoteType`, and
single-symbol request structure are held constant, which returned Quote fields and
values vary across listing exchanges and geographic markets?

## Controlled variables

Every request uses:

- endpoint: `query1.finance.yahoo.com/v7/finance/quote`;
- method: `GET`;
- parameters: `formatted=false`, `lang=en-US`, and `region=US`;
- one requested symbol;
- prepared anonymous cookie-plus-crumb session;
- expected top level: `quoteResponse`;
- expected Yahoo `quoteType`: `EQUITY`;
- default inter-request pause: `0 ms`;
- the existing retry and one-time authentication-refresh safeguards; and
- the same evidence, hashing, metadata, redaction, and analyzer conventions used in
  Studies 01, 02A, and 02B.

The primary study variable is the subject's primary listing exchange and country or
market.

## Subject panel

| Sequence | Market | Region | Yahoo symbol | Suffix | Panel exchange label | Expected currency |
|---:|---|---|---|---|---|---|
| 1 | United States | North America | `AAPL` | none | Nasdaq | `USD` |
| 2 | Canada | North America | `RY.TO` | `.TO` | Toronto | `CAD` |
| 3 | United Kingdom | Europe | `HSBA.L` | `.L` | London Stock Exchange | `GBp` |
| 4 | Netherlands | Europe | `INGA.AS` | `.AS` | Amsterdam | `EUR` |
| 5 | Germany | Europe | `SAP.DE` | `.DE` | XETRA | `EUR` |
| 6 | France | Europe | `AIR.PA` | `.PA` | Paris | `EUR` |
| 7 | Switzerland | Europe | `NESN.SW` | `.SW` | Swiss | `CHF` |
| 8 | Japan | Asia | `7203.T` | `.T` | Tokyo | `JPY` |
| 9 | Hong Kong | Asia | `0700.HK` | `.HK` | HKSE | `HKD` |
| 10 | Australia | Oceania | `BHP.AX` | `.AX` | ASX | `AUD` |
| 11 | Brazil | South America | `VALE3.SA` | `.SA` | São Paulo | `BRL` |
| 12 | India | Asia | `RELIANCE.NS` | `.NS` | NSE | `INR` |

`AAPL` is the unsuffixed U.S. continuity control. `INGA.AS` is the international
symbol previously reserved for the exchange and region study.

## Selection rules

Each subject must:

1. be a reviewed primary ordinary-equity listing rather than a U.S. ADR;
2. resolve under the documented Yahoo symbol;
3. be expected to return Yahoo `quoteType=EQUITY`;
4. provide a distinct country/exchange panel entry;
5. preserve symbol-format diversity, including alphabetic, numeric, zero-padded,
   numbered-share-class, and multiple Yahoo suffix patterns; and
6. remain unchanged during a single live run.

The panel was reviewed against current Yahoo Finance listing pages on July 28, 2026.
The live capture remains the authoritative evidence for what the Quote endpoint
actually returns at run time.

## Primary recorded values

For each subject, the comparison table records:

- requested and returned symbol;
- expected and returned `quoteType`;
- panel exchange label;
- Yahoo `exchange`;
- Yahoo `fullExchangeName`;
- expected and returned currency;
- currency agreement;
- `exchangeTimezoneName`;
- `market`;
- `marketState`;
- regular-market price and timestamp;
- top-level field count;
- raw and canonical hashes;
- request-parameter fingerprint;
- attempt and authentication-refresh counts; and
- evidence file paths.

## Generated comparison files

```text
comparison/exchange-region-results.csv
comparison/region-summary.csv
comparison/currency-summary.csv
```

The generic endpoint analyzer can also generate:

```text
fields-long.csv
field-catalog.csv
field-occurrence-long.csv
field-occurrence-matrix.csv
samples.csv
type-conflicts.csv
validation.json
```

## Evidence layout

```text
<run-id>_study-03-international-exchange-region/
  raw/
  metadata/
  errors/
  comparison/
  study-definition.resolved.json
  run-manifest.json
```

Raw Yahoo response bytes are never modified. Cookies, crumbs, authorization values,
tokens, and local absolute paths must not be persisted in public evidence.

## Validation gates

A successful live run should satisfy:

1. exactly 12 planned requests;
2. exactly 12 raw responses and 12 metadata sidecars;
3. all source and resolved-definition hashes verify;
4. all raw byte counts and raw SHA-256 values verify;
5. all valid JSON bodies reproduce their canonical JSON hashes;
6. every stored request URL has the crumb redacted;
7. no cookie or authorization values are stored;
8. all 12 requested symbols are returned;
9. all 12 returned `quoteType` values match `EQUITY`;
10. expected and returned currencies are explicitly compared rather than assumed;
11. all three comparison CSVs contain their expected rows; and
12. the existing endpoint analyzer completes without modification.

A currency mismatch or exchange-label difference is evidence to preserve, not a
reason to discard an otherwise valid response.

## Interpretation limits

This is a one-subject-per-country/exchange baseline, not a causal proof that every
difference is caused by region or exchange. Differences can also reflect:

- issuer or sector;
- company size and analyst coverage;
- corporate events;
- local trading hours and market state;
- local currency conventions, including pence versus pounds;
- exchange-specific feed behavior;
- Yahoo backend variation; or
- a combination of those factors.

A stable exchange- or region-level rule requires replication with additional
ordinary equities, repeat captures, or both.

## Commands

From the repository root:

```text
py -m pytest -q tests\test_exchange_region_quote_study.py --basetemp=".pytest-temp"
```

```text
py tools\exchange-region-study\run_exchange_region_quote_study.py --dry-run
```

```text
py tools\exchange-region-study\run_exchange_region_quote_study.py
```

Analyze a completed run:

```text
py tools\endpoint-analysis\analyze_endpoint_captures.py "captures\local\<run-folder>"
```

## Completion criterion

Study 03 is complete only after:

- offline tests pass;
- the 12-request dry run is reviewed;
- a live run is completed;
- the full run archive passes independent integrity validation;
- field and value findings are documented with interpretation limits; and
- a validation report is committed to `docs/verification/`.
