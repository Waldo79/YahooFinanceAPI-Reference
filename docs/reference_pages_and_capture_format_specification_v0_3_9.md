# Reference Pages and Capture Format Specification — v0.3.9

## Purpose

v0.3.9 defines the documentation and evidence format needed before building an automated Yahoo Finance capture and comparison utility.

## Added

### Reference pages

- Market states
- Security types
- Endpoint registry
- Timestamps and time zones
- Symbol formats
- Data-delay behavior

### Specifications

- Sequential capture of up to 30 table-selected symbols
- One request per symbol in the first implementation
- Exact raw-response preservation
- UTC request and response timestamps
- Metadata sidecar files
- SHA-256 evidence integrity
- Fixed-order normalized output
- Full JSON-path preservation
- Separate handling for missing fields, explicit nulls, empty results, and requested symbols omitted from results

## Key design decision

Raw JSON must remain valid and unchanged. The requested symbol and UTC capture information are placed in the filename, metadata sidecar, and normalized output rather than being prepended as a non-JSON header.

## Scope

This release supplies documentation and templates. It does not yet include a production downloader, timer, scheduler, or JSON formatter.

## Next planned release

v0.4.0 can implement the first capture utility using these specifications, beginning with:

- a user-editable symbol table;
- sequential single-symbol requests;
- a run manifest;
- raw and metadata files;
- conservative delay and retry controls; and
- normalized text output.
