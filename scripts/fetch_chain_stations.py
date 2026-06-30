#!/usr/bin/env python3
"""
Chain station-directory collector.

The fuel chains publish their own stations with EXACT coordinates (far more
precise than geocoding LEA's address strings). We pull those directories and
write data/sources/chain_stations.json:

  {"generated": "...", "count": N,
   "stations": [{"network","address","city","lat","lon","source"}, ...]}

merge_chain_coords.py then snaps LEA stations onto these exact coordinates.

Sources confirmed open (no auth):
  Baltic Petroleum  FSCC API     mobileapi.fscc.lt/bp/api/stations  (location.lat/lng)
  Emsi              WP GMaps     emsi.lt/wp-json/wpgmza/v1/markers  (lat/lng)
Add more chains as fetch_* functions; each is independent (failures are skipped).
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

OUT = os.path.join("data", "sources", "chain_stations.json")
UA_API = "okhttp/4.9.2"
UA_WEB = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
LT_BBOX = (53.7, 56.6, 20.8, 27.0)   # lat_min, lat_max, lon_min, lon_max


def http_json(url, ua):
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.load(r)


def http_text(url, ua):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE   # some chain sites present a self-signed chain
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Language": "lt"})
    resp = urllib.request.urlopen(req, timeout=40, context=ctx)
    raw = resp.read()
    if resp.headers.get("Content-Encoding") == "gzip":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", "replace")


def in_lt(lat, lon):
    return LT_BBOX[0] <= lat <= LT_BBOX[1] and LT_BBOX[2] <= lon <= LT_BBOX[3]


def fetch_baltic_petroleum():
    # pageSize=500 returns all ~95 stations (default page is only 50).
    data = http_json("https://mobileapi.fscc.lt/bp/api/stations?pageSize=500", UA_API)["data"]
    out = []
    for s in data:
        loc = s.get("location") or {}
        try:
            lat, lon = float(loc["latitude"]), float(loc["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        if in_lt(lat, lon):
            out.append({"network": "Baltic Petroleum", "address": (s.get("address") or "").strip(),
                        "city": (s.get("city") or "").strip(),
                        "lat": round(lat, 6), "lon": round(lon, 6), "source": "fscc-api"})
    return out


def fetch_emsi():
    data = http_json("https://emsi.lt/wp-json/wpgmza/v1/markers", UA_WEB)
    out, seen = [], set()
    for m in data:
        try:
            lat, lon = float(m.get("lat")), float(m.get("lng"))
        except (TypeError, ValueError):
            continue
        if not in_lt(lat, lon):
            continue
        key = (round(lat, 5), round(lon, 5))     # the feed has duplicate markers
        if key in seen:
            continue
        seen.add(key)
        out.append({"network": "Emsi", "address": (m.get("address") or "").strip(), "city": "",
                    "lat": round(lat, 6), "lon": round(lon, 6), "source": "wpgmza"})
    return out


def fetch_neste():
    # degalines.neste.lt is a Next.js app; station markers are embedded in the
    # flight payload as {"position":{lat,lng},"data":{name,address,hasFuels,...}}.
    html = http_text("https://degalines.neste.lt/", UA_WEB).replace(chr(92), "")
    out = []
    for m in re.finditer(r'"position":\{"lat":(5[3-6]\.\d+),"lng":(2[0-7]\.\d+)\}', html):
        lat, lon = float(m.group(1)), float(m.group(2))
        if not in_lt(lat, lon):
            continue
        win = html[m.end():m.end() + 700]          # the marker's data object
        if '"hasFuels":false' in win:              # skip EV-charging-only points
            continue
        name = re.search(r'"name":"([^"]{2,90})"', win)
        if name and "Elektromobil" in name.group(1):
            continue
        addr = re.search(r'"address":"([^"]{3,160})"', win)
        muni = re.search(r'"municipality":"([^"]{2,60})"', win)
        out.append({"network": "Neste", "address": (addr.group(1).strip() if addr else ""),
                    "city": (muni.group(1).strip() if muni else ""),
                    "lat": round(lat, 6), "lon": round(lon, 6), "source": "neste-locator"})
    return out


_VIADA_RE = re.compile(
    r"position'\s*:\s*new google\.maps\.LatLng\(\s*([0-9.]+)\s*,\s*([0-9.]+)\s*\).*?"
    r"infoWindowHtml\s*=\s*'<table[^>]*>.*?</div></td></tr><tr><td>(.*?)</td></tr>",
    re.DOTALL)


def fetch_viada():
    # Viada inlines each marker as `position: new LatLng(lat,lng)` followed by an
    # infoWindow whose address sits in the <td> after the logo cell.
    html = http_text("https://www.viada.lt/degalines/degaliniu-zemelapis/", UA_WEB)
    out, seen = [], set()
    for m in _VIADA_RE.finditer(html):
        lat, lon = float(m.group(1)), float(m.group(2))
        if not in_lt(lat, lon):
            continue
        addr = re.sub(r"<[^>]+>", " ", m.group(3))
        addr = re.sub(r"\s+", " ", addr).strip()
        key = (round(lat, 5), round(lon, 5))
        if not addr or key in seen:
            continue
        seen.add(key)
        out.append({"network": "Viada", "address": addr, "city": "",
                    "lat": round(lat, 6), "lon": round(lon, 6), "source": "viada-web"})
    return out


def fetch_circle_k():
    # Circle K is a Drupal site; all 99 stations (name + PHYSICAL address + coords)
    # are in the drupal-settings-json blob under ck_sim_search.station_results.
    html = http_text("https://www.circlek.lt/degaliniu-sarasas", UA_WEB)
    m = re.search(r'<script type="application/json" data-drupal-selector="drupal-settings-json">(.*?)</script>',
                  html, re.S)
    if not m:
        return []
    results = (json.loads(m.group(1)).get("ck_sim_search") or {}).get("station_results") or {}
    out = []
    # The per-station keys are LITERAL template strings, not f-string-substituted.
    for st in results.values():
        loc = st.get("/sites/{siteId}/location") or {}
        try:
            lat, lon = float(loc["lat"]), float(loc["lng"])
        except (KeyError, ValueError, TypeError):
            continue
        if not in_lt(lat, lon):
            continue
        phys = (st.get("/sites/{siteId}/addresses") or {}).get("PHYSICAL") or {}
        street, city = (phys.get("street") or "").strip(), (phys.get("city") or "").strip()
        addr = ", ".join(p for p in (street, city) if p)
        out.append({"network": "Circle K", "address": addr, "city": city,
                    "lat": round(lat, 6), "lon": round(lon, 6), "source": "circlek-web"})
    return out


CHAINS = [
    ("Baltic Petroleum", fetch_baltic_petroleum),
    ("Emsi", fetch_emsi),
    ("Neste", fetch_neste),
    ("Circle K", fetch_circle_k),
    ("Viada", fetch_viada),
]


def load_existing_by_network():
    """Previously-committed directory, grouped by network, for per-chain fallback."""
    try:
        data = json.load(open(OUT, encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    by_net = {}
    for s in data.get("stations", []):
        by_net.setdefault(s.get("network"), []).append(s)
    return by_net


def main():
    # Some chain endpoints (e.g. Baltic Petroleum's FSCC API, Viada's site) return
    # 403/empty from datacenter IPs like GitHub Actions runners. So for any chain
    # that fails or returns nothing THIS run, fall back to its previously-committed
    # data instead of dropping it — a per-network outage never loses coordinates.
    existing = load_existing_by_network()
    stations = []
    for name, fn in CHAINS:
        got = []
        try:
            got = fn()
        except Exception as e:
            print(f"[warn] {name} failed: {e}")
        if got:
            print(f"[ok] {name}: {len(got)} stations")
            stations += got
        else:
            prev = existing.get(name, [])
            if prev:
                print(f"[warn] {name}: fetch empty/failed — keeping {len(prev)} previously-committed stations")
                stations += prev
            else:
                print(f"[warn] {name}: no data this run and none committed before")

    # Total-collapse guard: never write a near-empty file (would clobber good data).
    if len(stations) < 50:
        print(f"[error] only {len(stations)} chain stations — aborting WITHOUT writing.")
        sys.exit(2)

    payload = {
        "generated": dt.datetime.now(dt.timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z",
        "count": len(stations),
        "networks": sorted({s["network"] for s in stations}),
        "stations": stations,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[ok] wrote {OUT}: {len(stations)} stations from {len(payload['networks'])} networks")


if __name__ == "__main__":
    main()
