# Study 06 Chart Repeated-Day Stability Validation - 2026-08-04

## Series identity

- Series ID: `2026-07-31_to_2026-08-04_study-06-chart-repeated-day-stability`
- Study: `study-06-chart-repeated-day-stability` v`0.1.0`
- Analyzer version: `0.1.1`
- Subject: `AAPL`
- Endpoint: Yahoo Finance Chart
- Session mode: `cookie-crumb`
- Scheduled rounds: `3`
- Planned requests: `27`
- Series capture window: `2026-07-31T19:47:11.284Z` through `2026-08-04T19:47:15.280Z`

## Validation result

**PASS** - the Study 06 three-round evidence series is complete, internally
consistent, hash-verifiable, successfully analyzed, and suitable as the permanent
local evidence set.

Validated outcomes:

- completed rounds: `3/3`
- evidence records: `27/27`
- HTTP 200 responses: `27/27`
- valid JSON responses: `27/27`
- expected Chart objects: `27/27`
- Chart result objects: `27/27`
- Chart errors: `0`
- targeted evidence-integrity checks passed: `324/324`
- manifest-to-sidecar exact matches: `27/27`
- analyzer warnings: `0`

All three rounds were recorded as `same-day-late`, starting `131.284`, `132.137`,
and `133.049` seconds after the configured target. The less-than-two-second spread
between those offsets preserved a closely aligned late-session comparison. The
lateness is disclosed as schedule metadata and is not classified as a capture failure.

## Integrity checks

The following checks passed:

- completed series status and three completed round manifests;
- continuous nine-request evidence in each round;
- one raw response and one metadata sidecar per request;
- exact manifest-to-sidecar equality for all 27 requests;
- raw byte-count and SHA-256 verification;
- canonical parsed-JSON SHA-256 consistency;
- request-parameter fingerprint consistency;
- cumulative comparison row counts of 27 and retained day-01 controls;
- successful independent analyzer execution for all three rounds;
- analyzer output row counts and no analyzer warnings;
- redacted stored request URLs; and
- no persisted cookie, crumb, authorization, bearer, or other sensitive values.

## Analyzer results

| Round | Samples | Flattened field rows | Catalog paths | Occurrence rows | Type conflicts |
|---|---:|---:|---:|---:|---:|
| day-01 | 9 | 20,502 | 62 | 558 | 5 |
| day-02 | 9 | 20,481 | 62 | 558 | 5 |
| day-03 | 9 | 20,488 | 62 | 558 | 5 |

All three rounds independently passed analyzer validation with no warnings. The catalog-path
count, occurrence-row count, and type-conflict count were identical across days; only the
flattened-value row count varied slightly with changing market data.

The five type-conflict rows were identical after removing day-specific sample identifiers:
`open`, `high`, `low`, `close`, and `volume` each showed `null;number`, with the null
observations confined to the `interval-1m-5d` variant. This repeated pattern is consistent
with an incomplete current one-minute bar rather than incompatible schema changes.

The analyzer's `source_run_completed_at_utc` compatibility field was null for all three
rounds. Each round manifest independently retained its actual start and completion
timestamps, so this analyzer metadata omission does not affect evidence integrity.

## Cross-day stability findings

1. **Response structure remained stable.** Metadata key sets, indicator key sets, and event
   identity sets were equal in all `27/27` control comparisons.

2. **Request fingerprints behaved as designed.** Request parameters matched the day-01
   control in `25/27` rows. The only differences were the day-02 and day-03
   `explicit-period-5d-1d` requests, whose `period1` and `period2` values are intentionally
   recalculated for each round.

3. **Changing response bytes were expected.** All 18 later-day comparisons had different
   timestamp sequences, canonical JSON hashes, and raw-response hashes from day-01 while
   preserving the structural invariants above.

4. **Five-day daily windows rolled predictably.** The baseline, events-omitted, and
   events-div-only variants retained five bars. Day-02 replaced one day-01 timestamp and
   day-03 replaced two.

5. **Intraday windows rolled while retaining their bar counts.** The 1-hour, 5-minute,
   pre/post 5-minute, and 1-minute variants showed old timestamps leaving and new timestamps
   entering the five-day window, with no bar-count change relative to day-01.

6. **The explicit five-calendar-day period remained distinct from `range=5d`.** On both
   later rounds it contained one fewer daily bar than day-01 as the moving calendar-time
   boundaries shifted across trading sessions.

7. **The one-month daily window varied only at its moving boundary.** Relative to day-01,
   day-02 contained two fewer bars and day-03 one fewer bar while retaining 20 shared
   timestamps in each comparison.

8. **Returned endpoint identity remained stable.** Across every variant and all three
   rounds, Yahoo returned `NMS`, `EQUITY`, `USD`, and `America/New_York`, with no changes
   in each variant's returned range or data granularity.

9. **No event-count or null-count instability appeared across days.** Every later-day
   comparison had zero event-count delta and zero null-indicator-count delta.

These findings support repeated-day structural stability for the tested conditions while
also demonstrating the expected movement of rolling time-series content. They do not imply
raw-response equality.

## Published comparison files

The compact tables committed with this validation are:

- `data/study-06-chart-repeated-day-stability-2026-08-04/chart-day-results.csv`
- `data/study-06-chart-repeated-day-stability-2026-08-04/chart-day-stability.csv`

The complete series manifests, round manifests, unchanged raw responses, metadata sidecars,
analyzer detail tables, and local validation material remain under the local capture series
and outside version control.

## Interpretation limits

Study 06 is a three-day AAPL observation at one closely aligned late-session time. It
establishes repeatability for these nine tested Chart request patterns across the observed
dates. It does not establish permanent behavior for all symbols, exchanges, market phases,
date ranges, corporate-event windows, Yahoo infrastructure regions, or future Yahoo backend
versions.

The synchronization controls time-of-day as one experimental variable; it does not remove
all market, data-window, backend, or content variables. Accordingly, `stable` here means
that expected structural and identity characteristics remained consistent while expected
time-dependent content changed coherently.

## Conclusion

Study 06 achieved its repeated-day stability objective:

1. all three planned rounds and all 27 requests completed successfully;
2. the 27-request evidence set passed targeted integrity, hash, sidecar, and privacy checks;
3. every round passed independent analyzer validation;
4. metadata keys, indicator keys, event identities, and returned endpoint identity remained
   stable across the three days;
5. rolling daily and intraday timestamp changes were coherent with the requested windows;
6. the only request-fingerprint changes were the intentionally dynamic explicit-period
   requests; and
7. the repeated `null;number` OHLCV conflicts were confined to the one-minute variant and
   classified as incomplete-bar behavior rather than schema instability.

**Status: Study 06 Chart repeated-day stability study complete and validated.**
