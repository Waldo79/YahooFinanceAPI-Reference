# Validation Rules

| Rule | Expression |
|---|---|
| Price change | `regularMarketChange = regularMarketPrice - regularMarketPreviousClose` |
| Percent change | `regularMarketChangePercent ≈ regularMarketChange / regularMarketPreviousClose × 100` |
| Market cap | `marketCap ≈ regularMarketPrice × sharesOutstanding` |
| Daily range | `regularMarketDayLow ≤ regularMarketPrice ≤ regularMarketDayHigh` when applicable |
