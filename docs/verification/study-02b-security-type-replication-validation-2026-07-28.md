# Study 02B Security-Type Replication Validation — 2026-07-28

## Run identity

- Run ID: `2026-07-28T23-07-53.433Z_study-02b-security-type-replication`
- Study: `study-02b-security-type-replication` v`0.1.0`
- Tool version: `0.1.0`
- Study schema: `0.5.0`
- Session mode: `cookie-crumb`
- Planned requests: `24`
- Security-type pairs: `12`
- Representatives per pair: `2`

## Validation result

**PASS** — the live Study 02B archive is complete, internally consistent,
portable through its resolved definition, and free of persisted Yahoo session secrets.

Validated outcomes:

- evidence records: `24/24`
- HTTP responses: `24/24`
- requested symbols returned: `24/24`
- expected `quoteType` matches: `24/24`
- returned `quoteType` agreement within pairs: `12/12`
- complete pairs: `12/12`
- retries: `0`
- authentication refreshes: `0`
- persisted sensitive values: `0`

## Integrity checks

The following checks passed:

- ZIP CRC integrity;
- one raw response and one metadata sidecar for each of 24 subjects;
- exact manifest-to-sidecar equality;
- raw byte-count verification;
- raw-response SHA-256 verification;
- valid JSON for every raw response;
- canonical parsed-JSON SHA-256 verification;
- request-parameter fingerprint verification;
- resolved-definition SHA-256 verification;
- source-definition SHA-256 verification against the Windows CRLF checkout;
- complete 24-row `security-type-results.csv`;
- complete 7-row `quote-type-summary.csv`;
- complete 12-row `security-type-pair-summary.csv`;
- pair-summary recomputation;
- sequential configured request order;
- crumb redaction in stored URLs;
- absence of stored cookie or authorization values; and
- analyzer compatibility.

Capture ZIP SHA-256:

```text
fd214edb392c9b2960876a85ca5c3d5556db9de28c38dfa496aa9b6963ef2814
```

The source-definition hash differs from the repository's LF byte representation only
because the Windows checkout uses CRLF line endings. The manifest hash exactly matches
the CRLF file used for the live run. The portable resolved-definition hash also passes.

## Pair results

| Project category | Baseline | Replication | Yahoo type | Fields B/R | Shared | B-only | R-only | Jaccard | State B/R |
|---|---|---|---|---:|---:|---:|---:|---:|---|
| Common Stock | `AAPL` | `MSFT` | `EQUITY` | 87 / 87 | 87 | 0 | 0 | 1.000000 | POST / POST |
| REIT | `PSA` | `O` | `EQUITY` | 86 / 87 | 86 | 0 | 1 | 0.988506 | POST / POST |
| MLP / Special Equity | `PAA` | `EPD` | `EQUITY` | 87 / 87 | 87 | 0 | 0 | 1.000000 | POST / POST |
| Special Share Class | `BRK-B` | `BF-B` | `EQUITY` | 80 / 86 | 80 | 0 | 6 | 0.930233 | POST / POST |
| Broad-Market ETF | `SPY` | `VTI` | `ETF` | 76 / 76 | 76 | 0 | 0 | 1.000000 | POST / POST |
| Bond ETF | `SHY` | `BND` | `ETF` | 76 / 69 | 68 | 8 | 1 | 0.883117 | POST / POST |
| Closed-End Fund | `PDI` | `UTF` | `EQUITY` | 75 / 79 | 75 | 0 | 4 | 0.949367 | POST / POST |
| Mutual Fund | `VTSAX` | `FXAIX` | `MUTUALFUND` | 63 / 55 | 55 | 8 | 0 | 0.873016 | POST / POST |
| Market Index | `^GSPC` | `^DJI` | `INDEX` | 58 / 58 | 58 | 0 | 0 | 1.000000 | POST / POST |
| Currency Pair | `EURUSD=X` | `GBPUSD=X` | `CURRENCY` | 58 / 58 | 58 | 0 | 0 | 1.000000 | REGULAR / REGULAR |
| Cryptocurrency | `BTC-USD` | `ETH-USD` | `CRYPTOCURRENCY` | 67 / 67 | 67 | 0 | 0 | 1.000000 | REGULAR / REGULAR |
| Futures Contract | `CL=F` | `GC=F` | `FUTURE` | 63 / 63 | 63 | 0 | 0 | 1.000000 | REGULAR / REGULAR |

Seven of twelve pairs had identical top-level field sets. Six pairs also had identical
normalized nested JSON-path sets. The Common Stock top-level sets were identical, but
the nested paths differed because `AAPL` had an empty `corporateActions` array while
`MSFT` had a populated dividend action.

## Analyzer result

The existing endpoint analyzer processed the live run without modification:

- endpoint families: `1`
- samples: `24`
- flattened field rows: `1,788`
- catalog JSON paths: `117`
- occurrence rows: `2,808`
- matrix rows: `117`
- type conflicts: `0`

## Replicated field findings

### Cryptocurrency

The same 12 paths occurred in both `BTC-USD` and `ETH-USD` and in none of the other
22 subjects:

- `circulatingSupply`
- `coinImageUrl`
- `coinMarketCapLink`
- `fromCurrency`
- `lastMarket`
- `logoUrl`
- `maxSupply`
- `startDate`
- `toCurrency`
- `totalSupply`
- `volume24Hr`
- `volumeAllCurrencies`

This promotes the Study 02A cryptocurrency cluster from a single-symbol observation
to a two-symbol replicated observation.

### Futures

The same seven paths occurred in both `CL=F` and `GC=F` and in none of the other
22 subjects:

- `contractSymbol`
- `expireDate`
- `expireIsoDate`
- `headSymbolAsString`
- `openInterest`
- `underlyingExchangeSymbol`
- `underlyingSymbol`

This likewise promotes the Study 02A futures cluster to a two-symbol replicated
observation.

### Fund-oriented fields

All six ETF and mutual-fund subjects — `SPY`, `VTI`, `SHY`, `BND`, `VTSAX`, and
`FXAIX` — shared:

- `netAssets`
- `netExpenseRatio`
- `trailingThreeMonthReturns`
- `ytdReturn`

All four ETF subjects, but neither mutual fund, also shared:

- `trailingThreeMonthNavReturns`

This strengthens the evidence for a broad fund-oriented field cluster and an
ETF-specific NAV-return field.

### Equity analyst and earnings fields

The following five fields replicated across both representatives of Common Stock,
REIT, MLP / Special Equity, and Special Share Class:

- `earningsTimestamp`
- `epsCurrentYear`
- `epsForward`
- `forwardPE`
- `priceEpsCurrentYear`

They did not occur in either closed-end-fund subject, even though both closed-end
funds returned Yahoo `quoteType=EQUITY`.

`averageAnalystRating` replicated for Common Stock, REIT, and MLP / Special Equity,
but not for Special Share Class because it was absent from `BRK-B` and present in
`BF-B`.

### Corporate actions are event-sensitive

The capture confirms that `corporateActions` cannot yet be treated as a permanent
security-type field:

- `AAPL` and `PDI` contained empty `corporateActions` arrays;
- `MSFT` and `UTF` contained populated dividend actions;
- both `PAA` and `EPD` contained populated dividend actions, each with a July 31,
  2026 ex-date; and
- the MLP pair therefore matched exactly at the nested path level during this run.

The MLP replication is noteworthy, but another capture outside the dividend-announcement
window is required before treating populated corporate-action paths as structurally
characteristic of MLPs.

## Pair-specific differences

### REIT

`O` added only `displayName`. The pair otherwise matched, producing a Jaccard score
of `0.988506`.

### Special Share Class

`BF-B` added:

- `averageAnalystRating`
- `dividendDate`
- `dividendRate`
- `dividendYield`
- `earningsCallTimestampEnd`
- `earningsCallTimestampStart`

These are issuer- and event-sensitive fields, not share-class syntax fields.

### Bond ETF

`SHY` had eight fields not returned for `BND`:

- `bookValue`
- `epsTrailingTwelveMonths`
- `financialCurrency`
- `priceToBook`
- `sharesOutstanding`
- `trailingAnnualDividendRate`
- `trailingAnnualDividendYield`
- `trailingPE`

`BND` alone had `dividendDate`.

### Closed-End Fund

`UTF` added:

- `bookValue`
- `displayName`
- `financialCurrency`
- `priceToBook`

At the nested level, `UTF` also had a populated dividend action while `PDI` had an
empty `corporateActions` array.

### Mutual Fund

`VTSAX` had eight fields not returned for `FXAIX`:

- `bookValue`
- `epsTrailingTwelveMonths`
- `financialCurrency`
- `priceToBook`
- `sharesOutstanding`
- `trailingAnnualDividendRate`
- `trailingAnnualDividendYield`
- `trailingPE`

These fields therefore should not be treated as universal mutual-fund fields.

## Market-state observation

All 18 U.S. exchange, fund, and index subjects reported `POST`. Both currency pairs,
both cryptocurrencies, and both futures contracts reported `REGULAR`.

Every pair agreed internally on `marketState`. This is a clean time-of-capture
observation, not yet a general market-hours rule.

## Conclusion

Study 02B achieved its replication objective:

1. all 24 symbols resolved correctly;
2. all 24 expected Yahoo `quoteType` values matched;
3. all 12 pairs agreed on returned `quoteType`;
4. crypto and futures field clusters replicated cleanly;
5. fund-oriented fields replicated across ETF and mutual-fund representatives;
6. closed-end funds remained distinguishable from ordinary equities despite Yahoo's
   shared `EQUITY` classification;
7. several apparent differences were shown to be issuer- or event-sensitive; and
8. the existing analyzer processed the doubled study without changes or type conflicts.

**Status: Study 02B live replication complete and validated.**

The recommended next controlled study is **Study 03 — International Exchange and
Region Baseline**.
