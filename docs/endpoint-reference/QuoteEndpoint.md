# Quote Endpoint

## URL

```text
https://query1.finance.yahoo.com/v7/finance/quote?symbols=AAPL,MSFT,SPY
```

## Purpose

Returns current quote snapshots for one or more symbols.

## Root Path

```text
quoteResponse.result[]
```

## Dynamic-Schema Notes

The fields returned by Yahoo can vary by:

- security type
- exchange
- `marketState`
- pre/post-market availability
- delayed data status
- whether a field is applicable to the symbol

Field order is not fixed.

## Reference Capture Set

| Type | Symbols |
|---|---|
| Stocks | AAPL, MSFT |
| ETFs | SPY, QQQI |
| Closed-end funds | NAC, PDI |
| Mutual funds | XNACX, VTSAX |
| Futures | ZT=F, CL=F |
| Indices | ^GSPC, ^DJI |
| Crypto | BTC-USD, ETH-USD |
| Forex | EURUSD=X, JPY=X |
