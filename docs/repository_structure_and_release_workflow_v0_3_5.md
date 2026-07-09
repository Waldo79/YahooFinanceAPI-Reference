# Repository Structure and Release Workflow — v0.3.5

## Purpose

The repository should make it easy for public users to track observed Yahoo Finance API behavior without needing to be programmers.

The project should remain a public reference and change-tracking resource first. Scripts and tests are support tools.

## Folder model

| Folder | Purpose |
|---|---|
| `/data` | Canonical structured CSV tables. |
| `/docs` | Human-readable explanations and project guidance. |
| `/templates` | Reusable templates for public observations and releases. |
| `/observations/raw` | Original unreviewed evidence. |
| `/observations/reviewed` | Normalized observations after basic review. |
| `/observations/confirmed` | Confirmed observations suitable for release notes. |
| `/scripts` | Optional helper scripts for validation/repeatability. |
| `/tests` | Minimal release-integrity tests. |

## Release workflow

1. Observe.
2. Normalize.
3. Classify.
4. Retest.
5. Confirm.
6. Update reference data.
7. Document release.
8. Package.
9. Publish.

## Versioning rules

- Use `vMAJOR.MINOR.PATCH`.
- Use `-draft` until the release is committed or tagged.
- Keep workbook, README, CHANGELOG, MANIFEST, and ZIP version numbers aligned.
- Use patch releases for documentation/workflow refinements.
- Use minor releases for major endpoint or field coverage expansion.
- Reserve v1.0.0 for a stable public workflow and durable core tables.

## Non-programmer contribution path

Public users should be able to contribute by filling in CSV templates and attaching raw evidence. They should not need to modify code or understand the internal app behavior of any third-party software.
