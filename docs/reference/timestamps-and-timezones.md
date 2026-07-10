# Timestamps and time zones

## Project standard

All project-generated timestamps must use Coordinated Universal Time (UTC).

Preferred display form:

```text
2026-07-10T22:05:30.123Z
```

Preferred filename form:

```text
2026-07-10T22-05-30.123Z
```

Colons are removed from filenames for broad operating-system compatibility.

## Preservation rules

- Preserve every raw numeric epoch timestamp exactly as returned.
- Add a decoded UTC value beside the raw value; do not replace the raw value.
- Record the field's full JSON path.
- Record the assumed epoch unit: seconds, milliseconds, microseconds, or nanoseconds.
- Mark uncertain units as uncertain rather than guessing.
- Preserve any exchange time-zone name and UTC offset returned by Yahoo.
- Do not label local computer time as GMT or UTC unless it has actually been converted.

## Recommended normalized representation

| JSON path | Raw value | Raw type | Epoch unit | Decoded UTC | Exchange time zone | Notes |
|---|---:|---|---|---|---|---|
| `quoteResponse.result[0].regularMarketTime` |  | number | seconds |  |  |  |

## Capture timestamps

Each request should record at least:

- `requested_at_utc`
- `response_received_at_utc`
- `elapsed_ms`

A complete run should also record:

- `run_started_at_utc`
- `run_completed_at_utc`

## GMT wording

UTC is the preferred technical label in project files. The term GMT may be used in user-facing explanations, but stored timestamps should be identified as UTC.
