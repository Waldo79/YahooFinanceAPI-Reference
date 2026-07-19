# Seven-Endpoint Live-Verification Validation Report

- Run ID: `2026-07-19T01-35-54.972Z_seven-endpoint-verification`
- Started: `2026-07-19T01:35:54.972Z`
- Completed: `2026-07-19T01:35:56.445Z`
- Total run duration: `1.473 seconds`
- ZIP integrity: `PASS`
- Manifest request count: `7`
- All expected top-level objects found: `True`
- Default inter-request pause: `0 ms`
- Session strategy: `basic-query1`
- Session refreshes: `0`
- Sensitive values persisted: `False`

## File and metadata validation

| Endpoint | HTTP | Bytes | SHA-256 | JSON | Expected object | Attempts | Manifest/metadata |
|---|---:|---:|---|---|---|---:|---|
| `quote` | 200 | 2545 | `c8ef321ef24d1bdb2ad60801f06c0da474fc135167ff0ebc2188d1cc18f58c6b` | VALID_JSON | PASS | 1 | PASS |
| `chart` | 200 | 1592 | `473a1d0c462afc25b71c8fddf07f11b66fa42721f69987bc9193c51a980bd8c5` | VALID_JSON | PASS | 1 | PASS |
| `quote-summary` | 200 | 2111 | `8d2f2341f7abbf2c378fb1dafd7c903d087d2235f2ef441d83d1bf00313680b0` | VALID_JSON | PASS | 1 | PASS |
| `search` | 200 | 1946 | `1ced6fd7a07f3a994644d9dec7c3adc66cd0a94ba09b1ec070c9a50b2575af6a` | VALID_JSON | PASS | 1 | PASS |
| `screener` | 200 | 16079 | `5f89b66c0b9d6e21a276e89049e59ec5f931dcb7573dc2409a44bfce20ce7f51` | VALID_JSON | PASS | 1 | PASS |
| `fundamentals-timeseries` | 200 | 865 | `b33a28cdd9d1659bf6d9157151cb57f8fc2b76878f31b9c7306c4decd92d277d` | VALID_JSON | PASS | 1 | PASS |
| `options` | 200 | 30780 | `5c960a8d27d9d53592c165670a516a238d8f2b9f5d431e0edf69587e6f589716` | VALID_JSON | PASS | 1 | PASS |

Every raw response byte count and SHA-256 matched its metadata sidecar. Every manifest request entry matched its corresponding metadata file.

## Initial response-shape observations

- **quote:** 1 result; returned symbol AAPL; 87 fields in the first result.
- **chart:** 1 result; symbol AAPL; 5 timestamps.
- **quote-summary:** 1 result; returned modules: summaryDetail, price.
- **search:** 6 quote records returned despite quotesCount=5; symbols/identities: ['AAPL', 'AAPL.SW', 'AAPLC.BA', 'AAPL19.BK', 'AAPL260717C00280000', None].
- **screener:** 5 quotes returned; total reported by Yahoo: 101.
- **fundamentals-timeseries:** 1 result block; keys: ['meta', 'timestamp', 'quarterlyMarketCap'].
- **options:** 23 expiration dates; 48 calls; 39 puts.

## Sensitive-session scan

PASS — no unredacted crumb query parameter, Cookie/Set-Cookie header, Authorization header, or Yahoo A3/T/Y cookie value was found in the archive.

## Conclusion

The first seven-endpoint live-verification run is internally consistent and suitable as baseline evidence. All seven requests succeeded on the first attempt with HTTP 200, valid JSON, and the expected top-level response object. No authentication refresh was required.
