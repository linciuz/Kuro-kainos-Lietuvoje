#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Saurida — the ONLY Lithuanian chain (besides Circle K) that publishes real
per-station pump prices, and our only price source that is not LEA.

WHY THIS MATTERS MORE THAN ITS 34 STATIONS SUGGEST
--------------------------------------------------
LEA retired the SharePoint spreadsheet on 2026-07-28 and now exposes exactly
one endpoint, so the app runs on a single upstream with no second opinion.
Surveyed the six biggest chains (52% of all priced stations) on 2026-08-11 for
a public price page; five genuinely do not publish one:

  Viada (133 st.)             no public price page, PDF or JSON anywhere
  Baltic Petroleum (92 st.)   per-user pricing, app-only ("Speciali mano kaina")
  Neste (85 st.)              stated policy NOT to disclose prices online
  Emsi (50 st.)               marketing/locator site, advertises "cheapest" only
  Orlen retail (30 st.)       only "kainos" URL is WHOLESALE, ex-tax per 1000 l

Third-party aggregators (kuro-kainos.lt, degalukaina.lt, kuroradaras.lt, ...)
were deliberately NOT used: degalukaina.lt states outright that it takes its
data from LEA. Ingesting one would launder LEA back in as a fake second
opinion — the pipeline would LOOK redundant while still being single-sourced.

Saurida is the exception, and it immediately proved its worth. Measured
2026-08-11: LEA had Saurida frozen at petrol95 1.650 / diesel 1.850 since
2026-08-06 — SIX DAYS — while Emsi's prices moved three times in the same
window. Saurida's own page said 1.690 / 1.900. The offset was a perfectly
uniform +0.04 / +0.05 across min, median AND max with identical distribution
shape, and LPG matched LEA exactly — the signature of a real price change LEA
had not picked up, not of a parsing error.

So this file is a STALENESS DETECTOR first and a price source second.

  ⚠ DOES NOT PUBLISH PRICES TO THE APP. It writes data/sources/saurida.json
  for verify_sources to compare against LEA. Letting a scraper overwrite LEA
  prices would trade a known-stale risk for an unknown-wrong one, and "never
  publish a wrong price" outranks "never publish a stale one".

THE TRAP THAT WOULD HAVE WRECKED THIS
-------------------------------------
The table has FIVE fuel columns and two of them are decoys:
  "Dyzelinas (B7)"   -> road diesel. THIS is our `diesel`.
  "Dyzelinas (Dž)"   -> flat 1.500 at all 23 stations that list it, ~0.45
                        BELOW road diesel. Agricultural/heating diesel. Mapping
                        this to `diesel` would publish a catastrophically wrong
                        price, so the column map is by EXACT header and anything
                        unrecognised is ignored rather than guessed at.
  "Benzinas A98"     -> not a fuel we track (only 2 stations).
"""

import datetime as dt
import html as _html
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

URL = "https://www.saurida.lt/kuro-kainos-degalinese/"
OUT = os.path.join("data", "sources", "saurida.json")
# Honest tool UA. robots.txt (verified 2026-08-11) disallows only /wp-admin/
# and /xmlrpc.php — this path is explicitly permitted, no login, no WAF.
UA = "fuelis-lt/1.0 (+https://fuelis.lt; price cross-check)"

# EXACT header -> our fuel key. Whitespace is collapsed before matching.
# Anything not listed here is deliberately dropped; see the Dž trap above.
COLUMNS = {
    "Dyzelinas (B7)": "diesel",
    "Benzinas A95 (E5)": "petrol95",
    "Dujos (LPG)": "lpg",
}
# Plausibility bands — a scrape that drifts outside these is a parse failure,
# not a price. Chosen wide enough to survive real market moves.
BANDS = {"petrol95": (1.0, 3.0), "diesel": (1.0, 3.0), "lpg": (0.3, 1.8)}


def _text(fragment):
    return re.sub(r"\s+", " ", _html.unescape(re.sub("<[^>]+>", " ", fragment))).strip()


def fetch():
    r = requests.get(URL, headers={"User-Agent": UA}, timeout=40)
    r.raise_for_status()
    m = re.search(r'<table class="table text-left responsive".*?</table>', r.text, re.S)
    if not m:
        raise RuntimeError("price table not found — page markup changed")
    tbl = m.group(0)

    heads = [_text(h) for h in re.findall(r"<th[^>]*>(.*?)</th>", tbl, re.S)]
    if not heads:
        raise RuntimeError("no table headers")
    mapping = {i: COLUMNS[h] for i, h in enumerate(heads) if h in COLUMNS}
    missing = set(COLUMNS.values()) - set(mapping.values())
    if missing:
        # Fail loudly rather than silently publishing a partial table: a renamed
        # header is exactly how a column shift would sneak the Dž price in.
        raise RuntimeError(f"columns {sorted(missing)} not found; headers were {heads}")

    stations = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", tbl, re.S):
        cells = [_text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) != len(heads) or not cells[0]:
            continue
        rec = {"name": cells[0]}
        for i, fuel in mapping.items():
            mm = re.search(r"\d+[.,]\d+", cells[i])
            if not mm:
                continue
            v = float(mm.group(0).replace(",", "."))
            lo, hi = BANDS[fuel]
            if lo < v < hi:
                rec[fuel] = v
        if len(rec) > 1:
            stations.append(rec)

    if len(stations) < 20:
        raise RuntimeError(f"only {len(stations)} station rows parsed (<20) — markup changed?")
    return stations


def summarise(stations):
    out = {}
    for fuel in COLUMNS.values():
        vals = sorted(s[fuel] for s in stations if fuel in s)
        if vals:
            out[fuel] = {"min": vals[0], "max": vals[-1], "count": len(vals),
                         "median": vals[len(vals) // 2]}
    return out


def main():
    try:
        stations = fetch()
    except Exception as e:
        print(f"::warning::[saurida] fetch failed: {type(e).__name__}: {e} "
              f"— cross-check skipped this run (not fatal; LEA is unaffected).")
        return 0            # never fail the pipeline over a cross-check
    doc = {
        "source": "Saurida",
        "source_url": URL,
        "scope": "per_station",
        "role": "independent cross-check against LEA — NOT published to the app",
        "fetched": dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
                     .isoformat().replace("+00:00", "Z"),
        "stations": len(stations),
        "summary": summarise(stations),
        "prices": stations,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(doc, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    s = doc["summary"]
    print(f"[saurida] {len(stations)} stations — " + " | ".join(
        f"{k} {v['min']:.3f}-{v['max']:.3f}" for k, v in s.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
