#!/usr/bin/env python3
"""
Circle K (Lietuva) price fetcher — an INDEPENDENT live source for the
comparison engine.

Circle K publishes its network-lowest retail prices on
https://www.circlek.lt/privatiems/degalu-kainos . The prices are in the static
HTML as Lithuanian comma-decimals, each next to a product image whose filename
identifies the fuel (Miles_95, Miles_D, LPG, ...). We map those to our keys and
write data/sources/circlek.json:

  {"source": "Circle K", "source_url": "...", "fetched": "<date>",
   "stated_date": "Birželio 30", "prices": {"petrol95": 1.689, "diesel": 1.739, "lpg": 0.730, ...}}

This is "network lowest", not per-station, so the comparison engine treats it
as a chain-level cross-check against LEA's Circle K stations.
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

URL = "https://www.circlek.lt/privatiems/degalu-kainos"
OUT = os.path.join("data", "sources", "circlek.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"

# Product-image basename (lowercased) -> our fuel key. We keep the premium
# variants separate; the engine compares the standard grades to LEA.
IMG_TO_FUEL = {
    "miles_95": "petrol95",
    "milesplus_95": "petrol95_plus",
    "milesplus_98": "petrol98",
    "miles_d": "diesel",
    "milesplus_d": "diesel_plus",
    "lpg": "lpg",
    "xtl": "xtl",
    "adblue": "adblue",
}

LT_MONTHS = {  # for the "Kainos atnaujintos <Month> 30-ą dieną" line
    "sausio": 1, "vasario": 2, "kovo": 3, "balandžio": 4, "gegužės": 5,
    "birželio": 6, "liepos": 7, "rugpjūčio": 8, "rugsėjo": 9, "spalio": 10,
    "lapkričio": 11, "gruodžio": 12,
}


def to_float(s):
    return round(float(s.replace(",", ".")), 3)


def parse(html):
    prices = {}
    # Each price is a comma-decimal; attribute it to the nearest product image.
    imgs = [(m.start(), m.group(1).lower())
            for m in re.finditer(r"/([A-Za-z0-9_]+)\.(?:jpg|png|webp)", html)]
    for m in re.finditer(r"\b([012],\d{2,3})\b", html):
        val = to_float(m.group(1))
        if not (0.3 < val < 3.5):
            continue
        if not imgs:
            continue
        _, base = min(imgs, key=lambda x: abs(x[0] - m.start()))
        # Image basenames carry a size suffix (lpg_665x374); match by the
        # longest known prefix so 'milesplus_95' doesn't collide with 'miles'.
        fuel = None
        for key in sorted(IMG_TO_FUEL, key=len, reverse=True):
            if base.startswith(key):
                fuel = IMG_TO_FUEL[key]
                break
        if fuel and fuel not in prices:
            prices[fuel] = val

    stated = None
    md = re.search(r"atnaujintos\s+([A-Za-zĄČĘĖĮŠŲŪŽąčęėįšųūž]+)\s+(\d{1,2})", html)
    if md:
        stated = f"{md.group(1)} {md.group(2)}"
    return prices, stated


def main():
    print(f"[info] fetching {URL}")
    html = requests.get(URL, headers={"User-Agent": UA, "Accept-Language": "lt"}, timeout=40).text
    prices, stated = parse(html)
    print(f"[info] stated date: {stated}")
    print(f"[info] prices: {prices}")
    if "petrol95" not in prices or "diesel" not in prices:
        print("[error] could not parse Circle K 95/diesel — page layout may have changed.")
        sys.exit(2)

    payload = {
        "source": "Circle K",
        "source_url": URL,
        "scope": "network_lowest",     # not per-station
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
