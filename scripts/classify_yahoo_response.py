#!/usr/bin/env python3
"""
Classify one saved Yahoo Finance JSON response without calling Yahoo.

Usage:
  python scripts/classify_yahoo_response.py path/to/response.json --endpoint chart

This script is intentionally offline. It helps decide whether a response should be
parsed into financial rows or captured as a diagnostic anomaly.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

def len_at(obj, path):
    cur = obj
    for part in path:
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    if isinstance(cur, list):
        return len(cur)
    if cur is None:
        return None
    return 1

def classify(payload, endpoint):
    if endpoint == "quote":
        qr = payload.get("quoteResponse") or {}
        if qr.get("error") is not None:
            return "YAHOO_ERROR_OBJECT", 0
        result = qr.get("result")
        if result is None:
            return "NULL_RESULT", None
        return ("OK" if len(result) > 0 else "EMPTY_RESULT"), len(result)

    if endpoint == "chart":
        chart = payload.get("chart") or {}
        if chart.get("error") is not None:
            return "YAHOO_ERROR_OBJECT", 0
        result = chart.get("result")
        if result is None:
            return "NULL_RESULT", None
        return ("OK" if len(result) > 0 else "EMPTY_RESULT"), len(result)

    if endpoint == "quoteSummary":
        qs = payload.get("quoteSummary") or {}
        if qs.get("error") is not None:
            return "YAHOO_ERROR_OBJECT", 0
        result = qs.get("result")
        if result is None:
            return "NULL_RESULT", None
        return ("OK" if len(result) > 0 else "EMPTY_RESULT"), len(result)

    if endpoint == "search":
        # Yahoo search is often rooted directly at $.quotes, not finance.search.
        quotes = payload.get("quotes")
        if quotes is None:
            quotes = ((payload.get("finance") or {}).get("search") or {}).get("quotes")
        if quotes is None:
            return "NULL_RESULT", None
        return ("OK" if len(quotes) > 0 else "EMPTY_RESULT"), len(quotes)

    if endpoint == "screener":
        finance = payload.get("finance") or {}
        if finance.get("error") is not None:
            return "YAHOO_ERROR_OBJECT", 0
        result = finance.get("result")
        if result is None:
            return "NULL_RESULT", None
        return ("OK" if len(result) > 0 else "EMPTY_RESULT"), len(result)

    return "SCHEMA_DRIFT", None

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("json_file")
    parser.add_argument("--endpoint", required=True, choices=["quote","chart","quoteSummary","search","screener"])
    args = parser.parse_args()

    path = Path(args.json_file)
    payload = json.loads(path.read_text(encoding="utf-8"))
    state, count = classify(payload, args.endpoint)
    print(json.dumps({"file": str(path), "endpoint": args.endpoint, "result_state": state, "result_count": count}, indent=2))

if __name__ == "__main__":
    main()
