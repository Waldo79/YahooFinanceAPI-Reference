# Study 01 Session-Mode Protocol — v0.5.0 Draft

## Status

Implementation-ready pilot protocol for determining whether each verified Yahoo
Finance endpoint family requires prepared anonymous session state.

The study performs exactly 21 controlled requests:

```text
7 endpoint request patterns × 3 session modes
```

It is a small owner-run pilot, not an unattended or repeated traffic program.

## Session modes

### `cookie-crumb`

A fresh anonymous Yahoo session is prepared by obtaining cookies and a crumb. The
crumb is added to the endpoint request.

### `cookie-only`

A separate fresh anonymous Yahoo session undergoes the same cookie-and-crumb
preparation procedure, but the crumb is deliberately omitted from the endpoint
request.

This isolates the effect of sending the crumb while keeping the preparation procedure
comparable. The retrieved crumb remains in memory and is never persisted.

### `no-session`

A separate fresh opener sends the endpoint request without a prepared Yahoo cookie jar
or crumb. Cookies returned by one request are not intentionally retained for later
requests in this mode.

## Request order

The run uses endpoint-major order. For each verified endpoint request, the three modes
run consecutively in this order:

```text
cookie-crumb
cookie-only
no-session
```

Exact UTC timestamps are retained, so temporal separation remains measurable.

## Study definition

The controlled request plan is stored separately from capture results:

```text
config/studies/study-01-session-modes.json
```

It contains:

- study identity and version;
- the continuity subject `AAPL`;
- session-mode definitions and order;
- all seven verified request patterns;
- expected top-level objects; and
- the dynamic Fundamentals Timeseries period rule.

The run resolves dynamic period bounds once at study start. All three session modes for
that endpoint therefore use identical non-session parameters and the same request
fingerprint.

## Evidence structure

```text
captures/local/
  <UTC>_study-01-session-modes/
    run-manifest.json
    raw/
      cookie-crumb/
      cookie-only/
      no-session/
    metadata/
      cookie-crumb/
      cookie-only/
      no-session/
    errors/
      cookie-crumb/
      cookie-only/
      no-session/
    comparison/
      session-mode-results.csv
      endpoint-session-summary.csv
```

Every HTTP response body is saved unchanged, including error responses. A network
failure produces metadata and an empty raw-response file.

## Required metadata

Each sample records:

- study ID and version;
- study condition and session mode;
- sequence and sample ID;
- request and endpoint IDs;
- canonical non-session parameters and SHA-256 fingerprint;
- redacted final URL;
- HTTP status and content type;
- response byte count and SHA-256;
- JSON parse status;
- expected-top-level status;
- classification;
- exact attempt timestamps;
- retry and authentication-refresh behavior;
- session strategy;
- cookie count;
- whether a crumb was retrieved;
- whether a crumb was sent; and
- confirmation that sensitive values were not persisted.

Cookie values, crumb values, authorization values, and request headers containing
secrets must never be written.

## Retry rules

- `429`, `500`, `502`, `503`, and `504` may use bounded retries.
- A prepared mode receiving `401` or `403` may refresh its anonymous session once and
  retry.
- `no-session` must not prepare or refresh authentication state after `401` or `403`;
  that response is retained as evidence.
- The project-added pause defaults to 0 ms.
- No parallel requests are used.

## Interpretation rules

An HTTP error is not a discarded sample.

The result should be interpreted as follows:

- expected object in all three modes: the endpoint did not require the prepared session
  for this request at this time;
- expected object only with `cookie-crumb`: evidence that both prepared cookies and the
  crumb may be required;
- expected object with both prepared modes but not `no-session`: evidence that prepared
  session state may matter while the sent crumb may not;
- expected object in `cookie-only` and `no-session` but not `cookie-crumb`: unexpected;
  inspect raw evidence and request timing;
- no mode returns the expected object: do not conclude session requirements; review
  endpoint availability, parameters, and Yahoo errors.

A single pilot is evidence for the tested request and time, not a permanent universal
rule.

## Commands

From the repository root:

```bat
py tools\session-mode-study\run_session_mode_study.py --dry-run
```

Then run the live study:

```bat
py tools\session-mode-study\run_session_mode_study.py
```

Analyze the completed run:

```bat
py tools\endpoint-analysis\analyze_endpoint_captures.py "<study run folder>"
```

Run unit tests on Windows with a repository-local pytest temporary directory:

```bat
rmdir /s /q .pytest-temp 2>nul
py -m pytest -q tests\test_session_mode_study.py --basetemp=".pytest-temp"
```

## Pilot acceptance criteria

The pilot is complete when:

1. all 21 planned evidence records are written;
2. every raw file matches its metadata byte count and SHA-256;
3. every manifest request entry matches its metadata sidecar;
4. the two comparison CSV files are produced;
5. no cookie or crumb value is persisted;
6. the endpoint analyzer processes all 21 samples deterministically; and
7. findings are reviewed before any repetition or larger sampling program.
