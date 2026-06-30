#!/usr/bin/env python3
"""
Geocode LEA fuel stations to lat/lon so the app can do "nearest to me" + map POIs.

The LEA data has no coordinates, so we geocode each station's address with the
free OpenStreetMap Nominatim service. Results are cached in data/geocode_cache.json
keyed by "address | municipality", so:
  * the slow full geocode happens only once;
  * the daily GitHub Action only geocodes genuinely new stations;
  * fetch_prices.py can rewrite stations.json and we just re-apply cached coords.

Run after fetch_prices.py:  python scripts/geocode.py

Nominatim usage policy: max 1 request/second, real User-Agent. We honour both.
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

STATIONS = os.path.join("data", "stations.json")
CACHE = os.path.join("data", "geocode_cache.json")
UA = "KuroKainosLietuvoje/1.0 (+https://github.com/linciuz/Kuro-kainos-Lietuvoje)"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
SLEEP = 1.1  # seconds between live requests (Nominatim policy: <= 1 req/s)


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, obj, indent=2):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)


STREET_RE = re.compile(
    r"\b(g\.|gatvė|gatve|pl\.|plentas|al\.|alėja|aleja|pr\.|prospekt|kel\.|aplinkkel|skg\.)", re.I)


def norm(s):
    """Normalise whitespace incl. non-breaking spaces that LEA uses."""
    return re.sub(r"\s+", " ", (s or "").replace("\xa0", " ").replace(" ", " ")).strip(" ,")


def clean_muni(m):
    """'Utenos r. sav.' -> 'Utenos', 'Vilniaus m. sav.' -> 'Vilniaus'."""
    m = norm(m)
    m = re.sub(r"\b(r\.|m\.|raj\.|rajono|miesto)\b", " ", m)
    m = m.replace("sav.", " ").replace("savivaldybė", " ").replace("savivaldybe", " ")
    return re.sub(r"\s+", " ", m).strip(" ,")


def town_segment(addr):
    """Best guess at the town/village part: a comma-segment with no street token.
    Returns '' if every segment has a street token (avoids fuzzy mis-matches)."""
    segs = [norm(s) for s in addr.split(",") if norm(s)]
    towns = [s for s in segs if not STREET_RE.search(s)]
    return towns[0] if towns else ""


def street_first_queries(addr):
    """Build street-first query variants for an address that may be town-first,
    use non-breaking spaces, or have a letter-suffixed house number."""
    a = norm(addr)
    if not a:
        return []
    variants = [a]
    segs = [norm(s) for s in a.split(",") if norm(s)]
    if len(segs) >= 2:
        variants.append(", ".join(reversed(segs)))           # "Town, Street" -> "Street, Town"
    elif len(segs) == 1 and STREET_RE.search(a):
        # No comma, likely "Town Street g. N" -> move leading town word(s) to end.
        w = a.split()
        if len(w) >= 3:
            variants.append(f"{' '.join(w[1:])}, {w[0]}")        # 1-word town
            variants.append(f"{' '.join(w[2:])}, {' '.join(w[:2])}")  # 2-word town
    a2 = re.sub(r"(\d+)[A-Za-z](?=\b)", r"\1", a)             # "33B" -> "33"
    if a2 != a:
        variants.append(a2)
        s2 = [norm(s) for s in a2.split(",") if norm(s)]
        if len(s2) >= 2:
            variants.append(", ".join(reversed(s2)))
    # de-dup, keep order
    seen, out = set(), []
    for v in variants:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def nominatim(q):
    """Return (lat, lon) for a free-form query, or None. Raises on network error."""
    url = NOMINATIM + "?" + urllib.parse.urlencode(
        {"q": q, "format": "json", "limit": 1, "countrycodes": "lt"})
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "lt"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    if data:
        return round(float(data[0]["lat"]), 6), round(float(data[0]["lon"]), 6)
    return None


def geocode_station(addr, muni):
    """Street-level first (several query shapes), then town locality, then
    municipality centroid. The last two are flagged approx=True."""
    cm = clean_muni(muni)

    # 1) Exact: street-first variants, optionally pinned to the municipality.
    for v in street_first_queries(addr):
        for q in ([f"{v}, Lietuva"] + ([f"{v}, {cm}, Lietuva"] if cm else [])):
            res = nominatim(q)
            time.sleep(SLEEP)
            if res:
                return {"lat": res[0], "lon": res[1], "approx": False}

    # 2) Approx: the town/village embedded in the address, PINNED to the
    # municipality so an ambiguous name can't match the wrong city.
    town = town_segment(addr)
    if town and cm and town.lower() != cm.lower():
        res = nominatim(f"{town}, {cm}, Lietuva")
        time.sleep(SLEEP)
        if res:
            return {"lat": res[0], "lon": res[1], "approx": True}

    # 3) Approx: municipality centroid.
    if cm:
        res = nominatim(f"{cm}, Lietuva")
        time.sleep(SLEEP)
        if res:
            return {"lat": res[0], "lon": res[1], "approx": True}
    return None


def main():
    data = load_json(STATIONS, None)
    if not data or "stations" not in data:
        print("[error] data/stations.json missing or invalid; run fetch_prices.py first.")
        sys.exit(1)
    cache = load_json(CACHE, {})
    stations = data["stations"]
    new_calls = applied = 0

    # Optional: forget approx / failed entries so they're retried with the
    # current (improved) query logic. Exact hits are kept.
    if "--retry-approx" in sys.argv:
        before = len(cache)
        cache = {k: v for k, v in cache.items() if v and not v.get("approx")}
        print(f"[info] retry-approx: dropped {before - len(cache)} approx/failed cache entries")

    for i, s in enumerate(stations):
        addr = (s.get("address") or "").strip()
        muni = (s.get("municipality") or "").strip()
        key = f"{addr} | {muni}"

        if key not in cache:
            try:
                cache[key] = geocode_station(addr, muni)  # dict or None (cache the miss too)
            except Exception as e:
                print(f"[warn] geocode failed for '{key}': {e}")
                continue  # transient: don't cache, retry next run
            new_calls += 1
            if new_calls % 20 == 0:
                save_json(CACHE, cache, indent=0)
                got = sum(1 for v in cache.values() if v)
                print(f"  ...{i + 1}/{len(stations)} processed, {got} located")

        ll = cache.get(key)
        if ll:
            s["lat"], s["lon"], s["approx"] = ll["lat"], ll["lon"], ll.get("approx", False)
            applied += 1

    save_json(CACHE, cache, indent=0)
    save_json(STATIONS, data, indent=2)
    located = sum(1 for s in stations if s.get("lat") is not None)
    exact = sum(1 for s in stations if s.get("lat") is not None and not s.get("approx"))
    print(f"[ok] new geocodes: {new_calls}; stations with coords: {located}/{len(stations)} "
          f"({exact} exact, {located - exact} approx municipality centroid)")


if __name__ == "__main__":
    main()
