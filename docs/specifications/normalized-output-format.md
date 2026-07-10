# Normalized output format specification

## Status

Draft specification for v0.3.9.

## Objective

Produce a stable, human-readable representation of each captured response while preserving the original JSON hierarchy and raw evidence.

## Parsing rule

Always parse JSON with a JSON parser.

Never split JSON text on commas. Commas may appear inside strings, arrays, and nested structures.

## Required outputs

For each successful capture, create:

1. unchanged raw JSON;
2. metadata sidecar JSON;
3. normalized UTF-8 text;
4. normalized CSV or workbook output in a later implementation; and
5. an unmapped-fields report when new paths are discovered.

## Human-readable header

```text
Symbol: MSFT
Endpoint: quote
Requested UTC: 2026-07-10T22:05:30.000Z
Received UTC: 2026-07-10T22:05:30.123Z
HTTP status: 200
Result classification: SUCCESS_RESULT_RETURNED
Raw file: MSFT_quote_2026-07-10T22-05-30.123Z.raw.json
```

## Normalized field record

| Column | Description |
|---|---|
| `display_order` | Stable order from the master field database |
| `json_path` | Full path including array indexes or normalized array notation |
| `field_name` | Leaf field name |
| `raw_type` | JSON type: string, number, boolean, object, array, or null |
| `raw_value` | Exact scalar value or compact representation |
| `decoded_utc` | UTC decoding when the field is a recognized timestamp |
| `master_field_status` | Known, new, deprecated, missing expected, or conflicting |
| `notes` | Interpretation or review note |

## Ordering

1. Use the master field database's display order when a JSON path is known.
2. Keep related fields grouped by endpoint/module.
3. Place new or unmapped paths after known fields.
4. Sort unmapped paths deterministically by full JSON path.
5. Never discard a field solely because it is unknown.

## Arrays

- Preserve array order.
- Include array indexes in the evidence view.
- A comparison view may use normalized array notation only when identity can be preserved.
- Do not combine multiple array objects into one record without retaining their original indexes.

## Missing and null values

Use separate states:

- `PRESENT_VALUE`
- `PRESENT_EMPTY_STRING`
- `PRESENT_EMPTY_ARRAY`
- `PRESENT_EMPTY_OBJECT`
- `PRESENT_EXPLICIT_NULL`
- `MISSING_EXPECTED_PATH`
- `NOT_EXPECTED_FOR_ENDPOINT_OR_TYPE`

## Timestamp display

Show both values:

```text
regularMarketTime
  raw: 1783721130
  decoded_utc: 2026-07-10T...
```

The decoded value must never replace the raw epoch.

## Comparison indicators

A later comparison utility should identify:

- new path;
- removed or missing expected path;
- explicit null replacing a value;
- value replacing explicit null;
- JSON type change;
- timestamp movement;
- requested symbol missing from result;
- returned symbol mismatch;
- duplicate or conflicting paths; and
- application-derived result versus direct Yahoo evidence.

## Example normalized line

```text
0010 | quoteResponse.result[0].symbol | symbol | string | MSFT | | Known |
```

## Output stability

Given the same raw JSON, master-field database version, and formatter version, normalized output should be deterministic.
