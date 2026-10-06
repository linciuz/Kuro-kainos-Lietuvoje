#!/usr/bin/env python3
"""
Merge LEA stations with coordinates published by the chains themselves
(data/sources/chain_stations.json from fetch_chain_stations.py).

Address matches require the complete street/house identity and explicit
locality agreement. Ambiguous directory points and different identities
competing for one point are skipped. Coordinates alone never prove identity.

Matched stations get the source lat/lon, approx=False, coord_source="chain".
This records the join/source; it does not prove cadastral or driveway accuracy.

Run after geocode.py:  python scripts/merge_chain_coords.py
"""

import json
import math
import os
import re
import statistics
import sys
from urllib.parse import urlsplit

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

STATIONS = os.path.join("data", "stations.json")
CHAINS = os.path.join("data", "sources", "chain_stations.json")

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


_ADMIN_PART = re.compile(r"\b(?:r|raj|rajonas|m|sav|savivaldybe|sen|seniunija|apskritis)\b")
_HOUSE = re.compile(r"\b\d+[a-z]?(?:-\d+[a-z]?)?\b")
_LABELS = {"g", "gatve", "k", "kaimas", "mst", "miestelis", "lietuva", "lithuania"}
_STREET_TYPES = {"pr": "pr", "prospektas": "pr", "pl": "pl", "plentas": "pl",
                 "al": "al", "aleja": "al", "kl": "kelias", "kel": "kelias"}


def address_identity(address, city="", municipality=""):
    """Parse public address evidence, preserving house suffixes and locality.

    This deliberately declines incomplete or differently spelled identities.
    Verified street/locality/house aliases belong in attributed exact overrides,
    not a similarity score that can silently select another station.
    """
    text = deaccent(address)
    text = re.sub(r"\b(?:lt[- ]*)?\d{5}\b", " ", text)
    # '26 A,' and '26A' describe the same complete house identifier. Only join
    # a single suffix at a component boundary; never consume a street word.
    text = re.sub(r"\b(\d+)\s+([a-z])(?=\s*[,;.]|\s*$)", r"\1\2", text)
    parts = [re.sub(r"[^a-z0-9-]+", " ", p).strip() for p in text.split(",")]
    numbered = [p for p in parts if _HOUSE.search(p)]
    if len(numbered) != 1:
        return None  # no full house, multiple addresses, road/city ambiguity
    street_part = numbered[0]
    houses = tuple(_HOUSE.findall(street_part))
    words = tuple(sorted(_STREET_TYPES.get(t, t) for t in street_part.split()
                         if t not in _LABELS and not _HOUSE.fullmatch(t)))
    if not words:
        return None
    locality = set()
    areas = set()
    # A village with a house ('Kalnujų k. 1') is a complete identity, even
    # though its locality and house occur in the same comma component.
    if {"k", "kaimas"} & set(street_part.split()):
        locality.update(words)
    for part in parts:
        if part == street_part:
            continue
        if _ADMIN_PART.search(part):
            if re.search(r"\b(?:r|raj|rajonas|m|sav|savivaldybe)\b", part):
                areas.update(t for t in part.split() if t not in _LABELS
                             and not _ADMIN_PART.fullmatch(t))
            continue
        locality.update(t for t in part.split() if t not in _LABELS)
    city_words = {t for t in norm(city).split() if t not in _LABELS
                  and not _ADMIN_PART.fullmatch(t)}
    municipality_words = {t for t in norm(municipality).split() if t not in _LABELS
                          and not _ADMIN_PART.fullmatch(t)}
    return {"street": words, "houses": houses, "locality": frozenset(locality),
            "city": frozenset(city_words), "municipality": frozenset(municipality_words),
            "areas": frozenset(areas)}


def address_identity_agrees(station, entry):
    """A positive identity join; neither distance nor shared numbers is proof."""
    left = address_identity(station.get("address", ""), municipality=station.get("municipality", ""))
    right = address_identity(entry.get("address", ""), entry.get("city", ""))
    if not left or not right:
        return False
    if (left["street"], left["houses"]) != (right["street"], right["houses"]):
        return False
    if right["areas"] and left["municipality"] and not (right["areas"] & left["municipality"]):
        return False
    if left["locality"] and right["locality"] and not (left["locality"] & right["locality"]):
        return False
    # A locator's separate city field is evidence, especially Neste's bare
    # street addresses. Do not discard it and join the same street in a city
    # named differently by either the address or its source municipality.
    if right["city"]:
        # A bare source street needs its city to agree with the explicitly
        # named station town; a broad municipality must not defeat that town.
        city_evidence = (left["locality"] if left["locality"] and not right["locality"]
                         else left["locality"] | left["municipality"])
        if not (right["city"] & city_evidence):
            return False
    return bool((left["locality"] & right["locality"]) or
                (right["city"] & (left["locality"] | left["municipality"])))


def match_chain_addresses(stations, entries):
    """Return conservative matches and skipped counts without modifying rows."""
    proposed, stats = [], {"matched": 0, "unmatched": 0, "ambiguous": 0, "collision": 0}
    for index, station in enumerate(stations):
        candidates = [entry for entry in entries
                      if all(isinstance(entry.get(k), (int, float)) and math.isfinite(entry[k])
                             for k in ("lat", "lon"))
                      and 53.7 <= entry["lat"] <= 56.6 and 20.8 <= entry["lon"] <= 27.0
                      and address_identity_agrees(station, entry)]
        points = {(e.get("lat"), e.get("lon")) for e in candidates}
        if not candidates:
            stats["unmatched"] += 1
        elif len(points) != 1:
            stats["ambiguous"] += 1
        else:
            proposed.append((index, candidates[0]))
    by_point = {}
    for index, entry in proposed:
        by_point.setdefault((entry.get("lat"), entry.get("lon")), []).append((index, entry))
    matches = []
    for group in by_point.values():
        identities = {tuple(address_identity(stations[i].get("address", ""),
                                             municipality=stations[i].get("municipality", ""))[k]
                            for k in ("street", "houses", "locality")) for i, _ in group}
        if len(identities) > 1:
            stats["collision"] += len(group)
        else:
            matches.extend(group)  # real co-located aliases with the same identity
    stats["matched"] = len(matches)
    return matches, stats


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
            matches, stats = match_chain_addresses(lea_for, entries)
            for index, entry in matches:
                snap(lea_for[index], entry)
            print(f"[cmp] {dirnet:18s} address-matched {stats['matched']}/{len(lea_for)}; "
                  f"skipped unmatched={stats['unmatched']}, ambiguous={stats['ambiguous']}, "
                  f"collision={stats['collision']}")
        else:
            print(f"[warn] {dirnet}: no address identity evidence; coordinate-only matches skipped")

    portal = apply_portal_coords(stations)
    ov = apply_overrides(stations)

    json.dump(lea, open(STATIONS, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    snapped = sum(1 for s in stations if s.get("coord_source") == "chain")
    fromportal = sum(1 for s in stations if s.get("coord_source") == "lea_portal")
    verified = sum(1 for s in stations if s.get("coord_source") == "verified")
    still_approx = sum(1 for s in stations if s.get("approx"))
    print(f"[ok] {snapped} on chain coords; {fromportal} on official LEA-portal coords ({portal} applied); "
          f"{verified} manually-verified ({ov} applied); {still_approx} still approximate")


def apply_portal_coords(stations):
    """Official operator-registered coordinates from LEA's self-service portal
    (data/sources/lea_portal.json, see fetch_lea_portal.py).

    Applied only to approximate stations, such as municipality centroids from
    the geocoding fallback. Existing non-approximate points are left alone;
    this policy does not certify their physical accuracy. Reviewed exact-key
    overrides still win after this fallback.
    """
    try:
        portal = json.load(open(os.path.join("data", "sources", "lea_portal.json"),
                                encoding="utf-8"))["stations"]
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        print("[warn] no lea_portal.json; skipping official-coordinate merge.")
        return 0
    idx = {}
    for r in portal:
        if r.get("lat") is None:
            continue
        idx.setdefault(norm(r.get("company", "")) + "|" + norm(r.get("address", "")), r)
        idx.setdefault(norm(r.get("address", "")), r)          # address-only fallback

    # Sanity reference: the median coordinate of each municipality, computed
    # ONLY from chain-site/manual-override points, so the portal cannot vouch
    # for itself. This is a coarse distance guard, not independent proof of
    # each reference point's physical accuracy. Same 45 km rule geocode.py uses.
    ref = {}
    for s in stations:
        if s.get("coord_source") in ("chain", "verified") and s.get("lat") is not None:
            ref.setdefault(s.get("municipality"), []).append((s["lat"], s["lon"]))
    med = {m: (statistics.median(p[0] for p in pts), statistics.median(p[1] for p in pts))
           for m, pts in ref.items() if len(pts) >= 4}

    applied = rejected = 0
    for s in stations:
        if not s.get("approx"):
            continue                      # existing non-approximate point
        r = (idx.get(norm(s.get("network", "")) + "|" + norm(s.get("address", "")))
             or idx.get(norm(s.get("address", ""))))
        if not r:
            continue
        c = med.get(s.get("municipality"))
        if c and haversine(c[0], c[1], r["lat"], r["lon"]) > 45:
            print(f"[warn] portal coord for {s.get('network')} / {s.get('address')} is "
                  f"{haversine(c[0], c[1], r['lat'], r['lon']):.0f} km from {s.get('municipality')} "
                  f"— rejected, keeping approximate")
            rejected += 1
            continue
        s["lat"], s["lon"] = r["lat"], r["lon"]
        s["approx"] = False
        s["coord_source"] = "lea_portal"
        applied += 1
    if rejected:
        print(f"[info] {rejected} portal coord(s) rejected by the municipality sanity check")
    return applied


def apply_overrides(stations):
    """Apply verified coordinates or attributed display-area corrections last.
    Display corrections require an exact raw station key; they never rewrite the
    source municipality used by favourites, reports and station identity."""
    path = os.path.join("data", "coord_overrides.json")
    try:
        with open(path, encoding="utf-8") as handle:
            overrides = json.load(handle).get("overrides", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return 0
    applied = 0
    for o in overrides:
        exact_key = o.get("station_key")
        nc = deaccent(o.get("network_contains", ""))
        ac = deaccent(o.get("address_contains", ""))
        mc = deaccent(o.get("municipality_contains", ""))
        if not (exact_key or nc or ac or mc):     # blank matchers would hit EVERY station
            print(f"[warn] skipping coord override with no matchers: {o.get('label', '?')}")
            continue
        display = o.get("display_municipality")
        evidence = o.get("display_municipality_source")
        if display and (not exact_key or not evidence):
            print(f"[warn] skipping display-area override without exact identity and source: "
                  f"{o.get('label', '?')}")
            continue
        display_address = o.get("display_address")
        address_evidence = o.get("display_address_source")
        try:
            address_url = urlsplit(address_evidence) if isinstance(address_evidence, str) else None
        except ValueError:
            address_url = None
        valid_address_source = (address_url is not None and address_url.scheme in ("https", "http")
                                and bool(address_url.netloc))
        if display_address and (not exact_key or not valid_address_source):
            print(f"[warn] skipping display-address override without exact identity and source: "
                  f"{o.get('label', '?')}")
            continue
        for s in stations:
            key = f"{s.get('network') or ''}|{s.get('address') or ''}|{s.get('municipality') or ''}"
            matches = key == exact_key if exact_key else (
                nc in deaccent(s.get("network", "")) and ac in deaccent(s.get("address", ""))
                and mc in deaccent(s.get("municipality", "")))
            if not matches:
                continue
            changed = False
            if o.get("lat") is not None and o.get("lon") is not None:
                s["lat"], s["lon"] = o["lat"], o["lon"]
                s["approx"] = False
                s["coord_source"] = "verified"
                changed = True
            if display:
                s["display_municipality"] = display
                s["display_municipality_source"] = evidence
                changed = True
            if display_address:
                s["display_address"] = display_address
                s["display_address_source"] = address_evidence
                changed = True
            if changed:
                applied += 1
    return applied


if __name__ == "__main__":
    main()
