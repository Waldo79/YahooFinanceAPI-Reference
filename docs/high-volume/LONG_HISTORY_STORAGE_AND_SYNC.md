# Long-History Storage and Synchronization Design

Version: `0.1.0-candidate.2`

## Objective

Download complete history once, then normally request only new data plus a
short overlap that can reveal recent revisions. Preserve every raw response and
maintain a queryable normalized archive without repeatedly downloading all
symbols' complete histories.

## Authoritative layers

1. Compressed raw Chart JSON (`.json.gz`) preserves Yahoo's complete response.
2. SQLite stores normalized bars, events, current symbol state, and revisions.
3. Future XLSX exports will be derived analytical views, not the sole evidence.

## Baseline

Baseline mode requests Yahoo Chart with explicit `period1`/`period2` bounds beginning at 1900-01-01 for every unique selected symbol and interval. Returned `meta.dataGranularity` must equal the requested interval before any bars are inserted. Duplicate input rows are deduplicated before network planning.

## Incremental Sync

Sync mode reads the latest stored timestamp for each symbol and interval. It
requests from `latest - overlap_days` through the requested end date. The
default overlap is 30 calendar days.

A symbol with no stored bar automatically receives a complete request so a
mixed old/new symbol list can be synchronized safely.

## Change classification

Bars are keyed by:

```text
symbol + interval + timestamp_utc
```

For returned records:

- absent key: new bar;
- existing key and equal values: unchanged bar;
- existing key and changed OHLC/adjusted close/volume: revised bar;
- existing stored timestamp absent from a returned comparison range:
  `MISSING_FROM_REFRESH` review record; the stored bar is retained.

Revision rows preserve old and new values as canonical JSON.

## Corporate actions and deep refresh

Dividends, splits, and capital gains are stored independently from price bars.
A new or revised event during Sync flags that symbol and interval for a complete
refresh. An adjusted-close revision during Sync does the same.

`refresh-flagged` requests only flagged symbols with the same explicit 1900-01-01 through-date bounds. It clears a
flag after a successful full comparison unless a missing historical bar is
still observed.

## Database durability

SQLite uses WAL mode, foreign-key enforcement, and one transaction per symbol.
A checkpoint entry is appended only after that symbol's raw file, database
changes, and result record have been written. Resume therefore skips completed
symbols without repeating committed work.

## Memory control

Network work is concurrent, but the number of pending futures is bounded to
approximately twice the worker count. SQLite writes occur serially as responses
complete, preventing thousands of complete history responses from accumulating
in memory.

## Current scope

Supported intervals:

- `1d`
- `1wk`
- `1mo`

Excluded from this candidate:

- intraday intervals;
- automatic deletion of missing bars;
- automatic XLSX export;
- remote/cloud storage;
- online GitHub capture output.
