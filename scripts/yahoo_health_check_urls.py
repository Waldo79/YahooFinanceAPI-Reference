#!/usr/bin/env python3
"""
Optional live Yahoo Finance health check.

This script calls the URLs in data/endpoint_health_urls_v0_3_1.csv and records
HTTP status, content type, and a compact result_state. It is not run by tests
because Yahoo can throttle or block automated requests.

Usage:
  python scripts/yahoo_health_check_urls.py
"""
from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "data" / "endpoint_health_urls_v0_3_1.csv"
OUTPUT = ROOT / "data" / "endpoint_health_results_latest.csv"

def classify_http(status):
    if status == 429:
        return "HTTP_ERROR_429"
    if status in (401, 403):
        return "HTTP_ERROR_401_403"
    if status < 200 or status >= 300:
        return "HTTP_ERROR_OTHER"
    return "HTTP_OK"

def main():
    rows = list(csv.DictReader(INPUT.open(newline="", encoding="utf-8")))
    out_fields = list(rows[0].keys()) + ["http_status_code", "response_content_type", "result_state", "response_body_prefix"]
    out_rows = []

    for row in rows:
        req = Request(row["url"], headers={
            "User-Agent": "Mozilla/5.0 YahooFinanceAPI-Reference health-check",
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
        })
        status = None
        content_type = ""
        prefix = ""
        try:
            with urlopen(req, timeout=20) as resp:
                status = resp.status
                content_type = resp.headers.get("Content-Type", "")
                body = resp.read(1000)
                prefix = body[:160].decode("utf-8", errors="replace")
        except HTTPError as e:
            status = e.code
            content_type = e.headers.get("Content-Type", "") if e.headers else ""
            try:
                prefix = e.read(160).decode("utf-8", errors="replace")
            except Exception:
                prefix = ""
        except URLError as e:
            prefix = str(e)
            status = 0

        result_state = classify_http(status or 0)
        out = dict(row)
        out.update({
            "http_status_code": status,
            "response_content_type": content_type,
            "result_state": result_state,
            "response_body_prefix": prefix.replace("\n", " ")[:160],
        })
        out_rows.append(out)
        time.sleep(1.5)

    with OUTPUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields)
        writer.writeheader()
        writer.writerows(out_rows)
    print(f"Wrote {OUTPUT}")

if __name__ == "__main__":
    main()
