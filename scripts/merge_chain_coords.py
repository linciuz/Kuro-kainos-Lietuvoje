#!/usr/bin/env python3
"""
Snap LEA stations onto the EXACT coordinates published by the chains themselves
(data/sources/chain_stations.json from fetch_chain_stations.py).

For every LEA station whose network matches a chain we have a directory for, we
find the directory entry with the best address match (street + house number +
town) and, if good enough, replace the geocoded lat/lon with the chain's exact
coordinate (and mark approx=False, coord_source="chain"). This fixes the
approximate town-centroid points for those chains.

Run after geocode.py:  python scripts/merge_chain_coords.py
"""

import json
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
THRESHOLD = 0.45

# LEA network substring -> chain-directory network name.
NET_MAP = {
    "baltic petroleum": "Baltic Petroleum",
    "emsi": "Emsi",
}


def deaccent(s):
    repl = {"ą": "a", "č": "c", "ę": "e", "ė": "e", "į": "i", "š": "s",
            "ų": "u", "ū": "u", "ž": "z"}
    s = (s or "").lower()
    for a, b in repl.items():
        s = s.replace(a, b)
    return s


def norm(a):
    a = deaccent(a)
    a = a.replace("lietuva", " ")
    a = re.sub(r"\b\d{5}\b", " ", a)          # postcodes
    a = re.sub(r"[^a-z0-9]+", " ", a)
    return re.sub(r"\s+", " ", a).strip()


def tokens(a):
    return set(t for t in norm(a).split() if len(t) >= 2 or t.isdigit())


def numbers(a):
    return set(re.findall(r"\b\d+\b", norm(a)))


def score(lea_addr, dir_addr):
    ta, tb = tokens(lea_addr), tokens(dir_addr)
    if not ta or not tb:
        return 0.0
    jac = len(ta & tb) / len(ta | tb)
    na, nb = numbers(lea_addr), numbers(dir_addr)
    if na and nb:
        if na & nb:
            jac += 0.15                       # same house number -> strong signal
        else:
            jac -= 0.30                       # different number -> likely different station
    return jac


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

    overridden = 0
    for s in lea["stations"]:
        net = (s.get("network") or "").lower()
        dirnet = next((dn for pat, dn in NET_MAP.items() if pat in net), None)
        if not dirnet or dirnet not in by_net:
            continue
        la = s.get("address") or ""
        best, best_sc = None, 0.0
        for c in by_net[dirnet]:
            sc = score(la, c["address"])
            if sc > best_sc:
                best_sc, best = sc, c
        if best and best_sc >= THRESHOLD:
            if s.get("lat") != best["lat"] or s.get("lon") != best["lon"]:
                overridden += 1
            s["lat"], s["lon"] = best["lat"], best["lon"]
            s["approx"] = False
            s["coord_source"] = "chain"

    json.dump(lea, open(STATIONS, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    snapped = sum(1 for s in lea["stations"] if s.get("coord_source") == "chain")
    print(f"[ok] snapped {snapped} stations to exact chain coords ({overridden} changed)")


if __name__ == "__main__":
    main()
