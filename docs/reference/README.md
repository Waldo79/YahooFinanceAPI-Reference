# Yahoo Finance API reference pages

These pages document **observed** Yahoo Finance API behavior. They are not official Yahoo documentation.

## Reference pages

- [Market states](market-states.md)
- [Security types](security-types.md)
- [Endpoints](endpoints.md)
- [Timestamps and time zones](timestamps-and-timezones.md)
- [Symbol formats](symbol-formats.md)
- [Data-delay behavior](data-delay-behavior.md)

## Evidence rule

A value, field, endpoint behavior, or timing rule should be marked **confirmed** only after the supporting evidence has passed the project's observation-review process.

Recommended evidence includes:

- exact raw response data;
- symbol, endpoint, and request parameters;
- UTC request and response timestamps;
- HTTP status and content type;
- repeat observations when timing or availability may vary; and
- a note identifying whether the evidence came from direct Yahoo access or a third-party application.

Candidate values may be listed for testing, but they must remain clearly marked as unconfirmed until reviewed.
