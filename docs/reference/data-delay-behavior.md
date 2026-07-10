# Data-delay behavior

## Purpose

This page defines how to document whether a value appears real-time, delayed, stale, previous-session, or of unknown timing.

A displayed clock time without an accompanying date is not sufficient evidence of the date to which the data applies.

## Delay classifications

| Classification | Meaning |
|---|---|
| `REALTIME_OR_NEAR_REALTIME` | Observed timestamp closely follows the capture time within the expected market context. |
| `DELAYED_KNOWN` | A delay is stated or consistently measured. |
| `PREVIOUS_SESSION_VALUE` | The value clearly applies to an earlier trading session. |
| `STALE_UNKNOWN_AGE` | The value is older than expected, but its exact age is not established. |
| `NOT_APPLICABLE` | The field is not session-time-sensitive. |
| `UNKNOWN_REQUIRES_EVIDENCE` | Timing cannot be determined from available evidence. |

## Required evidence

Record:

- symbol and security type;
- endpoint and full field path;
- raw value;
- raw market timestamp;
- decoded UTC timestamp;
- capture UTC timestamp;
- market state;
- exchange time zone;
- whether the market was open, closed, on a half-day, or on a holiday;
- comparison source, when used; and
- the calculated age of the value at capture time.

## Age calculation

```text
observed_age_seconds = response_received_at_utc - decoded_market_timestamp_utc
```

This age does not automatically equal the vendor's formal delay. It is an observed age and may include batching, market inactivity, or a field that updates only when a trade occurs.

## Interpretation rules

- A prior-day price during market hours should be identified as prior-session data when the date can be established.
- A percentage-change field must be interpreted together with its current price, previous close, market state, and timestamps.
- Mutual funds and some funds may update on a different schedule than exchange-traded securities.
- An unchanged value is not necessarily stale if no new trade or calculation is expected.
- Third-party application formatting can conceal the applicable date; preserve raw timestamps whenever possible.
