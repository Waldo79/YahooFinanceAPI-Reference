# Security types

## Purpose

This page separates the project's user-facing security categories from Yahoo's raw classification fields.

A project category should not automatically be treated as identical to a Yahoo field such as `quoteType`, `typeDisp`, or an exchange-specific classification.

## Standard coverage set

The project currently uses the following representative symbols. A third symbol can be added to each category as the standard test set is expanded.

| Project category | Representative 1 | Representative 2 | Representative 3 | Raw Yahoo classification | Status |
|---|---|---|---|---|---|
| Stock | `AAPL` | `MSFT` | TBD | To be observed | Active coverage |
| ETF | `SPY` | `QQQI` | TBD | To be observed | Active coverage |
| Closed-end fund | `NAC` | `PDI` | TBD | To be observed | Active coverage |
| Mutual fund | `XNACX` | `VTSAX` | TBD | To be observed | Active coverage |
| Futures | `ZT=F` | `CL=F` | TBD | To be observed | Active coverage |
| Index | `^GSPC` | `^DJI` | TBD | To be observed | Active coverage |
| Cryptocurrency | `BTC-USD` | `ETH-USD` | TBD | To be observed | Active coverage |
| Foreign exchange | `EURUSD=X` | `JPY=X` | TBD | To be observed | Active coverage |

## Classification record

When a raw response is available, record the classifications exactly as returned.

| Symbol | Project category | Field path | Raw value | Endpoint | Observed UTC | Evidence link | Status | Notes |
|---|---|---|---|---|---|---|---|---|
|  |  |  |  |  |  |  |  |  |

## Rules

- Preserve the exact Yahoo classification value and its full JSON path.
- Do not force closed-end funds into the ETF category merely because one endpoint uses an ETF-like value.
- Record differences between endpoints rather than choosing one endpoint as automatically correct.
- A missing classification is different from an explicit `null`.
- A classification produced by a third-party application should be identified as application-derived unless the raw Yahoo field is available.
