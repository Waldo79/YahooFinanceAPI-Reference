# Study 01 Session-Mode Validation — 2026-07-19

- Run ID: `2026-07-19T02-47-49.345Z_study-01-session-modes`
- Study: `study-01-session-mode-requirements` v`0.1.0`
- ZIP integrity: `PASS`
- Run duration: `5.899 seconds`
- Planned/evidence records: `21/21`
- HTTP responses: `21`
- Expected top-level objects found: `15`
- Comparison rows: `21` mode rows and `7` endpoint rows

## Evidence integrity

PASS — all 21 manifest request entries exactly match their metadata sidecars; all raw byte counts and SHA-256 hashes match; and every raw body parses as JSON.

Sensitive-session scan: `PASS`. No cookie, authorization, or unredacted crumb value was found.

## Live results

| Endpoint | Cookie + crumb | Cookie only | No session | Pilot interpretation |
|---|---|---|---|---|
| `quote` | HTTP 200; expected object | HTTP 401; expected object absent | HTTP 401; expected object absent | **Crumb-gated in this pilot** |
| `chart` | HTTP 200; expected object | HTTP 200; expected object | HTTP 200; expected object | **No prepared session required in this pilot** |
| `quote-summary` | HTTP 200; expected object | HTTP 401; expected object absent | HTTP 401; expected object absent | **Crumb-gated in this pilot** |
| `search` | HTTP 200; expected object | HTTP 200; expected object | HTTP 200; expected object | **No prepared session required for success in this pilot** |
| `screener` | HTTP 200; expected object | HTTP 200; expected object | HTTP 200; expected object | **No prepared session required in this pilot** |
| `fundamentals-timeseries` | HTTP 200; expected object | HTTP 200; expected object | HTTP 200; expected object | **No prepared session required in this pilot** |
| `options` | HTTP 200; expected object | HTTP 401; expected object absent | HTTP 401; expected object absent | **Crumb-gated in this pilot** |

## Endpoint findings

- **quote:** Only cookie-plus-crumb succeeded. Cookie-only still failed after one session refresh; no-session also failed.
- **chart:** All modes succeeded and parsed to identical JSON values. Raw SHA-256 differed only because Yahoo emitted object keys in different orders.
- **quote-summary:** Only cookie-plus-crumb succeeded. Both other modes returned 401 with `Invalid Crumb`.
- **search:** All modes succeeded. Cookie-only added `sectorDisp`/`industryDisp` to three quote records; repeat before attributing this permanently to session cookies.
- **screener:** All modes succeeded and parsed to identical JSON values. Raw hashes differed because object-key order differed.
- **fundamentals-timeseries:** All three responses were byte-for-byte identical.
- **options:** Only cookie-plus-crumb succeeded. Both other modes returned 401 with `Invalid Crumb`.

The three cookie-only failures (`quote`, `quote-summary`, and `options`) each triggered one bounded anonymous-session refresh and still ended in HTTP 401. The corresponding no-session requests made one attempt and retained the 401 as evidence.

The `quote-summary` and `options` error bodies said `Unauthorized: Invalid Crumb`. The Quote endpoint returned a different Unauthorized description, but the observed mode pattern was the same.

## Raw-hash interpretation

For Chart and Screener, the three parsed JSON objects were equal even though their raw SHA-256 values differed. Yahoo emitted the same object members in different key orders. Therefore, raw SHA-256 remains essential evidence integrity, but it must not be treated as a semantic-equality test.

Fundamentals Timeseries returned exactly the same bytes in all three modes.

Search succeeded in all modes. The cookie-only response contained six additional display-field occurrences (`sectorDisp` and `industryDisp` across three records), while the cookie-plus-crumb and no-session responses were otherwise the same apart from response timing metadata. This is an observation requiring repetition, not yet a permanent session-effect conclusion.

## Analyzer compatibility

- Analyzer exit status: `PASS`
- Samples: `21`
- Flattened field rows: `3847`
- Catalog paths: `510`
- Occurrence rows: `1530`
- Type conflicts: `0`

## Recommended small corrections before Study 02

1. Add a canonical parsed-JSON SHA-256 to distinguish semantic equality from raw byte equality.
2. Copy the resolved study definition into each run folder. The current manifest records its SHA-256 and all resolved requests, but its `study_definition_file` value is an owner-computer path rather than a portable evidence file.
3. Repeat Study 01 once before promoting the session observations from pilot findings to stronger endpoint rules.

## Conclusion

PASS — Study 01 produced a complete, internally consistent 21-sample evidence set. In this pilot, Quote, QuoteSummary, and Options required the sent crumb; Chart, Search, Screener, and Fundamentals Timeseries succeeded without prepared session state.
