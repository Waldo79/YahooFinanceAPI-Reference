# Long-History Full XLSX Export Validation — 2026-08-06

## Result

**PASS.** The verified compact Long-history archive completed a full read-only XLSX
export. The generated workbook passed ZIP integrity and SHA-256 verification, and
the compact source fingerprint remained unchanged.

The full workbook, source databases, JSON manifest, and text report remain external
evidence. This repository document records only non-sensitive validation facts and
does not include an absolute local path or the 739 MB workbook.

## Evidence identity

| Item | Verified value |
|---|---:|
| Export utility | `0.1.0-candidate.11` |
| Compact schema | `compact_long_history` version `1` |
| Compact build status | `ACTIVE_COMPACT` |
| Started UTC | `2026-08-06T23:35:28.902Z` |
| Completed UTC | `2026-08-06T23:54:44.488Z` |
| Elapsed time | 19 minutes 15.586 seconds |
| Symbols selected | 1,537 |
| Bars exported | 11,034,219 |
| Events exported | 256,040 |
| Workbook bytes | 739,149,341 |
| Workbook SHA-256 | `95ddabc95b57241172d168880878f1f85f35ab909f632e252a031bfdc15af848` |
| SQLite quick check | `ok` |

No symbol, interval, or date filter was applied. Revision sheets were not requested.

## Worksheet verification

The workbook contains 15 worksheets:

- `Summary`: 15 data rows;
- `Symbols`: 1,537 data rows;
- `Bars` through `Bars_011`: 1,000,000 data rows each;
- `Bars_012`: 34,219 data rows; and
- `Events`: 256,040 data rows.

The 12 Bars worksheets sum exactly to 11,034,219 rows. Every Bars worksheet stays
below Excel's 1,048,576-row limit after its header row is included.

## Safety verification

The exported manifest and report agree on all of the following:

- the source was `history_compact.sqlite`;
- SQLite opened the verified compact database read-only with `PRAGMA query_only=ON`;
- the original `history.sqlite` database was not opened;
- no network request was sent;
- no database was moved, renamed, replaced, or deleted;
- only a new external export folder and workbook were written; and
- the compact database fingerprint was unchanged after export.

## Candidate.13 progress follow-up

After candidate.13 added progress reporting, a five-symbol live export also passed.
It reported immediate phases, 5,000-row Bars intervals, per-worksheet packaging, ZIP
verification, source fingerprint verification, hashing, and report creation. That
export produced 29,210 Bars rows and 136 Events rows in approximately 10.74 seconds.

Candidate.13 changes progress visibility only. Focused comparison testing confirmed
that its Symbols, Bars, Events, and revision worksheet XML matched candidate.11 for
the same source data, apart from intentional utility-version metadata.

## Conclusion

The Long-history v0.1.0 implementation is operationally validated for baseline
preservation, compact synchronization, reviewed exclusions, and read-only XLSX
export. Formal milestone tagging remains a separate, explicitly authorized action.
The project-wide formal release remains v0.4.3, and the separate comparative-study
development line remains v0.5.0-draft.
