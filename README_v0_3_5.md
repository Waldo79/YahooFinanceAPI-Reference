# YahooFinanceAPI-Reference v0.3.5-draft

A public reference and change-tracking project for observed Yahoo Finance API behavior.

## Primary purpose

This project helps public users track Yahoo Finance API fields, endpoint outputs, schemas, market-state behavior, symbol coverage, missing/null results, and observed changes over time.

It is **not primarily a software-development project** and is **not an app-debugging project**. Scripts, tests, and validators are included only as support tools for repeatable public observation, documentation, and release packaging.

## Release focus

**v0.3.5 — Repository Structure and Release Workflow**

This release adds a GitHub-oriented repository layout and public release workflow so the project can be maintained as a reference project:

- recommended folder structure
- what belongs in `/data`, `/docs`, `/templates`, `/observations`, `/scripts`, and `/tests`
- release workflow from raw observation to confirmed change record
- versioning rules
- non-programmer contributor workflow
- release checklist template

The master field database remains at **198 rows**. No Yahoo API field rows were added in this release.

## Recommended repository structure

```text
YahooFinanceAPI-Reference/
  README.md
  CHANGELOG.md
  MANIFEST.csv
  YahooFinanceAPI_Reference_Master_Field_Database_v0_3_5.xlsx
  data/
    master_field_database.csv
    representative_symbols.csv
    result_interpretation_rules.csv
    repository_structure.csv
    release_workflow.csv
  docs/
    repository_structure_and_release_workflow_v0_3_5.md
  templates/
    symbol_lookup_run_log_template.csv
    change_record_template.csv
    release_checklist_template.csv
  observations/
    raw/
    reviewed/
    confirmed/
  scripts/
  tests/
```

## Public user workflow

1. Download the latest release package.
2. Use the templates to record symbol lookup or endpoint observations.
3. Preserve raw evidence when practical.
4. Classify the observation using the result interpretation rules.
5. Submit the observation through GitHub issue or pull request.
6. Retest or confirm before promoting the item into a documented change record.

## Using the ZIP

This is the normal commit-ready ZIP with folders preserved. If Windows built-in ZIP extraction fails, try 7-Zip. The package is a standard ZIP archive.

## Disclaimer

This is not an official Yahoo Finance project. It documents public observations of Yahoo Finance API behavior and may include temporary, incomplete, or environment-specific findings until confirmed.
