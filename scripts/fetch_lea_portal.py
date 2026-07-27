#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LEA's NEW self-service portal (degalukainos.ena.lt) -> data/sources/lea_portal.json

Discovered 2026-07-27. LEA launched a portal where operators register stations
and can submit prices themselves. Its Vue SPA reads a public JSON API:

    GET https://api-degalukainos.ena.lt/api/v1/read/prices?per_page=3000
    Authorization: Bearer <token shipped in the site's own JS bundle>

WHAT IT IS AND IS NOT (measured 2026-07-27, 2084 rows / 766 physical stations):
  * NOT a price source yet — only 147 of 2084 station-fuel rows carry a price
    (~7%), and every row is source="automatic": no operator has manually
    submitted anything. The mandatory daily LEA Excel (fetch_prices.py) stays
    the authoritative price feed. Revisit if coverage climbs.
  * IS the best COORDINATE source available: 745 stations with official
    operator-registered lat/lon, matching 94% of our stations by
    company+address. It fixes 122 of our 133 municipality-centroid guesses —
    median correction 11 km, worst 170 km, i.e. pins that were simply wrong
    for "nearest to me" and navigation.
  * Also gives the consumer BRAND ("Circle K") separately from the legal name
    ("UAB Circle K Lietuva"), stable gas_station_id/company_id, and logo URLs.

TOKEN HANDLING: the Bearer token is public — it ships inside the site's own
JavaScript to every visitor, and this endpoint is read-only. We still re-scrape
it from the live bundle on every run instead of hardcoding, so a rotation fixes
itself; the last-known value is only a fallback. If LEA ever restricts this,
the honest move is to ask them for a proper key, not to work around it.

Output: {"generated","source_url","count","priced","stations":[{company, brand,
municipality, address, lat, lon, fuels, prices:{petrol95,diesel,lpg},
gas_station_id, logo_url, submitted_at}]}
"""

import datetime as dt
import json
import os
import re
import sys
import urllib.request

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

SITE = "https://degalukainos.ena.lt/"
API_FALLBACK = "https://api-degalukainos.ena.lt/api/v1"
TOKEN_FALLBACK = "1|KIyMz1NebDWVphuzh7sDy68ANnqoTTABXh1uO4xN067fec57"
OUT = os.path.join("data", "sources", "lea_portal.json")
UA = "fuelis-lt/1.0 (+https://fuelis.lt; Lithuanian fuel price app)"

# portal fuel key -> ours
FUELS = {"benzinas_95": "petrol95", "dyzelinas": "diesel", "snd": "lpg"}
LT_BBOX = (53.8, 56.5, 20.9, 26.9)      # lat_min, lat_max, lon_min, lon_max


def get(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def discover_credentials():
    """Re-read the API base + Bearer token from the live SPA bundle, so a token
    rotation or asset rehash heals itself. Falls back to the last-known pair."""
    try:
        html = get(SITE)
        m = re.search(r'src="\.?(/assets/index-[A-Za-z0-9_-]+\.js)"', html)
        if not m:
            raise ValueError("entry bundle not found in index.html")
        entry = get(SITE.rstrip("/") + m.group(1), {"Referer": SITE})
        # The app chunk is referenced RELATIVE to /assets/ (e.g. "./FuelPriceSiteApp-B0VY_iNc.js"),
        # so match the bare filename and rebuild the absolute path.
        m2 = re.search(r'(FuelPriceSiteApp-[A-Za-z0-9_-]+\.js)', entry)
        chunk = get(SITE.rstrip("/") + "/assets/" + m2.group(1), {"Referer": SITE}) if m2 else entry
        base = re.search(r'apiBase:"([^"]+)"', chunk)
        tok = re.search(r'token:"([^"]+)"', chunk)
        if base and tok:
            print(f"[ok] credentials scraped from the live bundle ({base.group(1)})")
            return base.group(1), tok.group(1)
        raise ValueError("apiBase/token not present in the chunk")
    except Exception as e:
        print(f"[warn] could not scrape credentials ({type(e).__name__}: {e}) — using last known")
        return API_FALLBACK, TOKEN_FALLBACK


def sane_coord(lat, lon):
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if LT_BBOX[0] <= lat <= LT_BBOX[1] and LT_BBOX[2] <= lon <= LT_BBOX[3]:
        return round(lat, 6), round(lon, 6)
    return None


def main():
    base, token = discover_credentials()
    url = f"{base}/read/prices?per_page=3000"
    raw = get(url, {"Authorization": "Bearer " + token, "Accept": "application/json",
                    "Referer": SITE})
    payload = json.loads(raw)
    rows = payload.get("data") or []
    if not rows:
        print("[error] portal returned 0 rows — aborting WITHOUT writing so the last good file survives.")
        sys.exit(2)

    # rows are station x fuel; fold them into one record per physical station
    st = {}
    for r in rows:
        if not r.get("is_active", True):
            continue
        key = r.get("gas_station_id") or (r.get("company_name"), r.get("address"))
        s = st.setdefault(key, {
            "company": (r.get("company_name") or "").strip(),
            "brand": (r.get("gas_station_name") or "").strip(),
            "municipality": (r.get("municipality") or "").strip(),
            "address": (r.get("address") or "").strip(),
            "lat": None, "lon": None, "fuels": [], "prices": {},
            "gas_station_id": r.get("gas_station_id"),
            "logo_url": r.get("logo_url"), "submitted_at": r.get("submitted_at"),
        })
        xy = sane_coord(r.get("latitude"), r.get("longitude"))
        if xy and s["lat"] is None:
            s["lat"], s["lon"] = xy
        f = FUELS.get(r.get("fuel_type"))
        if f:
            if f not in s["fuels"]:
                s["fuels"].append(f)
            p = r.get("price")
            if p is not None:
                try:
                    v = round(float(p), 3)
                    if 0.3 < v < 3.5:
                        s["prices"][f] = v
                except (TypeError, ValueError):
                    pass

    stations = list(st.values())
    with_xy = sum(1 for s in stations if s["lat"] is not None)
    priced = sum(1 for s in stations if s["prices"])
    # Collapse guard: never clobber a good file with a degraded fetch.
    if with_xy < 300:
        print(f"[error] only {with_xy} stations with usable coords — aborting WITHOUT writing.")
        sys.exit(3)

    out = {
        "generated": dt.datetime.now(dt.timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z",
        "source": "LEA degalukainos.ena.lt (operator self-service portal)",
        "source_url": SITE,
        "api_last_updated": payload.get("last_updated"),
        "count": len(stations),
        "with_coords": with_xy,
        "priced": priced,      # watch this: if it climbs, the portal becomes a real price feed
        "stations": sorted(stations, key=lambda s: (s["municipality"], s["company"], s["address"])),
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[ok] wrote {OUT}: {len(stations)} stations, {with_xy} with official coords, "
          f"{priced} carrying a self-reported price ({100 * priced / len(stations):.0f}%)")


if __name__ == "__main__":
    main()
