#!/usr/bin/env python3
"""
Snap LEA stations onto the EXACT coordinates published by the chains themselves
(data/sources/chain_stations.json from fetch_chain_stations.py).

Two matching modes, chosen automatically per chain:
  * ADDRESS match — when the chain directory carries street addresses
    (Baltic Petroleum, Emsi, Neste): best street + house-number + town overlap.
  * PROXIMITY match — when the directory has coordinates only (Circle K, Viada):
    one-to-one greedy nearest-neighbour between the LEA geocoded point and the
    chain's exact points, within a distance cap. Closest pairs are assigned
    first, so dense city clusters resolve sensibly.

Matched LEA stations get the exact lat/lon, approx=False, coord_source="chain".

Run after geocode.py:  python scripts/merge_chain_coords.py
"""

import json
import math
import os
import re
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

STATIONS = os.path.join("data", "stations.json")
CHAINS = os.path.join("data", "sources", "chain_stations.json")
ADDR_THRESHOLD = 0.45
PROX_KM = 1.5          # max LEA-geocode-to-exact distance for proximity matching

# LEA network substring -> chain-directory network name.
NET_MAP = {
    "baltic petroleum": "Baltic Petroleum",
    "emsi": "Emsi",
    "neste": "Neste",
    "circle k": "Circle K",
    "viada": "Viada",
}


def deaccent(s):
    repl = {"ą": "a", "č": "c", "ę": "e", "ė": "e", "į": "i", "š": "s",
            "ų": "u", "ū": "u", "ž": "z"}
    s = (s or "").lower()
    for a, b in repl.items():
        s = s.replace(a, b)
    return s


def norm(a):
    a = deaccent(a).replace("lietuva", " ")
    a = re.sub(r"\b\d{5}\b", " ", a)
    a = re.sub(r"[^a-z0-9]+", " ", a)
    return re.sub(r"\s+", " ", a).strip()


def tokens(a):
    return set(t for t in norm(a).split() if len(t) >= 2 or t.isdigit())


def numbers(a):
    return set(re.findall(r"\b\d+\b", norm(a)))


def addr_score(a, b):
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    jac = len(ta & tb) / len(ta | tb)
    na, nb = numbers(a), numbers(b)
    if na and nb:
        jac += 0.15 if (na & nb) else -0.30
    return jac


def haversine(a_lat, a_lon, b_lat, b_lon):
    R = 6371.0
    dlat = math.radians(b_lat - a_lat)
    dlon = math.radians(b_lon - a_lon)
    h = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(a_lat)) * math.cos(math.radians(b_lat)) * math.sin(dlon / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(h))


def snap(s, e):
    s["lat"], s["lon"] = e["lat"], e["lon"]
    s["approx"] = False
    s["coord_source"] = "chain"


def main():
    lea = json.load(open(STATIONS, encoding="utf-8"))
    try:
        directory = json.load(open(CHAINS, encoding="utf-8"))["stations"]
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        print("[warn] no chain_stations.json; skipping.")
        return

    by_net = {}
    for c in directory:
        by_net.setdefault(c["network"], []).append(c)

    stations = lea["stations"]
    for dirnet, entries in by_net.items():
        lea_for = [s for s in stations
                   if next((dn for pat, dn in NET_MAP.items()
                            if pat in (s.get("network") or "").lower()), None) == dirnet]
        if not lea_for:
            continue
        has_addr = any(e.get("address") for e in entries)

        if has_addr:
            n = 0
            for s in lea_for:
                best, best_sc = None, 0.0
                for e in entries:
                    sc = addr_score(s.get("address") or "", e.get("address") or "")
                    if sc > best_sc:
                        best_sc, best = sc, e
                if best and best_sc >= ADDR_THRESHOLD:
                    snap(s, best)
                    n += 1
            print(f"[cmp] {dirnet:18s} address-matched {n}/{len(lea_for)}")
        else:
            # one-to-one greedy nearest within PROX_KM
            pairs = []
            for si, s in enumerate(lea_for):
                if s.get("lat") is None:
                    continue
                for ei, e in enumerate(entries):
                    d = haversine(s["lat"], s["lon"], e["lat"], e["lon"])
                    if d <= PROX_KM:
                        pairs.append((d, si, ei))
            pairs.sort()
            used_s, used_e, n = set(), set(), 0
            for d, si, ei in pairs:
                if si in used_s or ei in used_e:
                    continue
                snap(lea_for[si], entries[ei])
                used_s.add(si)
                used_e.add(ei)
                n += 1
            print(f"[cmp] {dirnet:18s} proximity-matched {n}/{len(lea_for)}")

    ov = apply_overrides(stations)

    json.dump(lea, open(STATIONS, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    snapped = sum(1 for s in stations if s.get("coord_source") == "chain")
    verified = sum(1 for s in stations if s.get("coord_source") == "verified")
    print(f"[ok] {snapped} LEA stations on chain coords; {verified} manually-verified overrides ({ov} applied)")


def apply_overrides(stations):
    """Apply manually-verified coordinate corrections last (highest priority).
    Lets a reported wrong location be fixed and survive the daily refresh."""
    path = os.path.join("data", "coord_overrides.json")
    try:
        overrides = json.load(open(path, encoding="utf-8")).get("overrides", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return 0
    applied = 0
    for o in overrides:
        nc = deaccent(o.get("network_contains", ""))
        ac = deaccent(o.get("address_contains", ""))
        mc = deaccent(o.get("municipality_contains", ""))
        for s in stations:
            if (nc in deaccent(s.get("network", "")) and ac in deaccent(s.get("address", ""))
                    and mc in deaccent(s.get("municipality", ""))):
                s["lat"], s["lon"] = o["lat"], o["lon"]
                s["approx"] = False
                s["coord_source"] = "verified"
                applied += 1
    return applied


if __name__ == "__main__":
    main()
