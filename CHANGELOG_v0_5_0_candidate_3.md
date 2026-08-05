# Fast-mode v0.5.0-candidate.3

## External capture storage

- Raw Fast-mode runs now resolve to external storage rather than
  `captures/local/fast-mode` inside the synchronized repository.
- The safe default for a repository under `.../YAHOO/Code/` is
  `.../YAHOO/Captures/fast-mode`.
- Output-root resolution order is command line, environment variable, ignored
  local config, then safe default.
- `--output-root` is the preferred option; `--outdir` remains a compatibility
  alias.
- Repository-contained output paths are rejected before network access.
- The external destination is created and write-tested before a live run.

## Machine-local configuration

- Added `--configure-output-root PATH` to write the ignored local config.
- Added `--show-output-root` to verify storage without reading symbol input or
  contacting Yahoo.
- Added tracked placeholder file
  `config/local/fast_mode_local.example.json`.
- Added `.gitignore` protection for the real
  `config/local/fast_mode_local.json`.
- Updated repository hygiene tests to require that ignore rule and reject any
  accidentally tracked machine-local config.

## Resume and manifests

- Resume folders must exist outside the repository and contain
  `checkpoint.jsonl`.
- Run manifests record the external-storage policy, resolution source, and run
  folder name without persisting the absolute local path.
- Checkpoints, raw responses, metadata, and summaries remain together in the
  external run folder.

## Migration and documentation

- Added `scripts/migrate-fast-mode-captures.ps1`, which copies the legacy local
  capture tree to external storage and verifies every file by size and SHA-256.
- The migration script deletes nothing.
- Added `docs/high-volume/EXTERNAL_CAPTURE_STORAGE.md` and updated the Fast-mode
  README.

## Tests

- Added eight offline storage tests covering precedence, safe defaults,
  repository blocking, local-config round trip, write testing, resume checks,
  option aliases, and no-network output-root display.
- Candidate-specific result: 25 tests passed.
- No Yahoo endpoint request or response-classification logic changed.
