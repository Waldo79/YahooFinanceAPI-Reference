# Yahoo Finance Capture Utility

This utility captures raw Yahoo Finance JSON and saves it unchanged.

It is intended to support:

- field discovery
- `marketState` comparison
- security-type comparison
- JSON-to-CSV converter testing
- golden test capture creation

## Install

Requires Python 3.10+.

No third-party packages are required.

## Capture Quote Endpoint

```bash
python tools/capture-utility/yahoo_capture.py --endpoint quote
```

This uses the default reference symbol set:

```text
AAPL, MSFT, SPY, QQQI, NAC, PDI, XNACX, VTSAX, ZT=F, CL=F, ^GSPC, ^DJI, BTC-USD, ETH-USD, EURUSD=X, JPY=X
```

## Capture Custom Symbols

```bash
python tools/capture-utility/yahoo_capture.py --endpoint quote --symbols AAPL,MSFT,SPY
```

## Capture Chart Data

```bash
python tools/capture-utility/yahoo_capture.py --endpoint chart --range 1mo --interval 1d
```

## Capture Search Results

```bash
python tools/capture-utility/yahoo_capture.py --endpoint search --queries AAPL,PIMCO,Nasdaq
```

## Capture Screener Results

```bash
python tools/capture-utility/yahoo_capture.py --endpoint screener --screeners most_actives,day_gainers,day_losers
```

## Output

Files are saved under:

```text
captures/local/<UTC timestamp>/<endpoint>/
```

Each capture includes:

- raw endpoint JSON
- metadata JSON containing URL, endpoint, timestamp, and symbols/query

## Important Parser Rule

Yahoo may reorder fields between captures. Always parse JSON by key name, not field position.
