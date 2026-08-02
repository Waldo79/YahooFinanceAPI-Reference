# Study 07 — Day Percent-Change Validity Protocol v0.5.0-draft

## Purpose

Study 07 investigates Yahoo Finance Day percent-change observations near zero and determines what causes exceptions to the expected relationship between the prior posted close and the current posted price or close.

The agreed near-zero band is inclusive:

```text
-0.001% <= regularMarketChangePercent <= +0.001%
```

For observations inside this band, the central test is whether:

```text
regularMarketPrice == regularMarketPreviousClose
```

Equality is evaluated using the configured absolute price tolerance. An in-band observation with unequal prices is retained as a near-zero exception for explanation rather than silently treated as an exact zero.

## Research questions

1. When Yahoo reports Day percent change inside the inclusive +/-0.001% band, are the current posted regular-market price and prior posted close equal?
2. What conditions explain in-band observations whose two posted prices are unequal?
3. Does `regularMarketChangePercent` reconcile arithmetically with `regularMarketPrice` and `regularMarketPreviousClose`?
4. Does `regularMarketChange` reconcile with the same two price fields?
5. Does `regularMarketPreviousClose` agree with the immediately preceding non-null daily Chart close?
6. Does `regularMarketTime` belong to the capture date in the instrument's reported exchange offset?
7. How do the results differ across the twelve validated project security types?
8. Are pre-market and post-market values present independently of the regular-market Day percent-change calculation?

## Subjects

The study reuses the 24 validated Study 02B representatives: two subjects for each of twelve project security types.

- Common Stock
- REIT
- MLP / Special Equity
- Special Share Class
- Broad-Market ETF
- Bond ETF
- Closed-End Fund
- Mutual Fund
- Market Index
- Currency Pair
- Cryptocurrency
- Futures Contract

## Requests

Each invocation creates an independent timestamped run and sends 48 sequential requests:

- one Quote request for each of 24 subjects;
- one five-day, one-day-interval Chart request for each subject.

The Quote response supplies the posted Day percent-change fields. The Chart response independently identifies the immediately preceding observed daily close without assuming that the previous calendar day was an open session.

## Central near-zero test

The tool first determines whether Yahoo's reported `regularMarketChangePercent` is inside the inclusive band from -0.001% through +0.001%.

For every in-band observation it then compares:

```text
current_posted_value = regularMarketPrice
prior_posted_close   = regularMarketPreviousClose
```

The outcome is one of two central classifications:

- `NEAR_ZERO_PRICE_EQUAL` — the two posted prices are equal within the configured price tolerance.
- `NEAR_ZERO_PRICE_UNEQUAL_EXCEPTION` — Yahoo reports an in-band Day percent change, but the two posted prices are unequal.

The second classification is an exception to be explained. It is not automatically declared a Yahoo error because a very small nonzero move can legitimately fall inside the band.

## Diagnostic gates

The following gates are applied before the central near-zero classification.

### Current-session gate

`regularMarketTime`, converted using Yahoo's reported exchange offset, must have the same local calendar date as the capture. A prior-session value observed before a market opens, on a weekend, or on a holiday is classified as not current for this application rule.

### Numeric-input gate

The following Quote fields must be numeric and usable:

- `regularMarketPrice`
- `regularMarketPreviousClose`
- `regularMarketChange`
- `regularMarketChangePercent`
- `regularMarketTime`

The previous close must be greater than zero.

### Previous-session Chart gate

The tool locates the daily Chart bar whose exchange-local date matches `regularMarketTime`. The immediately preceding non-null daily close is the independent reference previous close. Yahoo's Quote previous close must agree with that reference within the configured tolerance.

### Arithmetic gates

The tool calculates:

```text
calculated_change  = regularMarketPrice - regularMarketPreviousClose
calculated_percent = calculated_change / regularMarketPreviousClose * 100
```

The calculated values must agree with Yahoo's reported regular-market change and percent fields within configured reconciliation tolerances.

### Volume gate

Positive regular-market volume is required only when both conditions are true:

- `marketState` is `REGULAR`;
- the project security type is configured as an exchange-traded type for which volume is expected.

Volume remains informational for the other instrument types.

### Extended-hours separation

Pre-market and post-market fields are recorded separately. They do not replace `regularMarketPrice` in the central regular-market comparison.

## Classifications

The first applicable classification is used:

1. `INSUFFICIENT_DATA`
2. `NOT_CURRENT_SESSION`
3. `CHART_REFERENCE_UNAVAILABLE`
4. `PREVIOUS_CLOSE_MISMATCH`
5. `REPORTED_CHANGE_MISMATCH`
6. `REPORTED_PERCENT_MISMATCH`
7. `VOLUME_NOT_CONFIRMED`
8. `OUTSIDE_NEAR_ZERO_BAND`
9. `NEAR_ZERO_PRICE_EQUAL`
10. `NEAR_ZERO_PRICE_UNEQUAL_EXCEPTION`

For unequal-price exceptions, the tool records an initial cause category:

- `SMALL_NONZERO_PRICE_MOVE` when the independently calculated percent is also inside the band;
- `REPORTED_PERCENT_DOES_NOT_RECONCILE` when the reported percent fails arithmetic reconciliation;
- `UNRESOLVED` when the available evidence does not support either explanation.

## Display recommendations

- `PERCENT` — arithmetic and session evidence are valid, and the observation is either outside the near-zero band or an in-band equal-price observation.
- `REVIEW` — an in-band unequal-price exception requires interpretation.
- `N/A` — a required validity gate failed.

The study records recommendations; it does not hard-code an application policy before the observed exception patterns are reviewed.

## Outputs

Each run writes:

```text
captures/local/<timestamp>_study-07-day-change-validity[_label]/
  run-manifest.json
  study-definition.resolved.json
  raw/quote/*.raw.json
  raw/chart/*.raw.json
  metadata/quote/*.meta.json
  metadata/chart/*.meta.json
  errors/quote/*.error.txt
  errors/chart/*.error.txt
  comparison/day-change-validity.csv
  comparison/day-change-summary.csv
```

The manifest request entries exactly equal their metadata sidecars, allowing the existing endpoint analyzer to process the mixed Quote and Chart evidence.

## Recommended observations

The tool has no fixed schedule. Useful independent labeled runs include:

- weekend or holiday closed-market baseline;
- before the regular session opens;
- during the regular session;
- after the regular session closes;
- after daily mutual-fund NAV values have updated.

These runs are independent of Study 06 and do not alter its capture series.

## Commands

From repository root:

```text
py tools\day-change-study\run_day_change_validity_study.py --dry-run
py tools\day-change-study\run_day_change_validity_study.py --label weekend-baseline
```

Analyze a completed run with the existing analyzer:

```text
py tools\endpoint-analysis\analyze_endpoint_captures.py "captures\local\<run-folder>"
```
