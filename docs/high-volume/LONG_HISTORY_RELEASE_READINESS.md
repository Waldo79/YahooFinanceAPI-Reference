# Long-History v0.1.0 Release Readiness

Status: **ready for a bounded Long-history milestone release; no tag or GitHub
Release has been created.**

## Completed evidence

- [x] Full original-history baseline preserved externally.
- [x] Verified compact rebuild retained beside the original archive.
- [x] Compact incremental synchronization validated on a separate copy and in place.
- [x] Reviewed Long-history exclusions integrated without deleting prior evidence.
- [x] Read-only XLSX exporter validated on five symbols and the full 1,537-symbol set.
- [x] Full workbook ZIP integrity and SHA-256 verified.
- [x] Original `history.sqlite` remained unopened during export.
- [x] Verified compact database fingerprint remained unchanged during export.
- [x] Progress reporting validated during a live workbook build.
- [x] Offline repository suite reached 221 passing tests before candidate.14.

## Repository boundary

The milestone source contains utilities, tests, documentation, and reviewed policy
files only. It does not contain either SQLite database, raw capture evidence, a full
XLSX workbook, machine-local configuration, or absolute local paths. Those artifacts
remain under the external Long-history archive.

## Remaining authorized release actions

- [ ] Merge candidate.14 after its complete local and GitHub checks pass.
- [ ] Confirm the final release commit SHA on clean `main`.
- [ ] Create a subsystem milestone tag only after explicit authorization. A bounded
      name such as `long-history-v0.1.0` avoids implying that the project-wide formal
      release has advanced from v0.4.3.
- [ ] Create a GitHub Release from that tag only after explicit authorization.
- [ ] Attach or link only safe release notes; do not upload either database, raw
      captures, machine-local paths, or the 739 MB workbook.

## Release boundary

Candidate.14 prepares evidence and checks only. It does not create or push a tag,
create a GitHub Release, alter the project-wide v0.4.3 release, or change the separate
v0.5.0-draft comparative-study line.
