# Market states

## Purpose

This page tracks values observed in Yahoo Finance fields such as `marketState`, together with the conditions under which each value was seen.

Market-state values are not assumed to have one universal start or stop time. Their meaning may vary by exchange, instrument type, trading session, holiday, and Yahoo data source.

## Candidate values for verification

The following values are candidates for structured testing. Their presence here does **not** make them confirmed or exhaustive.

| Candidate value | Working interpretation | Verification status |
|---|---|---|
| `PREPRE` | Period before the normal pre-market session | Candidate |
| `PRE` | Pre-market session | Candidate |
| `REGULAR` | Regular trading session | Candidate |
| `POST` | Post-market session | Candidate |
| `POSTPOST` | Period after the normal post-market session | Candidate |
| `CLOSED` | Market or instrument not currently in an active session | Candidate |

## Confirmation table

Add a row only when evidence has been reviewed.

| Value | Project category | Security type | Exchange/market | Session start UTC | Session end UTC | First observed UTC | Last confirmed UTC | Evidence link | Status | Notes |
|---|---|---|---|---|---|---|---|---|---|---|
|  |  |  |  |  |  |  |  |  |  |  |

## Timing guidance

- Record all observation times in UTC.
- Keep the raw timestamp or epoch value when one is supplied.
- Record the exchange time zone separately from UTC.
- Do not infer a session boundary from a single observation.
- Repeat observations around the expected transition time.
- Test normal days, half-days, holidays, and overnight instruments separately.
- Treat a third-party application's displayed state as application evidence unless the underlying Yahoo response is preserved.

## Suggested transition test

For each selected symbol:

1. Capture once every few minutes before the expected session transition.
2. Continue through the transition and for a reasonable period afterward.
3. Preserve every raw response and metadata sidecar.
4. Compare the first appearance and last appearance of each state.
5. Repeat on more than one date before confirming a general timing rule.
