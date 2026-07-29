# Study 03 International Exchange and Region Validation — 2026-07-28

## Run identity

- Run ID: `2026-07-28T23-48-02.038Z_study-03-international-exchange-region`
- Study: `study-03-international-exchange-region` v`0.1.0`
- Tool version: `0.1.0`
- Study schema: `0.5.0`
- Session mode: `cookie-crumb`
- Planned requests: `12`
- Country or exchange panel entries: `12`
- Geographic regions: `5`
- Capture window: `2026-07-28T23:48:02.038Z` through `2026-07-28T23:48:04.172Z`

## Validation result

**PASS** — the live Study 03 archive is complete, internally consistent,
portable through its resolved definition, and free of persisted Yahoo session secrets.

Validated outcomes:

- evidence records: `12/12`
- HTTP responses: `12/12`
- requested symbols returned: `12/12`
- expected `quoteType=EQUITY` matches: `12/12`
- expected currency matches: `12/12`
- retries: `0`
- authentication refreshes: `0`
- persisted sensitive values: `0`

## Integrity checks

The following checks passed:

- ZIP CRC integrity;
- one raw response and one metadata sidecar for each of 12 subjects;
- exact manifest-to-sidecar equality;
- raw byte-count verification;
- raw-response SHA-256 verification;
- valid JSON for every raw response;
- canonical parsed-JSON SHA-256 verification;
- request-parameter fingerprint verification;
- resolved-definition SHA-256 verification;
- source-definition SHA-256 verification against the Windows CRLF checkout;
- complete 12-row `exchange-region-results.csv`;
- complete 10-row `currency-summary.csv`;
- complete 5-row `region-summary.csv`;
- comparison-summary recomputation;
- configured sequential request order;
- crumb redaction in every stored request URL;
- absence of stored cookie or authorization values; and
- compatibility with the existing endpoint analyzer.

Capture ZIP SHA-256:

```text
406c80242e51eb4df86a1eaf85ad6a896f2c3479ed2a255cec14603d39a8e4a9
```

The source-definition hash differs from the repository's LF byte representation only
because the Windows checkout uses CRLF line endings. The manifest hash exactly matches
the CRLF file used for the live run. The portable resolved-definition hash also passes.

## Panel results

| Market | Symbol | Yahoo exchange | Yahoo exchange name | Currency | Time zone | `marketState` | Top-level fields |
|---|---|---|---|---|---|---|---:|
| United States | `AAPL` | `NMS` | `NasdaqGS` | `USD` | `America/New_York` | `POST` | 87 |
| Canada | `RY.TO` | `TOR` | `Toronto` | `CAD` | `America/Toronto` | `POSTPOST` | 82 |
| United Kingdom | `HSBA.L` | `LSE` | `LSE` | `GBp` | `Europe/London` | `PREPRE` | 80 |
| Netherlands | `INGA.AS` | `AMS` | `Amsterdam` | `EUR` | `Europe/Amsterdam` | `PREPRE` | 81 |
| Germany | `SAP.DE` | `GER` | `XETRA` | `EUR` | `Europe/Berlin` | `PREPRE` | 81 |
| France | `AIR.PA` | `PAR` | `Paris` | `EUR` | `Europe/Paris` | `PREPRE` | 81 |
| Switzerland | `NESN.SW` | `EBS` | `Swiss` | `CHF` | `Europe/Zurich` | `PREPRE` | 81 |
| Japan | `7203.T` | `JPX` | `Tokyo` | `JPY` | `Asia/Tokyo` | `PREPRE` | 79 |
| Hong Kong | `0700.HK` | `HKG` | `HKSE` | `HKD` | `Asia/Hong_Kong` | `PREPRE` | 81 |
| Australia | `BHP.AX` | `ASX` | `ASX` | `AUD` | `Australia/Sydney` | `PRE` | 82 |
| Brazil | `VALE3.SA` | `SAO` | `São Paulo` | `BRL` | `America/Sao_Paulo` | `POSTPOST` | 81 |
| India | `RELIANCE.NS` | `NSI` | `NSE` | `INR` | `Asia/Kolkata` | `PREPRE` | 81 |

## Analyzer result

The existing endpoint analyzer processed the live run without modification:

- endpoint families: `1`
- samples: `12`
- flattened field rows: `993`
- catalog JSON paths: `95`
- occurrence rows: `1,140`
- matrix rows: `95`
- type conflicts: `0`

## Exchange, market, currency, and time-zone findings

The panel produced:

- `12` distinct Yahoo `exchange` codes;
- `12` distinct `fullExchangeName` values;
- `12` distinct `market` values;
- `12` distinct `exchangeTimezoneName` values; and
- `10` distinct currencies, because the Netherlands, Germany, and France
  representatives all returned `EUR`.

All expected currencies matched, including Yahoo's `GBp` convention for the London
listing.

The descriptive panel labels and Yahoo's returned names were not always textually
identical. `AAPL` returned `NasdaqGS` rather than the panel's broader `Nasdaq` label,
and `HSBA.L` returned `LSE` rather than `London Stock Exchange`. These are preserved
as returned-value observations, not treated as response failures.

## `marketState` findings

The live Quote responses contained four distinct exact strings:

| Exact returned value | Count | Symbols |
|---|---:|---|
| `POST` | 1 | `AAPL` |
| `POSTPOST` | 2 | `RY.TO`, `VALE3.SA` |
| `PRE` | 1 | `BHP.AX` |
| `PREPRE` | 8 | `HSBA.L`, `INGA.AS`, `SAP.DE`, `AIR.PA`, `NESN.SW`, `7203.T`, `0700.HK`, `RELIANCE.NS` |

`PREPRE` and `POSTPOST` are not transcription or normalization artifacts. They occur
verbatim in the raw Yahoo JSON, metadata sidecars, manifest, and comparison table.

They should therefore be retained as distinct observed `marketState` values pending
controlled transition testing. This run does not establish whether the duplicated
tokens are stable exchange-specific states, time-of-day states, backend artifacts,
or some combination.

Only `AAPL` included the four top-level post-market value fields:

- `postMarketChange`
- `postMarketChangePercent`
- `postMarketPrice`
- `postMarketTime`

The two `POSTPOST` subjects did not include those fields. In this capture,
the returned state string alone did not determine whether post-market value fields
were present.

## Field-structure findings

Across the 12 ordinary-equity records:

- top-level field counts ranged from `79` to `87`;
- the mean top-level field count was approximately `81.4`;
- `77` top-level fields occurred in every subject;
- `89` distinct top-level fields occurred across the union; and
- `12` top-level fields varied in occurrence.

The variable fields were:

- `displayName`
- `dividendDate`
- `earningsTimestamp`
- `epsCurrentYear`
- `fiftyTwoWeekLowChangePercent`
- `nameChangeDate`
- `postMarketChange`
- `postMarketChangePercent`
- `postMarketPrice`
- `postMarketTime`
- `prevName`
- `priceEpsCurrentYear`

Notable occurrence patterns:

- `displayName` and all four post-market fields appeared only for `AAPL`;
- `nameChangeDate` and `prevName` appeared only for `BHP.AX`;
- `dividendDate` appeared for `AAPL`, `RY.TO`, and `NESN.SW`;
- `earningsTimestamp` was absent only for `NESN.SW` and `BHP.AX`;
- `epsCurrentYear` and `priceEpsCurrentYear` were absent only for `7203.T`; and
- `fiftyTwoWeekLowChangePercent` was absent only for `HSBA.L`.

These differences are not sufficiently aligned by region to support a regional field
rule from this one-representative panel.

## Corporate-action finding

The top-level `corporateActions` array occurred for all 12 subjects. It was empty for
11 subjects and contained one dividend event for `RY.TO`.

The five populated child paths — action header, message, event type, date, and amount —
therefore appeared only for `RY.TO`. This is event-sensitive evidence rather than an
exchange- or region-specific structure.

## Interpretation limits

Study 03 is a one-subject-per-country or exchange baseline. It demonstrates that the
capture and analysis framework works across the 12 reviewed Yahoo symbol formats and
records exact exchange, market, currency, time-zone, field, and state differences.

It does not prove that any field difference is caused by geography or exchange.
Differences can also reflect issuer, sector, analyst coverage, corporate events,
local trading hours, Yahoo feed behavior, or capture timing.

A stable exchange- or region-level rule requires additional representatives, repeat
captures, or both.

## Conclusion

Study 03 achieved its baseline objective:

1. all 12 symbols resolved successfully;
2. all 12 returned `quoteType=EQUITY`;
3. all 12 expected currencies matched;
4. all raw, metadata, hash, comparison, and secret-redaction checks passed;
5. the analyzer processed all 12 international subjects without modification or type
   conflicts;
6. the exchange, market, currency, and time-zone fields remained structurally present
   across the entire panel; and
7. the exact additional `marketState` values `PREPRE` and `POSTPOST` were documented
   in reproducible raw evidence.

**Status: Study 03 live baseline complete and validated.**

The recommended next controlled study is **Study 04 — Market-State Transition
Study**, with repeated captures around local exchange session boundaries to test
`PRE`, `PREPRE`, `REGULAR`, `POST`, and `POSTPOST`, together with the appearance and
disappearance of extended-hours fields.
