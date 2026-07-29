# Study 04 Market-State Transition Protocol — v0.5.0 Draft

## Status

Implementation-ready interval protocol for observing exact Yahoo Finance Quote
`marketState` strings and extended-hours field behavior through a rolling international
market cycle.

The default study performs:

```text
13 subjects × 105 rounds × 1 Quote request pattern = 1,365 requests
```

The rounds are spaced 15 minutes apart over 26 hours. The study preserves every exact
returned state string, including duplicated tokens such as `PREPRE` and `POSTPOST`.

## Research question

When the endpoint, request parameters, session mode, subject panel, and capture cadence
are held constant, how does Yahoo Finance Quote `marketState` change through local market
session boundaries, and when do pre-market and post-market value fields appear or
disappear?

## Motivation

Study 03 recorded the exact values:

- `PRE`
- `PREPRE`
- `POST`
- `POSTPOST`

Study 02B also recorded `REGULAR` for continuous-market subjects. Study 04 converts those
cross-sectional observations into a longitudinal transition study.

The duplicated tokens are treated as valid raw observations. The tool does not normalize
`PREPRE` to `PRE` or `POSTPOST` to `POST`.

## Subject panel

Study 04 repeats the twelve Study 03 international ordinary-equity subjects:

- `AAPL`
- `RY.TO`
- `HSBA.L`
- `INGA.AS`
- `SAP.DE`
- `AIR.PA`
- `NESN.SW`
- `7203.T`
- `0700.HK`
- `BHP.AX`
- `VALE3.SA`
- `RELIANCE.NS`

It adds `BTC-USD` as a twenty-four-hour continuous-market control.

This produces a 13-subject panel across the reviewed time zones and Yahoo symbol formats.

## Sampling design

Default sampling parameters:

```text
duration: 26 hours
interval: 15 minutes
rounds: 105, including the initial round
subjects per round: 13
planned evidence records: 1,365
```

A 26-hour rolling window was chosen so a run started on a normal weekday can span one
complete daily cycle for all reviewed local time zones without requiring the tool to
predict Yahoo's state changes from exchange schedules.

The 15-minute baseline resolves transitions to a maximum sampling interval of roughly
15 minutes. A later boundary-refinement run may use `--interval-minutes 5` or another
shorter interval after the transition sequence is known.

## Reference market hours

These official exchange hours are interpretation references only. They are not used to
rewrite, infer, or validate Yahoo's returned `marketState`.

### Nasdaq anchor — `AAPL`

Nasdaq publishes regular trading from 09:30 to 16:00 Eastern Time, pre-market trading
from 04:00 to 09:30, and after-hours trading from 16:00 to 20:00.

Reference:

- <https://www.nasdaq.com/market-activity/stock-market-holiday-schedule>

### Toronto anchor — `RY.TO`

TMX publishes TSX pre-open from 07:00 to 09:30 Eastern Time and continuous trading from
09:30 to 16:00. TMX also documents an extended special trading session from 16:15 to
17:00 for qualifying cross facilities.

References:

- <https://www.tsx.com/en/trading/calendars-and-trading-hours/trading-hours>
- <https://www.tsx.com/en/trading/toronto-stock-exchange/order-types-and-features/cross-facilities>

### London anchor — `HSBA.L`

London Stock Exchange publishes trading hours from 08:00 to 16:30 London time.

Reference:

- <https://www.londonstockexchange.com/personal-investing/faqs>

### Australia anchor — `BHP.AX`

ASX publishes equity pre-open from 07:00 to 10:00 Sydney time and the normal open session
from 10:00 to 16:00, followed by closing phases.

Reference:

- <https://www.asx.com.au/markets/market-resources/trading-hours-calendar/market-microstructure>

## Controlled request pattern

Every request uses:

```text
GET https://query1.finance.yahoo.com/v7/finance/quote
symbols=<one configured symbol>
formatted=false
lang=en-US
region=US
session mode=cookie-crumb
```

The project default is zero added delay between subjects inside one round. Rounds are
scheduled from a monotonic clock to reduce accumulated drift.

## Evidence layout

A completed run contains:

```text
study-definition.resolved.json
run-manifest.json
raw/
metadata/
errors/
comparison/
```

Each request produces one unchanged raw response and one metadata sidecar. File names
contain the global sequence, round index, symbol, and endpoint.

The comparison directory contains:

- `market-state-observations.csv`
- `market-state-summary.csv`
- `market-state-transitions.csv`
- `symbol-transition-summary.csv`

## Transition logic

For each symbol, observations are ordered by round and request sequence.

A transition is recorded only when the exact current `marketState` differs from the exact
prior value. For example:

```text
PREPRE → PRE → REGULAR → POST → POSTPOST
```

is five distinct states and four transitions. No duplicated-token normalization occurs.

## Extended-hours fields

The tool separately records the presence of:

### Pre-market group

- `preMarketChange`
- `preMarketChangePercent`
- `preMarketPrice`
- `preMarketTime`

### Post-market group

- `postMarketChange`
- `postMarketChangePercent`
- `postMarketPrice`
- `postMarketTime`

Field presence is compared with the exact state string but is not assumed to be determined
by that string. Study 03 already showed that `POSTPOST` did not necessarily include the
four post-market value fields.

## Long-run behavior and interruption handling

The default live run lasts approximately 26 hours. The command window must remain open and
the computer must remain awake.

After every completed round, the tool rewrites the comparison tables and a checkpoint
manifest. Pressing Ctrl+C triggers graceful partial finalization. The retained archive
records `run_status=interrupted` and remains usable, but it is not a complete default run.

The tool currently starts a new run rather than resuming an interrupted run. A future
hardening step may add resumable scheduling after the baseline transition behavior is
validated.

## Commands

Dry run:

```text
py tools\market-state-study\run_market_state_transition_study.py --dry-run
```

Full default run:

```text
py tools\market-state-study\run_market_state_transition_study.py
```

Two-round live pilot:

```text
py tools\market-state-study\run_market_state_transition_study.py --rounds 2
```

Five-minute refinement over four hours:

```text
py tools\market-state-study\run_market_state_transition_study.py --duration-hours 4 --interval-minutes 5
```

Analyze a completed or interrupted archive:

```text
py tools\endpoint-analysis\analyze_endpoint_captures.py "captures\local\<study-04-run-folder>"
```

## Acceptance criteria

A full default run passes when:

1. 105 complete rounds are written;
2. 1,365 metadata sidecars and raw responses are written;
3. the expected symbol is returned for every successful request;
4. expected `quoteType` values match for every returned subject;
5. exact state strings are preserved without normalization;
6. transition and state summaries recompute from the metadata sidecars;
7. raw byte counts and SHA-256 hashes verify;
8. canonical parsed-JSON hashes verify;
9. request fingerprints verify;
10. the resolved study-definition hash verifies;
11. no cookie, crumb, authorization, or session secret is persisted; and
12. the existing endpoint analyzer processes all evidence without modification.

## Interpretation limits

The study observes Yahoo's Quote response at 15-minute intervals. It does not prove the
precise instant of a state transition, because the transition occurs somewhere between
the last observation of the prior value and the first observation of the new value.

State behavior may vary by exchange, symbol, issuer, holiday calendar, trading halt,
corporate event, Yahoo backend, and capture date. Official exchange hours are contextual
references rather than a guarantee that Yahoo changes state at those exact times.

A single 26-hour run establishes one longitudinal baseline. Stable rules require a repeat
run on another ordinary trading day.
