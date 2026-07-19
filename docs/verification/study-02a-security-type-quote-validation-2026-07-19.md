# Study 02A Security-Type Quote Validation — 2026-07-19

## Run identity

- Run ID: `2026-07-19T04-06-57.736Z_study-02a-security-type-quote`
- Study: `study-02a-security-type-quote-baseline` v`0.1.0`
- Tool version: `0.1.0`
- Session mode: `cookie-crumb`
- Planned requests: `12`
- Evidence records written: `12`
- HTTP responses: `12`
- Exact requested symbols returned: `12`
- Expected `quoteType` matches: `12`
- `quoteType` mismatches: `0`

## Validation result

**PASS** — the live Study 02A archive is complete, internally consistent, portable,
and free of persisted Yahoo session secrets.

The following checks passed:

- ZIP integrity;
- one raw response and one metadata sidecar for every planned subject;
- exact manifest-to-sidecar equality for all 12 requests;
- raw byte-count verification;
- raw SHA-256 verification;
- valid JSON for all 12 raw responses;
- canonical parsed-JSON SHA-256 verification;
- portable `study-definition.resolved.json`;
- resolved-definition SHA-256 verification;
- complete 12-row `security-type-results.csv`;
- complete 7-row `quote-type-summary.csv`;
- comparison-table consistency with the metadata;
- sequential configured request order;
- one attempt per request, with no authentication refresh;
- redacted crumb query parameters; and
- no persisted cookie, authorization value, or unredacted crumb.

## Returned classifications

| Symbol | Project security type | Yahoo `quoteType` | `typeDisp` | Exchange code | Full exchange | Time zone | `marketState` | Quote-object fields |
|---|---|---|---|---|---|---|---|---:|
| `AAPL` | Common Stock | `EQUITY` | Equity | `NMS` | NasdaqGS | America/New_York | `CLOSED` | 87 |
| `PSA` | REIT | `EQUITY` | Equity | `NYQ` | NYSE | America/New_York | `CLOSED` | 86 |
| `PAA` | MLP / Special Equity | `EQUITY` | Equity | `NMS` | NasdaqGS | America/New_York | `CLOSED` | 87 |
| `BRK-B` | Special Share Class | `EQUITY` | Equity | `NYQ` | NYSE | America/New_York | `CLOSED` | 81 |
| `SPY` | Broad-Market ETF | `ETF` | ETF | `PCX` | NYSEArca | America/New_York | `CLOSED` | 76 |
| `SHY` | Bond ETF | `ETF` | ETF | `NGM` | NasdaqGM | America/New_York | `CLOSED` | 76 |
| `PDI` | Closed-End Fund | `EQUITY` | Equity | `NYQ` | NYSE | America/New_York | `CLOSED` | 75 |
| `VTSAX` | Mutual Fund | `MUTUALFUND` | Fund | `NAS` | Nasdaq | America/New_York | `CLOSED` | 63 |
| `^GSPC` | Market Index | `INDEX` | Index | `SNP` | SNP | America/New_York | `CLOSED` | 58 |
| `EURUSD=X` | Currency Pair | `CURRENCY` | Currency | `CCY` | CCY | Europe/London | `CLOSED` | 58 |
| `BTC-USD` | Cryptocurrency | `CRYPTOCURRENCY` | Cryptocurrency | `CCC` | CCC | UTC | `REGULAR` | 67 |
| `CL=F` | Futures Contract | `FUTURE` | Futures | `NYM` | NY Mercantile | America/New_York | `REGULAR` | 63 |

The expected exchange descriptions in the study manifest were broad review labels.
The table above preserves Yahoo's exact returned exchange fields.

## Endpoint-analyzer result

The existing endpoint analyzer processed all 12 live samples successfully:

- Endpoint families: `1`
- Samples: `12`
- Flattened field rows: `893`
- Catalog JSON paths: `117`
- Occurrence rows: `1,404`
- Matrix rows: `117`
- Type conflicts: `0`

Forty-seven catalog paths were present in all 12 samples.

## Initial field-occurrence findings

### Fields characteristic of the common-equity controls

Six fields occurred in `AAPL`, `PSA`, `PAA`, and `BRK-B`, but not in the ETF,
closed-end-fund, mutual-fund, index, currency, cryptocurrency, or futures samples:

- `averageAnalystRating`
- `earningsTimestamp`
- `epsCurrentYear`
- `epsForward`
- `forwardPE`
- `priceEpsCurrentYear`

This does not mean every Yahoo `EQUITY` record always has those fields. Notably, the
closed-end fund `PDI` also returned `quoteType=EQUITY` but did not share this exact
analyst/earnings field set.

### Fund-oriented fields

`SPY`, `SHY`, and `VTSAX` shared:

- `netAssets`
- `netExpenseRatio`
- `trailingThreeMonthReturns`
- `ytdReturn`

The two ETFs, `SPY` and `SHY`, additionally shared `trailingThreeMonthNavReturns`.

This is useful evidence that field applicability is finer-grained than the top-level
Yahoo `quoteType`: ETF and mutual-fund records overlap, but they are not identical.

### Closed-end-fund treatment

`PDI` returned:

```text
quoteType = EQUITY
typeDisp  = Equity
exchange  = NYQ
```

Its 75-field Quote object was less extensive than the four common-equity controls and
did not carry the same complete analyst/earnings subset. The project should continue
to retain **Closed-End Fund** as its own project category even though Yahoo classifies
this sample as `EQUITY`.

### PAA corporate-action fields

Five nested corporate-action paths appeared only in the `PAA` sample:

- `corporateActions[].header`
- `corporateActions[].message`
- `corporateActions[].meta.amount`
- `corporateActions[].meta.dateEpochMs`
- `corporateActions[].meta.eventType`

These are observation-time fields, not necessarily permanent MLP characteristics.
They should be retested before being classified as security-type fields.

### Cryptocurrency-specific fields

Twelve paths appeared only in `BTC-USD`:

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

This is a strong initial cryptocurrency-specific field cluster.

### Futures-specific fields

Seven paths appeared only in `CL=F`:

- `contractSymbol`
- `expireDate`
- `expireIsoDate`
- `headSymbolAsString`
- `openInterest`
- `underlyingExchangeSymbol`
- `underlyingSymbol`

This is a strong initial futures-specific field cluster.

## Market-state note

At the capture timestamp, the equity, ETF, closed-end-fund, mutual-fund, index, and
currency samples reported `CLOSED`. `BTC-USD` and `CL=F` reported `REGULAR`.

Study 02A records those values but does not interpret them as a market-hours rule.
The `CL=F` result should be revisited under the planned market-state transition study,
where clock time and trading-session state are the controlled variables.

## Interpretation limits

This is a one-symbol-per-project-category pilot. A field observed in only one subject
can reflect:

- the security type;
- that particular issuer or instrument;
- current corporate events;
- the capture time or market state;
- Yahoo backend variation; or
- a combination of those factors.

Promotion from “observed” to a stable security-type rule requires at least a second
representative or a repeat capture, following the project's observation lifecycle.

## Conclusion

Study 02A achieved its pilot objective:

1. all 12 reviewed special and ordinary symbol formats resolved correctly;
2. all seven expected Yahoo `quoteType` classes matched;
3. field occurrence varied materially by project security type;
4. closed-end-fund treatment was demonstrably different from ordinary common-equity
   treatment despite both returning Yahoo `quoteType=EQUITY`;
5. useful crypto-, futures-, and fund-oriented field clusters were identified; and
6. the existing analyzer can process the study without modification.

**Status: Study 02A live pilot complete and validated.**
