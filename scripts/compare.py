#!/usr/bin/env python3
"""
Comparison engine — cross-checks LEA's official 10:00 prices against
independent live sources and flags where they DISAGREE, so the app can warn
"price may have changed since 10:00".

Inputs:
  data/stations.json        — LEA official baseline (per station)
  data/sources/*.json       — live sources (e.g. circlek.json from fetch_circlek.py)

Output:
  data/discrepancies.json   — { generated, threshold, items: [ {chain, fuel,
                              lea_min, live, delta, direction, source, ...} ] }

A source is matched to a LEA chain by a name pattern. For a "network_lowest"
source (e.g. Circle K's posted lowest), we compare it to the MINIMUM LEA price
for that chain: if the live lowest is below LEA's lowest by more than the
threshold, prices likely dropped since the 10:00 report (and vice-versa).
"""

import datetime as dt
import glob
import json
import os
import sys
from statistics import median

from price_engine import match_saurida_rows, SAURIDA_MAX_GAP, SAURIDA_MAX_AGE_H, _parse_ts

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

LEA = os.path.join("data", "stations.json")
SOURCES_GLOB = os.path.join("data", "sources", "*.json")
OUT = os.path.join("data", "discrepancies.json")
THRESHOLD = 0.015  # €/L; below this we treat prices as unchanged
FUELS = ("petrol95", "diesel", "lpg")

# Source "source" name -> substring that identifies its stations in LEA.
CHAIN_PATTERNS = {
    "Circle K": "circle k",
    "Baltic Petroleum": "baltic petroleum",
    "Viada": "viada",
    "Neste": "neste",
    "Orlen": "orlen baltics",
    "Emsi": "emsi",
}


def lea_networks(stations, pattern):
    return sorted({s["network"] for s in stations
                   if s.get("network") and pattern in s["network"].lower()})


def compare_source(src, stations, now=None):
    """Compare only matching scopes; per-station rows never become chain minima."""
    chain = src.get("source", "?")
    scope, prices = src.get("scope"), src.get("prices")
    if scope not in ("network_lowest", "per_station") or prices is None:
        return []
    fetched = _parse_ts(src.get("fetched"))
    now = now or dt.datetime.now(dt.timezone.utc)
    age = (now - fetched).total_seconds() / 3600 if fetched else None
    if (age is None or age < 0 or age > SAURIDA_MAX_AGE_H
            or src.get("stale_kept") or src.get("generated_stale_kept")):
        print(f"[skip] {chain} comparison has no fresh successful source fetch")
        return []
    nets = lea_networks(stations, CHAIN_PATTERNS.get(chain, chain.lower()))
    if not nets:
        return []
    candidates = []
    if scope == "network_lowest" and isinstance(prices, dict):
        for fuel in FUELS:
            values = [s[fuel] for s in stations if s.get("network") in nets
                      and isinstance(s.get(fuel), (int, float))]
            if values and isinstance(prices.get(fuel), (int, float)):
                candidates.append((fuel, min(values), prices[fuel], None))
    elif scope == "per_station" and chain == "Saurida" and isinstance(prices, list):
        pairs = match_saurida_rows(prices, stations)
        gaps = [abs(p[f] - s[f]) for p, s in pairs for f in FUELS
                if isinstance(p.get(f), (int, float)) and isinstance(s.get(f), (int, float))]
        if gaps and median(gaps) > SAURIDA_MAX_GAP:
            print("::warning::Saurida comparison refused: matched price basis differs too far")
            return []
        for p, s in pairs:
            for fuel in FUELS:
                if isinstance(p.get(fuel), (int, float)) and isinstance(s.get(fuel), (int, float)):
                    candidates.append((fuel, s[fuel], p[fuel], s))
    else:
        print(f"[skip] unsupported price contract for {chain}: {scope}")
        return []
    items = []
    for fuel, official, live, station in candidates:
        delta = round(live - official, 3)
        if abs(delta) < THRESHOLD:
            continue
        item = {"chain": chain, "networks": [station["network"]] if station else nets,
                "fuel": fuel, "lea_min": round(official, 3), "live": live,
                "delta": delta, "direction": "down" if delta < 0 else "up",
                "scope": scope, "source": chain, "source_url": src.get("source_url"),
                "stated_date": src.get("stated_date")}
        if station:
            item["station_key"] = "|".join(station.get(k, "") for k in
                                           ("network", "address", "municipality"))
            item["address"] = station["address"]
        items.append(item)
    return items


def main():
    lea = json.load(open(LEA, encoding="utf-8"))
    stations = lea["stations"]
    items = []
    sources_seen = []

    for path in sorted(glob.glob(SOURCES_GLOB)):
        src = json.load(open(path, encoding="utf-8"))
        # Only compare RETAIL price sources. Skip wholesale (Orlen refinery),
        # station directories, EV chargers, etc. — they have other scopes or no
        # per-fuel "prices" object and must never feed the discrepancy engine.
        if src.get("scope") not in ("network_lowest", "per_station") or "prices" not in src:
            continue
        sources_seen.append(src.get("source", "?"))
        items.extend(compare_source(src, stations))

    payload = {
        "generated": dt.datetime.now(dt.timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z",
        "lea_date": lea.get("updated"),
        "threshold": THRESHOLD,
        "sources": sources_seen,
        "items": items,
    }
    os.makedirs("data", exist_ok=True)
    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[ok] wrote {OUT}: {len(items)} discrepancy flag(s) from {len(sources_seen)} source(s)")


if __name__ == "__main__":
    main()
