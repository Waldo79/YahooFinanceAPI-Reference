# v0.3.7 — Issue Templates and Observation Forms

This release adds GitHub-ready issue forms for public Yahoo Finance API observations.

The project is intended to collect repeatable public observations about Yahoo Finance API behavior, not to operate as an app-debugging project. The forms are designed so non-programmers can report useful information without source-code access.

## Added issue templates

- Field added / removed / changed
- Symbol missing from result
- Endpoint empty or null result
- MarketState observation
- Schema drift observation
- Bad or stale quote data
- Mutual fund / NAV timing observation
- Special-symbol problem
- Documentation correction

## Reporting principle

A report should include enough detail for another public user to understand what was requested, what was returned, and why the behavior may represent an API change or data-quality issue.

Raw JSON, CSV output, or copied error text is preferred over screenshots.

## Classification principle

Do not over-classify a single observation as a confirmed Yahoo change. Use the observation lifecycle:

1. raw observation
2. reviewed observation
3. repeatable observation
4. confirmed change record
5. release documentation

## App-user note

When a third-party app only exposes a symbol lookup box, treat full endpoint URLs entered into that box as app-compatibility observations, not as direct Yahoo endpoint tests.
