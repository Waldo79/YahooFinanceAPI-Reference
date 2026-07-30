# Study 06 — Chart Repeated-Day Stability Protocol (v0.5.0-draft)

## Purpose

Study 06 measures how the nine validated Study 05 Chart request variants behave when
repeated at the same late-session market time on three separate U.S. trading days.

The study is designed to distinguish:

- stable request and response structure;
- expected rolling-window movement;
- changing current-session values;
- variation in incomplete intraday bars;
- day-to-day event availability; and
- unexpected endpoint or schema changes.

Study 06 reuses the Study 05 subject, variant order, session mode, raw-evidence rules,
hashing rules, and controlled parameter definitions.

## Subject and endpoint

- Subject: `AAPL`
- Endpoint: Yahoo Finance Chart
- Session mode: anonymous cookie and crumb
- Variants per round: `9`
- Scheduled rounds: `3`
- Planned requests: `27`

## Schedule

The configured Eastern offset is fixed at `-04:00` because all three planned dates occur
during U.S. daylight-saving time.

| Round | Date | Eastern | Pacific | UTC |
|---|---|---:|---:|---:|
| day-01 | Friday, July 31, 2026 | 3:45 p.m. | 12:45 p.m. | 19:45 |
| day-02 | Monday, August 3, 2026 | 3:45 p.m. | 12:45 p.m. | 19:45 |
| day-03 | Tuesday, August 4, 2026 | 3:45 p.m. | 12:45 p.m. | 19:45 |

The dates intentionally span a weekend without requiring the computer to remain running.

## Invocation model

Study 06 captures exactly one scheduled round per normal invocation.

On each scheduled date:

1. start the command before 12:45 p.m. Pacific;
2. the tool waits only until that date's 12:45 p.m. Pacific target;
3. it captures the nine Study 05 variants;
4. it saves the completed round and updates the cumulative comparison tables; and
5. it exits.

The tool never waits across calendar days. Starting it on an earlier date produces an
instructional error instead of keeping the computer running overnight or over a weekend.

If started after 12:45 p.m. Pacific on the correct date, the tool begins immediately and
records the same-day lateness in the round manifest.

`--run-now` is an explicit recovery/testing override. It should not be used for the
normal scheduled workflow.

## Commands

Run from the repository root.

Validate the definition and display the schedule without contacting Yahoo:

```text
py tools\chart-stability-study\run_chart_repeated_day_stability_study.py --dry-run
```

On each scheduled date, start the normal invocation before the target time:

```text
py tools\chart-stability-study\run_chart_repeated_day_stability_study.py
```

The same command is used on all three dates. The existing series manifest determines the
next incomplete round.

## Source variants

The tool reads the committed Study 05 definition:

```text
config/studies/study-05-chart-parameter-variation.json
```

The repeated variants are:

1. `baseline-5d-1d`
2. `events-omitted`
3. `events-div-only`
4. `range-1mo-1d`
5. `interval-1h-5d`
6. `interval-5m-5d`
7. `include-prepost-true-5m`
8. `interval-1m-5d`
9. `explicit-period-5d-1d`

The explicit-period request is recalculated from each round's actual start time. Its
request-parameter fingerprint is therefore expected to differ across days.

## Evidence layout

The persistent series folder is:

```text
captures/local/2026-07-31_to_2026-08-04_study-06-chart-repeated-day-stability/
```

It contains:

```text
series-manifest.json
study-definition.resolved.json
comparison/
rounds/
  day-01_2026-07-31/
  day-02_2026-08-03/
  day-03_2026-08-04/
```

Each completed round contains its own:

- `run-manifest.json`;
- unchanged raw Chart responses;
- metadata sidecars;
- error files when applicable;
- Study 05 parameter-results table; and
- Study 05 controlled-comparison table.

Because every round has a normal `run-manifest.json`, it can be analyzed independently
with the existing endpoint analyzer.

## Cross-day tables

The series folder maintains two cumulative tables after every completed round.

### `comparison/chart-day-results.csv`

One row per round and Study 05 variant. It preserves the round identity, scheduled and
actual timing, request parameters, response classifications, Chart metrics, and evidence
hashes.

Expected row counts:

- after day-01: `9`
- after day-02: `18`
- after day-03: `27`

### `comparison/chart-day-stability.csv`

Compares each variant in every completed round with the same variant in `day-01`.

The table reports:

- request-parameter fingerprint equality;
- timestamp-sequence equality and overlap;
- timestamps unique to either round;
- metadata-key and indicator-key equality;
- event-identity equality;
- canonical JSON and raw-response equality; and
- response-size, bar-count, null-count, and event-count deltas.

The day-01 identity rows are retained as explicit controls.

## Required controls

The following remain fixed:

- subject;
- endpoint;
- session mode;
- nine-variant definition and order;
- target Eastern local time;
- one sequential request per variant;
- no normal inter-request pause;
- retry and timeout defaults;
- unchanged raw response bytes;
- canonical parsed-JSON hashes;
- request-parameter fingerprints;
- redacted crumb in stored URLs; and
- no persisted cookie, crumb, authorization, or other sensitive values.

## Expected variation

The study does not expect complete raw-response equality across days.

Expected changes include:

- rolling `5d` and `1mo` timestamp windows;
- current-session OHLCV values;
- live metadata such as current price and trading periods;
- incomplete intraday bars;
- response sizes;
- explicit `period1` and `period2` values; and
- event content when a requested window begins to include an event.

More stable candidates include:

- top-level response structure;
- metadata key sets;
- indicator key sets by interval;
- endpoint identity fields;
- returned exchange, instrument type, currency, and time zone; and
- request fingerprints for non-dynamic variants.

## Missed-round handling

A normal invocation after the scheduled date refuses to silently relabel the late capture
as an on-time round.

If a round is missed, preserve the existing series folder and review the recovery choice
before using `--run-now`. Any recovery capture records the override and the schedule
offset so that it cannot be mistaken for the planned observation.

## Validation plan

After day-03:

1. confirm `3/3` completed rounds and `27/27` evidence records;
2. confirm HTTP, JSON, expected Chart-object, and error summaries;
3. analyze each round with the existing endpoint analyzer;
4. validate raw byte counts and SHA-256 hashes;
5. validate manifest-to-sidecar equality;
6. validate cumulative comparison row counts and control identities;
7. classify expected rolling-window changes separately from schema changes;
8. inspect all type conflicts, especially intraday `null;number` observations; and
9. preserve the complete series locally while publishing only compact findings and
   comparison tables.

## Interpretation limits

Study 06 is a three-day AAPL observation at one late-session time. It does not establish
permanent behavior for all symbols, exchanges, market phases, date ranges, corporate
events, or future Yahoo backend versions.

## Completion criterion

Study 06 is complete when all three scheduled rounds are captured, the cumulative
27-request evidence set passes integrity and privacy validation, each round is analyzed,
and the cross-day findings are documented.
