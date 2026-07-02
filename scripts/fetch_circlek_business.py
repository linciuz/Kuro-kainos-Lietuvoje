#!/usr/bin/env python3
"""
Circle K (Lietuva) BUSINESS fixed price — a genuine SAME-DAY reference.

Unlike Orlen's wholesale (which settles one business day late) and Circle K's
retail page (stamped a day behind), Circle K's business "Fiksuota kaina verslo
klientams" page publishes a TODAY-stamped nationwide list price (VAT-inclusive,
EUR/L). Each table row carries <time datetime="YYYY-MM-DDT..Z"> = the day it is
valid, plus a "previous" column proving the daily refresh. We scrape that
server-rendered table and write data/sources/circlek_business.json.

NB: this is a network-wide list price WITH VAT — a "today" reference, NOT a
bare wholesale figure. And the /export/price/{N} CSV links do NOT work (they
302 into a Drupal batch flow that 404s) — the HTML table is the source of truth.
"""

import datetime as dt
import json
import os
import re
import sys

import requests

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

URL = "https://www.circlek.lt/verslui/degalai/fiksuota-kaina"
OUT = os.path.join("data", "sources", "circlek_business.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"

# "Rūšis" (product) cell text, lowercased -> our fuel key. Matched exact first,
# then longest-prefix, so '95miles+' never collides with '95miles'.
TYPE_TO_FUEL = {
    "95miles": "petrol95",
    "95miles+": "petrol95_plus",
    "98miles+": "petrol98",
    "dmiles": "diesel",
    "dmiles+": "diesel_plus",
    "miles+ xtl": "xtl",
    "lpg": "lpg",
    "adblue": "adblue",
}


def to_float(s):
    return round(float(s.replace(",", ".")), 3)


def strip_tags(t):
    # Drop mobile-only label spans first ("Rūšis:", "Kaina galioja:" ...).
    t = re.sub(r'<span[^>]*uk-hidden@m[^>]*>.*?</span>', ' ', t, flags=re.S)
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', t)).strip()


def map_fuel(typ):
    t = typ.lower().strip()
    for key in sorted(TYPE_TO_FUEL, key=len, reverse=True):
        if t == key or t.startswith(key):
            return TYPE_TO_FUEL[key]
    return None


def parse(html):
    prices = {}
    stated = None
    for row in re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.S):
        # Column order isn't fixed (there's a leading icon cell), so locate
        # cells by content: the one that names a fuel, the first current price,
        # the first valid-date. The "previous" price/date columns come later in
        # the row, so "first" always gives the current values.
        cells = [strip_tags(c) for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.S)]
        fuel = None
        for c in cells:
            fuel = map_fuel(c)
            if fuel:
                break
        if not fuel:
            continue
        val = None
        for c in cells:
            pm = re.search(r'([0-2],\d{2,3})', c)
            if pm and 0.3 < to_float(pm.group(1)) < 3.5:
                val = to_float(pm.group(1))
                break
        if val is None:
            continue
        prices.setdefault(fuel, val)
        if not stated:
            for c in cells:
                dm = re.search(r'(\d{4})-(\d{2})-(\d{2})', c)
                if dm:
                    stated = f"{dm.group(3)}.{dm.group(2)}"   # dd.mm, matching Orlen
                    break
    return prices, stated


def main():
    print(f"[info] fetching {URL}")
    html = requests.get(URL, headers={"User-Agent": UA, "Accept-Language": "lt"}, timeout=40).text
    prices, stated = parse(html)
    print(f"[info] stated date: {stated}")
    print(f"[info] prices: {prices}")
    if "petrol95" not in prices or "diesel" not in prices:
        print("[error] could not parse Circle K business 95/diesel — layout may have changed.")
        sys.exit(2)

    payload = {
        "source": "Circle K (verslo fiksuota kaina)",
        "source_url": URL,
        "scope": "business_fixed_incl_vat",   # nationwide list price WITH VAT — not wholesale
        "unit": "EUR/L su PVM",
        "fetched": dt.date.today().isoformat(),
        "stated_date": stated,
        "prices": prices,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[ok] wrote {OUT}")


if __name__ == "__main__":
    main()
