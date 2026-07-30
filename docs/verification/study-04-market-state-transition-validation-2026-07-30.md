# Study 04 Market-State Transition Validation — 2026-07-30

## Run identity

- Run ID: `2026-07-29T00-52-54.762Z_study-04-market-state-transition`
- Study: `study-04-market-state-transition` v`0.1.0`
- Tool version: `0.1.1`
- Study schema: `0.5.0`
- Session mode: `cookie-crumb`
- Subjects: `13`
- Planned rounds: `105`
- Sampling interval: `15 minutes`
- Planned requests: `1,365`
- Capture window: `2026-07-29T00:52:54.762Z` through `2026-07-30T02:52:55.949Z`

## Validation result

**PASS** — the live Study 04 archive is complete, internally consistent,
hash-verifiable, reconciled with the final command-window output, and suitable as the
permanent evidence set. A repeat capture is not warranted.

Validated outcomes:

- completed rounds: `105/105`
- evidence records: `1,365/1,365`
- HTTP 200 responses: `1,365/1,365`
- requested symbols returned: `1,365/1,365`
- expected `quoteType` matches: `1,365/1,365`
- market-state transitions: `50`
- retries: `0`
- authentication refreshes: `0`
- parse errors: `0`
- Yahoo quote-response errors: `0`
- missing evidence records: `0`

## Integrity checks

The following checks passed:

- ZIP CRC integrity;
- one raw response and one metadata sidecar for every planned request;
- exactly 13 subjects in each of 105 rounds;
- continuous request sequence from 1 through 1,365;
- exact manifest-to-sidecar equality;
- raw byte-count verification;
- raw-response SHA-256 verification;
- valid JSON for every raw response;
- canonical parsed-JSON SHA-256 verification;
- resolved-definition SHA-256 verification;
- complete comparison tables;
- requested-symbol and `quoteType` identity checks;
- crumb redaction and absence of persisted cookie or authorization values; and
- reconciliation of the final command-window totals with the manifest.

Capture ZIP SHA-256:

```text
fa20c05e4259a3acc61053653883f19567fffd2121f3d435bccdb02d3854bfe3
```

## Timing validation

- All 104 scheduled round gaps were exactly `900 seconds`.
- Each 13-symbol round completed in `1.412` to `2.971 seconds`; the median was
  `1.843 seconds`.
- Individual request latency ranged from `92` to `1,127 milliseconds`; the median was
  `127 milliseconds`.
- No latency event caused a retry or missed interval.
- The first request of a round occurred from `0.974 seconds early` to `0.485 seconds late`
  relative to the nominal scheduled timestamp. This is a minor scheduler characteristic,
  not a material defect at 15-minute sampling resolution.

## Market-state coverage

The capture observed all five targeted exact values:

- `PREPRE`
- `PRE`
- `REGULAR`
- `POST`
- `POSTPOST`

No `CLOSED` value appeared. The archive recorded 50 transitions across the 13-subject
panel.

| Symbol | Ordered observed sequence | Transitions | Pre-field rows | Post-field rows |
|---|---|---:|---:|---:|
| `AAPL` | `POSTPOST → PREPRE → PRE → REGULAR → POST → POSTPOST` | 5 | 22 | 57 |
| `RY.TO` | `POSTPOST → PREPRE → PRE → REGULAR → POST → POSTPOST` | 5 | 0 | 0 |
| `HSBA.L` | `PREPRE → PRE → REGULAR → POST → POSTPOST → PREPRE` | 5 | 0 | 0 |
| `INGA.AS` | `PREPRE → REGULAR → POSTPOST → PREPRE` | 3 | 0 | 0 |
| `SAP.DE` | `PREPRE → PRE → REGULAR → POST → POSTPOST → PREPRE` | 5 | 0 | 0 |
| `AIR.PA` | `PREPRE → REGULAR → POSTPOST → PREPRE` | 3 | 0 | 0 |
| `NESN.SW` | `PREPRE → REGULAR → POSTPOST → PREPRE` | 3 | 0 | 0 |
| `7203.T` | `REGULAR → POSTPOST → PREPRE → REGULAR` | 3 | 0 | 0 |
| `0700.HK` | `PREPRE → PRE → REGULAR → POSTPOST → PREPRE → PRE → REGULAR` | 6 | 0 | 0 |
| `BHP.AX` | `REGULAR → POSTPOST → PREPRE → PRE → REGULAR` | 4 | 0 | 0 |
| `VALE3.SA` | `POSTPOST → PREPRE → PRE → REGULAR → POST → POSTPOST` | 5 | 0 | 0 |
| `RELIANCE.NS` | `PREPRE → REGULAR → POSTPOST → PREPRE` | 3 | 0 | 0 |
| `BTC-USD` | `REGULAR` | 0 | 0 | 0 |

## Principal findings

1. **AAPL supplied the clean five-state reference cycle.** Its observed transitions
   bracketed the configured midnight, 04:00, 09:30, 16:00, and 20:00
   `America/New_York` boundaries within one 15-minute sample.
2. **`marketState` is venue- or feed-specific.** Some non-U.S. equities exposed all or
   most transition labels, while others moved directly from `PREPRE` to `REGULAR` or
   from `REGULAR` to `POSTPOST`.
3. **Extended-hours field presence is not a substitute for `marketState`.** Only `AAPL`
   returned the configured pre-market or post-market field groups. Every other subject
   returned zero such groups, including symbols that entered `PRE` or `POST`.
4. **Post-market fields can persist outside the active `POST` state.** For `AAPL`, the
   four post-market fields remained present through `POSTPOST` and the following
   `PREPRE` period, carrying the prior after-hours values. Applications must evaluate
   both field timestamps and `marketState`.
5. **`BTC-USD` remained `REGULAR` in all 105 observations**, providing the intended
   continuous-market control.
6. **No identity drift occurred.** Currency, time-zone label, and `quoteType` matched the
   configured subject definitions in every response.

## Published comparison files

The compact tables committed with this validation are:

- `data/study-04-market-state-transition-2026-07-29/market-state-summary.csv`
- `data/study-04-market-state-transition-2026-07-29/market-state-transitions.csv`
- `data/study-04-market-state-transition-2026-07-29/symbol-transition-summary.csv`

The raw responses, metadata sidecars, complete observation table, local validation files,
command-window transcript, and capture ZIP remain outside version control.

## Interpretation limits

This is one 26-hour weekday capture with one representative per reviewed international
market plus a cryptocurrency control. It establishes the observed transition sequences
for this run and confirms that the capture framework preserves them accurately. It does
not establish universal exchange schedules, holiday behavior, half-day behavior, or a
permanent Yahoo contract for any state token or extended-hours field.

## Conclusion

Study 04 achieved its controlled-transition objective:

1. all 1,365 planned requests completed successfully;
2. all five targeted exact `marketState` values were observed;
3. 50 transitions were preserved and reproduced in the comparison tables;
4. the relationship between state values and extended-hours fields was tested directly;
5. all structural, identity, hash, privacy, timing, and command-window reconciliation
   checks passed; and
6. the continuous-market control behaved as expected.

**Status: Study 04 live transition study complete and validated.**
