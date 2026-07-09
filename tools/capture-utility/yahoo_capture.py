#!/usr/bin/env python3
"""
Yahoo Finance raw JSON capture utility.

Purpose:
- Capture raw Yahoo Finance JSON responses.
- Save timestamped files.
- Preserve raw Yahoo response for later parser/CSV validation.
- Batch symbols so captures occur at nearly the same time.

Notes:
- Yahoo Finance endpoints are unofficial and can change.
- This script reads JSON by endpoint response, but does not parse or transform it.
- Do not rely on JSON field order.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


DEFAULT_SYMBOLS = [
    "AAPL", "MSFT",
    "SPY", "QQQI",
    "NAC", "PDI",
    "XNACX", "VTSAX",
    "ZT=F", "CL=F",
    "^GSPC", "^DJI",
    "BTC-USD", "ETH-USD",
    "EURUSD=X", "JPY=X",
]


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def request_json(url: str, timeout: int = 30) -> dict:
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 YahooFinanceAPI-Reference Capture Utility",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    with urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw)


def save_json(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=False), encoding="utf-8")


def build_quote_url(symbols: list[str]) -> str:
    return "https://query1.finance.yahoo.com/v7/finance/quote?" + urlencode(
        {"symbols": ",".join(symbols)}
    )


def build_chart_url(symbol: str, range_: str, interval: str) -> str:
    return f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}?" + urlencode(
        {"range": range_, "interval": interval, "events": "div,splits,capitalGains"}
    )


def build_search_url(query: str) -> str:
    return "https://query1.finance.yahoo.com/v1/finance/search?" + urlencode({"q": query})


def build_screener_url(scr_id: str) -> str:
    return "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?" + urlencode(
        {"scrIds": scr_id}
    )


def capture_quote(symbols: list[str], outdir: Path) -> Path:
    url = build_quote_url(symbols)
    data = request_json(url)
    stamp = now_stamp()
    path = outdir / stamp / "quote" / f"quote_batch_{stamp}.json"
    save_json(data, path)

    meta = {
        "captureUtc": stamp,
        "endpoint": "quote",
        "url": url,
        "symbols": symbols,
        "note": "Raw Yahoo Finance quote endpoint capture. Field order is not guaranteed."
    }
    save_json(meta, path.with_name(path.stem + "_metadata.json"))
    return path


def capture_chart(symbols: list[str], outdir: Path, range_: str, interval: str) -> list[Path]:
    paths = []
    stamp = now_stamp()
    for symbol in symbols:
        url = build_chart_url(symbol, range_, interval)
        data = request_json(url)
        safe_symbol = symbol.replace("^", "caret_").replace("=", "_").replace("/", "_")
        path = outdir / stamp / "chart" / f"chart_{safe_symbol}_{range_}_{interval}_{stamp}.json"
        save_json(data, path)
        meta = {
            "captureUtc": stamp,
            "endpoint": "chart",
            "url": url,
            "symbol": symbol,
            "range": range_,
            "interval": interval,
            "note": "Raw Yahoo Finance chart endpoint capture."
        }
        save_json(meta, path.with_name(path.stem + "_metadata.json"))
        paths.append(path)
        time.sleep(0.25)
    return paths


def capture_search(queries: list[str], outdir: Path) -> list[Path]:
    paths = []
    stamp = now_stamp()
    for query in queries:
        url = build_search_url(query)
        data = request_json(url)
        safe_query = query.replace(" ", "_").replace("/", "_")
        path = outdir / stamp / "search" / f"search_{safe_query}_{stamp}.json"
        save_json(data, path)
        meta = {
            "captureUtc": stamp,
            "endpoint": "search",
            "url": url,
            "query": query,
        }
        save_json(meta, path.with_name(path.stem + "_metadata.json"))
        paths.append(path)
        time.sleep(0.25)
    return paths


def capture_screener(scr_ids: list[str], outdir: Path) -> list[Path]:
    paths = []
    stamp = now_stamp()
    for scr_id in scr_ids:
        url = build_screener_url(scr_id)
        data = request_json(url)
        path = outdir / stamp / "screener" / f"screener_{scr_id}_{stamp}.json"
        save_json(data, path)
        meta = {
            "captureUtc": stamp,
            "endpoint": "screener",
            "url": url,
            "scrId": scr_id,
        }
        save_json(meta, path.with_name(path.stem + "_metadata.json"))
        paths.append(path)
        time.sleep(0.25)
    return paths


def parse_symbols(value: str | None) -> list[str]:
    if not value:
        return DEFAULT_SYMBOLS
    return [s.strip() for s in value.split(",") if s.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture raw Yahoo Finance JSON.")
    parser.add_argument("--endpoint", choices=["quote", "chart", "search", "screener"], required=True)
    parser.add_argument("--symbols", help="Comma-separated symbols. Defaults to reference set.")
    parser.add_argument("--queries", help="Comma-separated search queries.")
    parser.add_argument("--screeners", default="most_actives,day_gainers,day_losers", help="Comma-separated screener IDs.")
    parser.add_argument("--range", default="1mo", help="Chart range, e.g. 1d,5d,1mo,6mo,1y")
    parser.add_argument("--interval", default="1d", help="Chart interval, e.g. 1m,5m,1d")
    parser.add_argument("--outdir", default="captures/local", help="Output directory.")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    symbols = parse_symbols(args.symbols)

    try:
        if args.endpoint == "quote":
            path = capture_quote(symbols, outdir)
            print(f"Saved: {path}")
        elif args.endpoint == "chart":
            paths = capture_chart(symbols, outdir, args.range, args.interval)
            for path in paths:
                print(f"Saved: {path}")
        elif args.endpoint == "search":
            queries = [q.strip() for q in (args.queries or "AAPL,PIMCO,Nasdaq").split(",") if q.strip()]
            paths = capture_search(queries, outdir)
            for path in paths:
                print(f"Saved: {path}")
        elif args.endpoint == "screener":
            scr_ids = [s.strip() for s in args.screeners.split(",") if s.strip()]
            paths = capture_screener(scr_ids, outdir)
            for path in paths:
                print(f"Saved: {path}")
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"Capture failed: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
