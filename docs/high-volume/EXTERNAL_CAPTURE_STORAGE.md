# External Fast-mode capture storage

Fast-mode raw responses, metadata, checkpoints, manifests, and large summary
files belong outside the synchronized Git repository.

## Standard layout

For a repository located at:

```text
...\YAHOO\Code\YahooFinanceAPI-Reference-main
```

the safe default capture root is:

```text
...\YAHOO\Captures\fast-mode
```

The utility derives that location automatically. A machine-local ignored JSON
file can make the choice explicit and preserve it if command habits change.

## Configure this computer

From the repository root in Command Prompt:

```text
py tools\capture-utility\yahoo_fast_capture.py --configure-output-root "%USERPROFILE%\Downloads\YAHOO\Captures\fast-mode"
```

This creates:

```text
config\local\fast_mode_local.json
```

The real local file is ignored by Git. Only
`config/local/fast_mode_local.example.json` is tracked.

Verify the resolved destination without loading input data or contacting Yahoo:

```text
py tools\capture-utility\yahoo_fast_capture.py --show-output-root
```

## Resolution order

The utility resolves storage in this order:

1. `--output-root PATH` on the command line;
2. `YAHOO_FAST_CAPTURE_ROOT` environment variable;
3. ignored `config/local/fast_mode_local.json`;
4. the safe external default beside the `Code` directory.

`--outdir` remains an alias for backward compatibility. Any destination inside
the repository is rejected before network access.

## Copy and verify existing captures

The migration script copies every existing file from the legacy ignored folder
to the external archive and verifies file size plus SHA-256. It deletes nothing.

```text
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\migrate-fast-mode-captures.ps1
```

After it reports `COPY AND VERIFY PASSED`, inspect the external destination.
Keep the old source until the copy has been reviewed and backed up.

## What remains in GitHub

Keep these online:

- capture programs and offline tests;
- small symbol lists and configuration templates;
- sanitized validation summaries;
- manifests and checksums that contain no private path or authentication data;
- small synthetic fixtures.

Keep these external and local:

- raw JSON/HTTP responses;
- large CSV exports;
- checkpoints and resumable run folders;
- full request logs and audit ZIPs;
- any file that might contain cookies, crumbs, tokens, or local private paths.
