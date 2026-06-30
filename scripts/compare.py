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


def main():
    lea = json.load(open(LEA, encoding="utf-8"))
    stations = lea["stations"]
    items = []
    sources_seen = []

    for path in sorted(glob.glob(SOURCES_GLOB)):
        src = json.load(open(path, encoding="utf-8"))
        chain = src.get("source", "?")
        sources_seen.append(chain)
        pattern = CHAIN_PATTERNS.get(chain, chain.lower())
        nets = lea_networks(stations, pattern)
        if not nets:
            print(f"[warn] no LEA stations match '{chain}' (pattern '{pattern}')")
            continue

        for fuel in FUELS:
            live = src.get("prices", {}).get(fuel)
            if live is None:
                continue
            lea_vals = [s[fuel] for s in stations
                        if s.get("network") in nets and s.get(fuel) is not None]
            if not lea_vals:
                continue
            lea_min = round(min(lea_vals), 3)
            delta = round(live - lea_min, 3)
            changed = abs(delta) >= THRESHOLD
            print(f"[cmp] {chain:18s} {fuel:8s} LEA_min={lea_min} live={live} "
                  f"Δ={delta:+.3f} {'CHANGED' if changed else 'ok'}")
            if changed:
                items.append({
                    "chain": chain,
                    "networks": nets,
                    "fuel": fuel,
                    "lea_min": lea_min,
                    "live": live,
                    "delta": delta,
                    "direction": "down" if delta < 0 else "up",
                    "scope": src.get("scope", "network_lowest"),
                    "source": chain,
                    "source_url": src.get("source_url"),
                    "stated_date": src.get("stated_date"),
                })

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
