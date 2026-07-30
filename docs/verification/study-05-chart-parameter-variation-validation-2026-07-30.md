# Study 05 Chart Parameter Variation Validation — 2026-07-30

## Run identity

- Run ID: `2026-07-30T19-45-22.392Z_study-05-chart-parameter-variation`
- Study: `study-05-chart-parameter-variation` v`0.1.0`
- Tool version: `0.1.0`
- Analyzer version: `0.1.1`
- Subject: `AAPL`
- Session mode: `cookie-crumb`
- Planned requests: `9`
- Capture window: `2026-07-30T19:45:22.392Z` through `2026-07-30T19:45:24.324Z`
- Duration: `1.932 seconds`

## Validation result

**PASS** — the Study 05 archive is complete, internally consistent, hash-verifiable,
successfully analyzed, and suitable as the permanent evidence set.

Validated outcomes:

- evidence records: `9/9`
- HTTP 200 responses: `9/9`
- valid JSON responses: `9/9`
- expected Chart objects: `9/9`
- Chart result objects: `9/9`
- retries: `0`
- authentication refreshes: `0`
- Chart errors: `0`
- independent checks passed: `166/166`

Source ZIP SHA-256:

```text
2cc03fc8d1e7d8579f28895c120faf84785a33d1fd469141864c448afa29c184
```

## Integrity checks

The following checks passed:

- ZIP CRC integrity;
- completed run status and continuous sequence 1 through 9;
- unique sample and variant identifiers;
- one raw response and one metadata sidecar per request;
- exact manifest-to-sidecar equality;
- raw byte-count and SHA-256 verification;
- canonical parsed-JSON SHA-256 verification;
- request-parameter fingerprint verification;
- resolved study-definition SHA-256 verification;
- no unreferenced raw or metadata files;
- empty errors directory;
- analyzer manifest hash, output hashes, and row counts;
- no analyzer warnings;
- no absolute owner-computer paths; and
- no persisted cookie, authorization, bearer, or unredacted crumb values.

## Analyzer results

- samples: `9`
- flattened field rows: `20,476`
- catalog paths: `62`
- occurrence rows: `558`
- matrix rows: `62`
- type conflicts: `5`

The five type-conflict rows are `null;number` observations for `open`, `high`, `low`,
`close`, and `volume`. They arise from the current incomplete intraday bar containing
null OHLCV values while completed bars contain numbers. They are not incompatible schema
changes.

## Principal findings

1. **Range controls the number of trading sessions returned.** The `1mo`/`1d` request
   returned 22 daily timestamps. All five `5d` baseline timestamps were present, plus
   17 earlier timestamps.

2. **Interval changes both density and response schema.** The `1h`, `5m`, and `1m`
   variants returned 36, 389, and 1,937 bars. Intraday responses omitted the daily
   `adjclose` group and added the metadata keys `previousClose`, `scale`, and
   `tradingPeriods`.

3. **`includePrePost=true` substantially expands the intraday series.** The 5-minute
   pre/post request returned 911 timestamps versus 389 for regular-only, a net increase
   of 522. The comparison contained 388 shared timestamps, 523 timestamps only in the
   pre/post response, and one rolling live-edge timestamp only in the regular-only
   response.

4. **`range=5d` is not equivalent to an exact rolling five-calendar-day period.** The
   baseline returned five daily bars, beginning July 24, 2026. The explicit period from
   July 25, 2026 19:45 UTC through July 30, 2026 19:45 UTC returned four daily bars,
   beginning July 27.

5. **The event variations produced no event evidence.** Every response contained zero
   event objects. The `events`-omitted and `events=div` responses were canonically equal.
   Their small difference from the baseline was confined to the live final daily bar and
   current-market metadata changing between sequential requests, not to event content.

6. **Incomplete intraday bars may contain null OHLCV values.** The 5-minute regular,
   5-minute pre/post, and 1-minute responses each contained five null indicator values at
   the current 19:45 UTC bar.

## Published comparison files

The compact tables committed with this validation are:

- `data/study-05-chart-parameter-variation-2026-07-30/chart-parameter-results.csv`
- `data/study-05-chart-parameter-variation-2026-07-30/chart-controlled-comparison.csv`

The raw responses, metadata sidecars, analyzer detail tables, local validation files,
command-window transcript, and capture ZIP remain outside version control.

## Interpretation limits

This is one AAPL capture performed during an active trading session. It establishes the
observed behavior of these nine Chart request patterns at that time. It does not establish
permanent endpoint rules for all symbols, markets, dates, corporate-event windows, or
Yahoo backend versions. Event-parameter behavior requires a subject and date range that
actually contains dividends, splits, or capital-gain events.

## Conclusion

Study 05 achieved its controlled parameter-variation objective:

1. all nine planned requests completed successfully;
2. all evidence, metadata, hashes, fingerprints, and analyzer outputs validated;
3. range, interval, pre/post inclusion, and explicit-period behavior produced measurable
   controlled differences;
4. current incomplete intraday-bar nulls were preserved and correctly classified; and
5. the event parameter remains a targeted follow-up because this capture contained no
   event objects.

**Status: Study 05 live Chart parameter-variation study complete and validated.**
