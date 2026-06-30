#!/usr/bin/env python3
"""
ORLEN Lietuva WHOLESALE (refinery) prices — an always-legit reference.

ORLEN's Mažeikiai refinery publishes daily wholesale product prices at
orlenlietuva.lt (Didmeninė prekyba). These are NOT pump prices — they exclude
the retail margin — but they are the official base price, so the app shows them
as a labelled reference ("Orlen didmeninė kaina, ne degalinės kaina").

The page embeds a Highcharts iframe; each chart's `renderTo: 'container<Product>'`
identifies the product and its `data:[...]` array's last point is the latest
EUR/l (@15°C) value. (ORLEN mislabels every series name as 'A95'; the container
is the real product, so we key off that.)

Writes data/sources/orlen_wholesale.json.
"""

import datetime as dt
import gzip
import json
import os
import re
import ssl
import sys
import urllib.request

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

IFRAME = "https://www.orlenlietuva.lt/en/wholesale/_layouts/f2hCharts/default_lt_M.aspx"
OUT = os.path.join("data", "sources", "orlen_wholesale.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"

# Chart container name -> our fuel key.
CONTAINER_TO_FUEL = {
    "A95": "petrol95",
    "Diesel Fuel": "diesel",
    "Diesel Agriculture": "diesel_agri",   # red diesel, low excise (not a pump fuel)
}


def fetch(url):
    # orlenlietuva.lt presents a self-signed cert chain in some networks; this is
    # public read-only data, so verification is intentionally relaxed.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "lt"})
    resp = urllib.request.urlopen(req, timeout=40, context=ctx)
    raw = resp.read()
    if resp.headers.get("Content-Encoding") == "gzip":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", "replace")


def main():
    html = fetch(IFRAME)
    prices = {}
    for m in re.finditer(r"renderTo:\s*'container([^']+)'(.*?)data:\s*\[([0-9.,\s]+)\]", html, re.S):
        container = m.group(1).strip()
        fuel = CONTAINER_TO_FUEL.get(container)
        if not fuel:
            continue
        data = [x for x in re.split(r"[,\s]+", m.group(3)) if x]
        if data:
            prices[fuel] = round(float(data[-1]), 3)

    md = re.search(r"categories:\[([^\]]+)\]", html)
    stated = None
    if md:
        dates = [d.strip().strip("'") for d in md.group(1).split(",") if d.strip()]
        stated = dates[-1] if dates else None      # e.g. "29.06"

    print(f"[info] Orlen wholesale (latest {stated}): {prices}")
    if "petrol95" not in prices and "diesel" not in prices:
        print("[error] could not parse Orlen wholesale charts — layout may have changed.")
        sys.exit(2)

    payload = {
        "source": "ORLEN Lietuva (didmeninė)",
        "source_url": "https://www.orlenlietuva.lt/LT/Wholesale/Puslapiai/Produktu-kainos.aspx",
        "scope": "wholesale",          # refinery price, excludes retail margin — NOT a pump price
        "unit": "EUR/l @15°C",
        "fetched": dt.date.today().isoformat(),
        "stated_date": stated,
        "prices": prices,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[ok] wrote {OUT}")


if __name__ == "__main__":
    main()
