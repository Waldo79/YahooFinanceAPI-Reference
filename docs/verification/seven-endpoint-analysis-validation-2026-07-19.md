# Seven-Endpoint Analysis Validation — 2026-07-19

- Source run: `2026-07-19T01-35-54.972Z_seven-endpoint-verification`
- ZIP integrity: `PASS`
- Source endpoints: `7`
- Analysis schema version: `0.5.0`
- Analyzer version: `0.1.0`

## Source evidence

| Endpoint | Manifest = metadata | Byte count | SHA-256 | JSON |
|---|---|---|---|---|
| `quote` | PASS | PASS | PASS | PASS |
| `chart` | PASS | PASS | PASS | PASS |
| `quote-summary` | PASS | PASS | PASS | PASS |
| `search` | PASS | PASS | PASS | PASS |
| `screener` | PASS | PASS | PASS | PASS |
| `fundamentals-timeseries` | PASS | PASS | PASS | PASS |
| `options` | PASS | PASS | PASS | PASS |

## Generated output validation

| File | Rows | Expected | Row count | SHA-256 |
|---|---:|---:|---|---|
| `field-catalog.csv` | 501 | 501 | PASS | PASS |
| `field-occurrence-long.csv` | 501 | 501 | PASS | PASS |
| `field-occurrence-matrix.csv` | 501 | 501 | PASS | PASS |
| `fields-long.csv` | 2367 | 2367 | PASS | PASS |
| `samples.csv` | 7 | 7 | PASS | PASS |
| `type-conflicts.csv` | 0 | 0 | PASS | PASS |

## Analysis summary

- Samples: `7`
- Flattened field rows: `2367`
- Catalog paths: `501`
- Occurrence rows: `501`
- Matrix rows: `501`
- Type conflicts: `0`
- Decoded UTC values: `155`

### Presence states

- `PRESENT_EMPTY_ARRAY`: 23
- `PRESENT_EXPLICIT_NULL`: 20
- `PRESENT_FALSE`: 79
- `PRESENT_VALUE`: 2148
- `PRESENT_ZERO`: 97

### JSON types

- `array`: 23
- `boolean`: 143
- `null`: 20
- `number`: 1569
- `string`: 612

## Additional consistency checks

- Every comparison path is array-normalized; no numeric array index remains.
- Every field row references a valid sample and matching endpoint.
- Every occurrence fingerprint reproduces from its underlying field rows.
- Every matrix code agrees with the long-form occurrence table.
- Non-applicable endpoint/sample matrix cells contain `X`.
- All long integer values that could lose spreadsheet precision are text-protected.
- All string values beginning with `=`, `+`, `-`, or `@` are spreadsheet-protected.
- All persisted crumb parameters contain the literal value `REDACTED`; no session secret was found.

## Conclusion

PASS — the generated analysis is internally consistent, traceable to the unchanged seven-endpoint source evidence, and suitable as the first field-inventory baseline.
