# v0.5.0 Representative Sampling Program — Draft

## Status

Design draft for the controlled-coverage stage that follows successful seven-endpoint
live verification and deterministic field analysis.

This document defines study structure and acceptance rules. It does not authorize a
large unattended capture run. Each study should first be executed as a small pilot,
reviewed, and then expanded deliberately.

## 1. Purpose

The sampling program will determine how Yahoo Finance JSON fields and structures vary
across:

- endpoint families;
- security types;
- exchanges and regions;
- request parameters;
- capture dates and repeated runs;
- Yahoo-reported market states;
- valid, empty, unsupported, and invalid subjects; and
- authentication/session modes.

The program must preserve enough metadata to distinguish a genuine field difference
from a different request, subject, time, market state, or response condition.

## 2. Governing principles

1. Change one controlled variable at a time whenever practical.
2. Use one subject per request during initial coverage studies.
3. Preserve the unchanged response body and its SHA-256 before analysis.
4. Record exact canonical request parameters for every sample.
5. Treat errors, empty results, omissions, and rate limits as evidence.
6. Never write cookie, crumb, authorization, or session values to evidence.
7. Do not infer that a field is universally absent from a single sample.
8. Do not merge samples whose request fingerprints differ.
9. Retain exact indexed evidence paths and normalized comparison paths.
10. Regenerate analysis deterministically from the preserved evidence.

## 3. Study sequence

The controlled program should proceed in the following order.

### Study 01 — Session-mode requirements

Objective: determine which endpoint families actually require prepared Yahoo session
state.

For each of the seven verified request patterns, compare:

1. anonymous cookie plus crumb;
2. anonymous cookie without adding the crumb; and
3. no prepared Yahoo cookie or crumb.

Keep the subject and every non-session request parameter unchanged.

Record:

- HTTP status;
- content type;
- response bytes and SHA-256;
- valid-JSON status;
- expected top-level object status;
- endpoint-level result status;
- retry and authentication-refresh behavior; and
- any Yahoo error object.

Acceptance requirement: each result must be attributable to exactly one documented
session mode without persisting the secret values.

### Study 02 — Security-type baseline

Objective: establish initial field occurrence by project security type.

Create a reviewed subject manifest containing at least one valid representative for
each available project category:

- common stock;
- exchange-traded fund;
- closed-end fund;
- mutual fund;
- market index;
- preferred or other listed equity;
- real-estate investment trust;
- currency pair;
- cryptocurrency;
- futures contract;
- exchange-traded note or similar product; and
- a subject known not to support listed options.

Not every endpoint is meaningful for every category. The study manifest must state
whether a subject/endpoint pair is:

- expected and evaluated;
- expected to return a legitimate empty result;
- not expected for that security type; or
- intentionally deferred.

Use `AAPL` only as the already-verified continuity subject. Select the remaining
subjects from the project's reviewed symbol inventory rather than embedding an
unverified permanent symbol list in code.

### Study 03 — Exchange and region baseline

Objective: distinguish exchange/region effects from security-type effects.

For common stocks, select reviewed representatives from:

- United States primary listings;
- at least one Canadian listing;
- at least one United Kingdom listing;
- at least two continental European suffix conventions;
- at least one Asia-Pacific listing; and
- at least one symbol whose Yahoo suffix, exchange code, or currency requires special
  handling.

Include `INGA.AS` as one continuity subject because it has already been used in the
project's corrected symbol set.

For each subject, retain:

- project-expected exchange;
- Yahoo exchange and full-exchange names when returned;
- Yahoo region and language settings;
- currency;
- exchange timezone;
- quote type;
- source interval and applicable timestamps; and
- any returned symbol normalization.

### Study 04 — Market-state transition baseline

Objective: identify fields that appear, disappear, or change interpretation around
market-state transitions.

Do not assume an exhaustive fixed list of Yahoo `marketState` values. Record the
literal value returned in each sample and group observations by capture window.

For selected liquid U.S. subjects, capture controlled samples during:

- before the premarket window;
- premarket;
- shortly before the regular open;
- regular trading;
- shortly before the regular close;
- after-hours;
- after the after-hours window; and
- a weekend or full market holiday.

Capture UTC timestamps and the subject exchange timezone. Scheduled windows should be
wide enough to tolerate clock and network variance, but every actual request timestamp
must remain recorded.

The first pilot should use Quote only. Expand to Chart and other endpoints after the
Quote transition records are reviewed.

### Study 05 — Endpoint parameter baselines

Objective: isolate parameter-dependent structures.

#### Quote

- one symbol;
- controlled multi-symbol batching as a separate study;
- formatted false versus formatted true only after raw-value behavior is established;
- region/language variation only as an explicitly labeled study.

#### Chart

Vary one dimension at a time:

- range;
- interval;
- includePrePost;
- event selection; and
- known periods containing dividends, splits, or capital-gain events.

Record null bars, array lengths, timestamp alignment, trading periods, and interval
metadata.

#### QuoteSummary

Begin with one module per request, then small documented module groups.

Track:

- requested modules;
- returned modules;
- module omissions;
- module-level error behavior;
- `raw`, `fmt`, and `longFmt` representations; and
- security-type applicability.

#### Search

Use controlled query classes:

- exact symbol;
- exact company or fund name;
- partial name;
- ambiguous term;
- exchange-suffixed symbol;
- punctuation-bearing symbol;
- invalid query; and
- empty or whitespace query only when intentionally testing validation behavior.

Track result category, ordering, identity, count controls, and records without a
tradable symbol.

#### Screener

Begin with reviewed predefined screeners.

Vary:

- screener ID;
- count;
- offset; and
- paging behavior.

Treat custom POST screener definitions as a later, separately specified study.

#### Fundamentals Timeseries

Use one metric per first-pass request.

Then compare:

- annual;
- quarterly;
- trailing;
- valuation; and
- deliberately unsupported or unavailable metrics.

Record exact metric names, result blocks, date bounds, `asOfDate`, timestamps, and
reported values. Metric chunking must be tested separately from field coverage.

#### Options

Compare:

- default nearest-expiration response;
- one explicitly selected valid expiration;
- another later valid expiration;
- an invalid expiration; and
- a subject without listed options.

Use contract symbol as the preferred contract identity. Where absent, use a documented
composite identity consisting of option type, expiration, and strike.

### Study 06 — Repeated-day stability

Objective: distinguish transient values from structural changes.

Repeat selected stable request fingerprints:

- at the same approximate market window on at least three trading days;
- once during a weekend or holiday condition where applicable; and
- after any significant endpoint-adapter or analyzer version change.

Compare:

- path occurrence;
- JSON type;
- array identity and ordering;
- empty/null state;
- top-level errors;
- response-size changes; and
- field-definition review notes.

### Study 07 — Negative and boundary behavior

Controlled negative samples should include:

- invalid symbol;
- delisted or unavailable subject when responsibly identified;
- valid subject unsupported by an endpoint;
- empty result;
- invalid parameter;
- unsupported interval/range combination;
- invalid option expiration;
- pagination past the available result set;
- deliberate authentication-mode variation; and
- observed 401, 403, 429, or retryable 5xx responses.

Do not manufacture excessive traffic merely to provoke rate limiting.

## 4. Pilot size and expansion gates

Each study begins with the smallest useful pilot:

- one request per endpoint/condition unless repetition is the variable;
- no parallel requests;
- project default added pause remains 0 ms unless pause is the controlled variable;
- automatic retries remain documented and bounded; and
- stop the pilot when an unexpected schema, privacy, or validation failure appears.

Expansion requires:

1. all source files passing byte-count and SHA-256 validation;
2. no persisted session secret;
3. deterministic analyzer output;
4. reviewed sample classifications;
5. reviewed subject metadata;
6. no unexplained path-identity collisions; and
7. an approved next subject/condition manifest.

## 5. Study manifest

Each study should have a CSV or JSON manifest containing at least:

```text
study_id
study_version
study_title
study_variable
request_id
endpoint_id
subject
subject_class
project_security_type
expected_exchange
expected_currency
market_window_target
session_mode
canonical_parameters
repeat_number
enabled
notes
```

The manifest must separate study design from capture results. Capture output must never
silently alter the planned subject or parameters.

## 6. Required sample metadata additions

Future multi-study captures should add or preserve:

```text
study_id
study_version
study_variable
study_condition
repeat_number
session_mode
capture_label
project_security_type
expected_exchange
expected_currency
market_window_target
yahoo_market_state_observed
request_parameters_canonical
request_parameters_sha256
```

`yahoo_market_state_observed` is derived from the response and must not replace the
planned `market_window_target`.

## 7. Comparison rules

Field comparisons must be scoped at minimum by:

- endpoint ID;
- canonical request fingerprint;
- project security type;
- expected exchange or exchange group;
- session mode;
- market-window target;
- observed Yahoo market state;
- analyzer schema version; and
- study version.

A path absent from an inapplicable endpoint/type combination is
`NOT_EXPECTED_FOR_ENDPOINT_OR_TYPE`, not `MISSING_EXPECTED_PATH`.

A path expected for the comparison group but not observed in one valid sample is
`MISSING_EXPECTED_PATH`.

## 8. Outputs

Every completed study should produce:

```text
study-manifest
capture run manifest
unchanged raw responses
metadata sidecars
samples.csv
fields-long.csv
field-catalog.csv
field-occurrence-long.csv
field-occurrence-matrix.csv
type-conflicts.csv
validation.json
study findings report
```

Later workbook generation should consume these validated CSV outputs rather than parse
raw Yahoo JSON independently.

## 9. Initial implementation boundary

The next code implementation should support Study 01 first:

- explicit session-mode selection;
- the same seven minimal verified requests;
- identical evidence and redaction rules;
- one run manifest containing all three modes; and
- grouped comparison output.

Broad security-type or market-state capture should wait until Study 01 is complete and
the production shared capture engine has replaced the pre-implementation verification
script.
