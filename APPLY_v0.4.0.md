# Apply the YahooFinanceAPI-Reference v0.4.0 update

This package contains the complete v0.4.0 file set prepared against repository version v0.3.9.

## Files replaced

- `.gitignore`
- `README.md`
- `CHANGELOG.md`
- `ROADMAP.md`
- `tools/capture-utility/README.md`
- `tools/capture-utility/yahoo_capture.py`

## Files added

- `tools/capture-utility/symbols.csv`
- `tests/test_capture_utility.py`
- `docs/releases/v0.4.0-first-working-capture-utility.md`

## Apply with a local clone

From the parent directory of your clone, extract this ZIP so its files overwrite the matching paths in the repository. Then run from the repository root:

```bash
python -m pytest -q
python -m py_compile tools/capture-utility/yahoo_capture.py
python tools/capture-utility/yahoo_capture.py --dry-run
```

Commit after validation:

```bash
git add .gitignore README.md CHANGELOG.md ROADMAP.md \
  tools/capture-utility/README.md \
  tools/capture-utility/yahoo_capture.py \
  tools/capture-utility/symbols.csv \
  tests/test_capture_utility.py \
  docs/releases/v0.4.0-first-working-capture-utility.md

git commit -m "Implement v0.4.0 quote capture utility"
git push
```

## Expected validation

```text
5 passed
```

The dry run should display the 16 representative symbols in table order without contacting Yahoo.

## First live capture

```bash
python tools/capture-utility/yahoo_capture.py
```

Output is written below `captures/local/`, which v0.4.0 adds to `.gitignore`.
