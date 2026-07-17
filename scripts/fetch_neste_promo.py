#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Neste "Nuolaidadienis" (Wednesday discount day) fetcher.

neste.lt/lt/nuolaidadienis publishes the Wednesday discount as PLAIN TEXT with
the specific date (verified 2026-07-08):
    "Tik šį trečiadienį, liepos 8 d. - 7 ct nuolaida su NESTE programėle ir
     nuolaidų kortele visiems degalams"
That is Neste's own officially-announced figure, so we extract it under strict
validation (explicit day-month date + a sane 1-30 ct band + the word
'trečiadien'). The app applies it ONLY on the stated date as the Neste loyalty
discount (with the app/card — consistent with the opt-in discounts feature),
reverting to the user's configured cents any other day. If the page changes or
the promo pauses, parsing yields nothing and the file just omits the promo.

OUTPUT  data/sources/neste_promo.json
  {"generated","source_url","valid_date":"YYYY-MM-DD","cents":7.0}   # promo present
  {"generated","source_url"}                                          # no promo
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

URL = "https://www.neste.lt/lt/nuolaidadienis"
OUT = os.path.join("data", "sources", "neste_promo.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"

# Lithuanian month names (genitive, as used in dates) -> month number.
LT_MONTHS = {
    "sausio": 1, "vasario": 2, "kovo": 3, "balandžio": 4, "gegužės": 5,
    "birželio": 6, "liepos": 7, "rugpjūčio": 8, "rugsėjo": 9, "spalio": 10,
    "lapkričio": 11, "gruodžio": 12,
}


def fetch(url):
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "lt"})
    resp = urllib.request.urlopen(req, timeout=40, context=ctx)
    raw = resp.read()
    if resp.headers.get("Content-Encoding") == "gzip":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", "replace")


def parse(html):
    """Return (valid_date_iso, cents) or (None, None)."""
    txt = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = re.sub(r"\s+", " ", txt)

    # "šį trečiadienį, liepos 8 d. - 7 ct nuolaida"
    m = re.search(
        r"trečiadien\w*[,\s]+(\w+)\s+(\d{1,2})\s*d\.?\s*[-–—]?\s*(\d{1,2}(?:[.,]\d)?)\s*ct\s*nuolaid",
        txt, re.I)
    if not m:
        print("[info] no dated Wednesday discount found on the page")
        return None, None
    month = LT_MONTHS.get(m.group(1).lower())
    day = int(m.group(2))
    cents = float(m.group(3).replace(",", "."))
    if not month or not (1 <= day <= 31) or not (1 <= cents <= 30):
        print(f"[warn] parsed values out of band: month={m.group(1)} day={day} cents={cents}")
        return None, None

    # Year: the promo is always same-week; handle the Dec/Jan wrap.
    today = dt.date.today()
    year = today.year
    if month == 1 and today.month == 12:
        year += 1
    elif month == 12 and today.month == 1:
        year -= 1
    valid = dt.date(year, month, day).isoformat()
    return valid, cents


def load_existing():
    try:
        return json.load(open(OUT, encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def main():
    print(f"[info] fetching {URL}")
    payload = {
        "generated": dt.datetime.now(dt.timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z",
        "source_url": URL,
    }
    try:
        html = fetch(URL)
        valid, cents = parse(html)
        if valid and cents:
            payload["valid_date"] = valid
            payload["cents"] = cents
            print(f"[ok] Neste nuolaidadienis: −{cents} ct/L on {valid}")
    except Exception as e:
        # Carry-forward (the Viada pattern): a fetch hiccup must not clobber a
        # promo that is still valid today+, and must NOT re-stamp `generated` —
        # a fresh stamp on a failure run blinds verify_sources' rot rule.
        prev = load_existing()
        if prev and prev.get("valid_date", "") >= dt.date.today().isoformat():
            print(f"[warn] fetch failed: {type(e).__name__}: {e} — keeping previous file "
                  f"(valid {prev.get('valid_date')}), stale-keep marked")
            prev["generated_stale_kept"] = True
            json.dump(prev, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            return
        if prev:
            print(f"[warn] fetch failed: {type(e).__name__}: {e} — keeping previous file untouched "
                  f"(old generated stamp stays visible to the staleness gate)")
            return
        print(f"[warn] fetch failed: {type(e).__name__}: {e} — nothing committed before; writing promo-less file")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[ok] wrote {OUT}")


if __name__ == "__main__":
    main()
