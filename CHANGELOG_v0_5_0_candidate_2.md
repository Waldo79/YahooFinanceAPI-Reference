# YahooFinanceAPI-Reference v0.5.0-candidate.2

Date: 2026-08-04

## Smoke-run corrections

- Separates actual network **task** classifications from per-symbol endpoint
  classifications in `run-summary.txt`.
- Adds the explicit per-symbol result count to the text summary.
- Marks only the second intentional duplicate occurrence as reusing a shared
  batched Quote result; the first occurrence is no longer mislabeled.
- Classifies an Options response containing only quote metadata and no
  expiration dates or option sets as `NOT_OPTIONABLE_OR_NO_CHAIN`.
- Preserves `NOT_OPTIONABLE_OR_NO_CHAIN` as a normal recorded outcome that
  does not abort the run or require review.
- Adds regression tests for all corrections above.

## Existing candidate capabilities retained

- Batched Quote requests.
- Concurrent QuoteSummary/Fundamental, Chart, and Options stages.
- Automatic individual Quote retests for batch omissions.
- Retry/backoff, checkpoint/resume, raw-response hashes, privacy redaction,
  request-level accounting, and the prepared 1,547-row input universe.
- Existing v0.4.3 capture utility remains unchanged.

## Candidate validation target

- Python compile check.
- Candidate offline test suite.
- Full-universe dry run: 4,657 initial tasks.
- Smoke dry run: 91 initial tasks.
