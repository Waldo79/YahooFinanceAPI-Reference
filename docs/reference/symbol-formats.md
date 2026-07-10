# Symbol formats

## Purpose

This page records symbol forms used in project testing. It is not an exhaustive Yahoo symbol specification.

## Current examples

| Project category | Example | Notable characters | Filename-safe form |
|---|---|---|---|
| Stock | `AAPL` | Letters | `AAPL` |
| ETF | `SPY` | Letters | `SPY` |
| Closed-end fund | `NAC` | Letters | `NAC` |
| Mutual fund | `VTSAX` | Letters | `VTSAX` |
| Futures | `ZT=F` | Equals sign | `ZT_F` |
| Index | `^GSPC` | Caret prefix | `GSPC` |
| Cryptocurrency | `BTC-USD` | Hyphen | `BTC-USD` |
| Foreign exchange | `EURUSD=X` | Equals sign | `EURUSD_X` |

## Rules

- Store the exact requested symbol in metadata.
- Use a separate filename-safe form only for the file path.
- Do not silently change case.
- Do not remove suffixes such as `=F` or `=X` from the request.
- Do not treat a complete endpoint URL as a symbol.
- Record aliases or exchange-qualified forms as separate test cases.
- If Yahoo returns a canonical symbol different from the requested symbol, preserve both.

## Recommended metadata fields

```json
{
  "requested_symbol": "^GSPC",
  "returned_symbol": "^GSPC",
  "filename_symbol": "GSPC",
  "project_security_type": "Index"
}
```
