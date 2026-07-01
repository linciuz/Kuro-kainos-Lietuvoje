#!/usr/bin/env python3
"""
EV charging stations in Lithuania.

Two sources, merged:
  * Lithuania's official AFIR National Access Point (Via Lietuva), OCPI 2.3.0
    https://ev.vialietuva.lt/ocpi/2.3.0/locations + /tariffs  — open (CC BY 4.0),
    no key. Gives official locations, connector power, and the €/kWh PRICE (via
    tariff_ids). Each charger keeps its `ocpi_id` so live occupancy can be looked
    up at runtime (the OCPI endpoint blocks browser CORS, so the app reads live
    status through the Cloudflare Worker proxy — see worker/).
  * OpenStreetMap (Overpass) — broader location coverage for chargers not yet on
    the NAP (location + power only, no price/status).

Writes data/sources/ev_chargers.json:
  {generated, count, with_price, ocpi_count, chargers:[{lat,lon,name,operator,
   power_kw,price,sockets,ocpi_id,source}]}
"""

import datetime as dt
import json
import math
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

OUT = os.path.join("data", "sources", "ev_chargers.json")
EV_OVERRIDES = os.path.join("data", "ev_overrides.json")
LT_BBOX = (53.7, 56.45, 20.8, 27.0)   # Lithuania's northernmost point is ~56.45N; 56.6 leaked LV chargers
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
OVERPASS_QUERY = ('[out:json][timeout:90];area["ISO3166-1"="LT"][admin_level=2]->.lt;'
                  'node["amenity"="charging_station"](area.lt);out;')
OCPI_LOCATIONS = "https://ev.vialietuva.lt/ocpi/2.3.0/locations"
OCPI_TARIFFS = "https://ev.vialietuva.lt/ocpi/2.3.0/tariffs"
IGNITIS_MAP = "https://ignitison.lt/zemelapis"
UA_BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
_CTX = ssl.create_default_context()   # verify TLS — ev.vialietuva.lt presents a valid cert


def in_lt(lat, lon):
    return LT_BBOX[0] <= lat <= LT_BBOX[1] and LT_BBOX[2] <= lon <= LT_BBOX[3]


# The official registry lists Elinta Charge's public network under its platform
# company "Stuart Energy, UAB" — relabel to the brand drivers actually recognize.
OPERATOR_ALIASES = {
    "stuart energy, uab": "Elinta Charge",
    "stuart energy": "Elinta Charge",
    "elinta": "Elinta Charge",
}


def norm_operator(op):
    return OPERATOR_ALIASES.get((op or "").strip().lower(), (op or "").strip())


def haversine(a, b, c, d):
    R = 6371.0
    dlat, dlon = math.radians(c - a), math.radians(d - b)
    h = math.sin(dlat / 2) ** 2 + math.cos(math.radians(a)) * math.cos(math.radians(c)) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def getj(url, data=None):
    req = urllib.request.Request(url, data=data,
                                 headers={"User-Agent": "KuroKainosLietuvoje/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=120, context=_CTX) as r:
        return json.load(r)


def getj_paged(url, page=1000):
    """Walk an OCPI list endpoint via offset paging until X-Total-Count is reached
    (or a page comes back empty). The NAP serves only ~94 of 2943 rows by default."""
    offset, out, total = 0, [], None
    while True:
        sep = "&" if "?" in url else "?"
        req = urllib.request.Request(f"{url}{sep}offset={offset}&limit={page}",
                                     headers={"User-Agent": "KuroKainosLietuvoje/1.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=180, context=_CTX) as r:
            if total is None:
                total = int(r.headers.get("X-Total-Count") or 0)
            batch = json.load(r).get("data", [])
        out.extend(batch)
        if not batch or (total and len(out) >= total):
            return out
        offset += len(batch)


# --- Lithuania NAP OCPI (official: locations + power + price) -----------------

def fetch_ocpi():
    tariffs = {t["id"]: t for t in getj(OCPI_TARIFFS).get("data", [])}

    def kwh_price(tariff_id):
        t = tariffs.get(tariff_id)
        if not t:
            return None
        for el in t.get("elements", []):
            for pc in el.get("price_components", []):
                if pc.get("type") == "ENERGY":
                    try:
                        return round(float(pc["price"]), 3)
                    except (KeyError, ValueError, TypeError):
                        pass
        return None

    # Location ids repeat across pages (2943 records -> ~1888 sites); merge by id
    # and union the EVSEs so multi-EVSE sites aren't pinned multiple times.
    by_id = {}
    for loc in getj_paged(OCPI_LOCATIONS):
        co = loc.get("coordinates") or {}
        try:
            lat, lon = float(co["latitude"]), float(co["longitude"])
        except (KeyError, ValueError, TypeError):
            continue
        if not in_lt(lat, lon):
            continue
        oid = str(loc.get("id")) if loc.get("id") is not None else None
        key = oid or f"{round(lat, 6)},{round(lon, 6)}"
        rec = by_id.get(key)
        if rec is None:
            rec = {
                "lat": round(lat, 6), "lon": round(lon, 6),
                "name": (loc.get("name") or (loc.get("operator") or {}).get("name") or "").strip(),
                "operator": norm_operator((loc.get("operator") or {}).get("name") or ""),
                "address": (loc.get("address") or "").strip(),
                "city": (loc.get("city") or "").strip(),
                "power_kw": None, "price": None, "sockets": None,
                "ocpi_id": oid, "source": "vialietuva-ocpi",
                "_powers": [], "_evses": set(),
            }
            by_id[key] = rec
        for e in loc.get("evses", []):
            rec["_evses"].add(e.get("uid") or e.get("evse_id") or len(rec["_evses"]))
            for c in e.get("connectors", []):
                mp = c.get("max_electric_power")
                if mp:
                    rec["_powers"].append(mp / 1000.0)
                for tid in (c.get("tariff_ids") or []):
                    p = kwh_price(tid)
                    if p is not None and rec["price"] is None:
                        rec["price"] = p
    out = []
    for rec in by_id.values():
        rec["power_kw"] = round(max(rec["_powers"]), 1) if rec["_powers"] else None
        rec["sockets"] = len(rec["_evses"]) or None
        del rec["_powers"], rec["_evses"]
        out.append(rec)
    return out


# --- Ignitis ON (largest LT network; per-connector €/kWh on its public map) ----

def fetch_ignitis():
    req = urllib.request.Request(IGNITIS_MAP, headers={"User-Agent": UA_BROWSER, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=120, context=_CTX) as r:
        html = r.read().decode("utf-8", "replace")
    m = re.search(r'data-drupal-selector="drupal-settings-json">(.*?)</script>', html, re.S)
    if not m:
        return []
    locs = ((json.loads(m.group(1)).get("ignitisChargingMap") or {}).get("locations")) or []
    out = []
    for loc in locs:
        ll = loc.get("latlng") or []
        try:
            lat, lon = float(ll[0]), float(ll[1])
        except (IndexError, ValueError, TypeError):
            continue
        addr = (loc.get("address") or "").strip()
        al = addr.lower()
        # Map is Baltic-wide; keep Lithuania only.
        if not (("lietuv" in al) or (in_lt(lat, lon) and not any(x in al for x in ("latvij", "latvia", "eesti", "estonia")))):
            continue
        conns = loc.get("connectors") or []
        prices = [c["price"] for c in conns if isinstance(c.get("price"), (int, float))]
        powers = [c["power"] for c in conns if isinstance(c.get("power"), (int, float))]
        out.append({
            "lat": round(lat, 6), "lon": round(lon, 6),
            "name": (loc.get("label") or "Ignitis ON").strip(),
            "operator": "Ignitis ON",
            "address": addr.split(",")[0].strip() if addr else "",
            "city": "",
            "power_kw": round(max(powers), 1) if powers else None,
            "price": round(min(prices), 3) if prices else None,
            "sockets": len(conns) or None,
            "ocpi_id": None,
            "source": "ignitis",
        })
    return out


# --- OpenStreetMap (broader coverage; location + power) ----------------------

def overpass():
    last = None
    for url in OVERPASS_MIRRORS:
        try:
            return getj(url, data=OVERPASS_QUERY.encode())
        except Exception as e:
            print(f"[warn] overpass mirror failed ({url}): {e}")
            last = e
    raise last


def osm_power_kw(tags):
    best = 0.0
    for k, v in tags.items():
        if "output" in k or k in ("maxpower", "charging_station:output"):
            m = re.search(r"(\d+(?:\.\d+)?)", str(v))
            if m:
                val = float(m.group(1))
                if val >= 1000:
                    val /= 1000.0
                if 1 <= val <= 400:
                    best = max(best, val)
    return round(best, 1) if best else None


def fetch_osm():
    out = []
    for el in overpass().get("elements", []):
        if el.get("type") != "node":
            continue
        t = el.get("tags", {})
        if t.get("access") == "private":
            continue
        operator = t.get("operator") or t.get("network") or t.get("brand") or ""
        if re.search(r"ignitis", operator, re.I):
            operator = "Ignitis ON"
        operator = norm_operator(operator)
        sockets = sum(int(re.search(r"\d+", str(v)).group()) for k, v in t.items()
                      if k.startswith("socket:") and not k.endswith(("output", "voltage", "current"))
                      and re.search(r"\d", str(v))) or None
        street = " ".join(p for p in (t.get("addr:street", ""), t.get("addr:housenumber", "")) if p).strip()
        city = (t.get("addr:city") or "").strip()
        out.append({
            "lat": round(el["lat"], 6), "lon": round(el["lon"], 6),
            "name": (t.get("name") or operator or "Įkrovimo stotelė").strip(),
            "operator": operator.strip(),
            "address": street, "city": city,
            "power_kw": osm_power_kw(t),
            "price": None, "sockets": sockets, "ocpi_id": None, "source": "osm",
        })
    return out


def committed(source):
    """Last-committed chargers for one source, to fall back on if a fetch fails."""
    try:
        return [c for c in json.load(open(OUT, encoding="utf-8")).get("chargers", [])
                if c.get("source") == source]
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def near_any(c, others, km=0.15):
    return any(haversine(c["lat"], c["lon"], o["lat"], o["lon"]) < km for o in others)


def main():
    try:
        ocpi = fetch_ocpi()
        print(f"[ok] OCPI (official, live+price): {len(ocpi)} sites, "
              f"{sum(1 for c in ocpi if c['price'] is not None)} with €/kWh")
    except Exception as e:
        print(f"[warn] OCPI fetch failed: {e}")
        ocpi = []
    if not ocpi:
        ocpi = committed("vialietuva-ocpi")
        if ocpi:
            print(f"[warn] OCPI empty — keeping {len(ocpi)} previously-committed OCPI sites")

    try:
        ignitis = fetch_ignitis()
        print(f"[ok] Ignitis ON: {len(ignitis)} LT sites, "
              f"{sum(1 for c in ignitis if c['price'] is not None)} with €/kWh")
    except Exception as e:
        print(f"[warn] Ignitis fetch failed: {e}")
        ignitis = committed("ignitis")
        if ignitis:
            print(f"[warn] Ignitis empty — keeping {len(ignitis)} previously-committed Ignitis sites")

    try:
        osm = fetch_osm()
        print(f"[ok] OSM: {len(osm)} chargers")
    except Exception as e:
        print(f"[warn] OSM fetch failed: {e}")
        osm = []
    if not osm:
        # Overpass is flaky (often 504s from CI). Keep last-committed OSM locations.
        osm = committed("osm")
        if osm:
            print(f"[warn] OSM empty — keeping {len(osm)} previously-committed OSM chargers")

    # OCPI is authoritative (official price + live status). Ignitis enriches an
    # OCPI site that lacks a price, or is added where the NAP doesn't cover it.
    # OSM fills any remaining gaps (location only).
    chargers = list(ocpi)
    ign_added = 0
    for g in ignitis:
        best, bestd = None, 9e9
        for c in ocpi:
            d = haversine(g["lat"], g["lon"], c["lat"], c["lon"])
            if d < bestd:
                bestd, best = d, c
        if best and bestd < 0.15:
            if best["price"] is None and g["price"] is not None:
                best["price"] = g["price"]
        else:
            chargers.append(g)
            ign_added += 1
    for o in osm:
        if not near_any(o, chargers):
            chargers.append(o)
    print(f"[ok] merged: {len(ocpi)} OCPI + {ign_added} new Ignitis + "
          f"{len(chargers) - len(ocpi) - ign_added} OSM")

    # Manually-verified stations the public feeds miss (e.g. Elinta's own HQ
    # charger). A nearby fuzzy OSM node is dropped in favour of the verified point.
    try:
        overrides = json.load(open(EV_OVERRIDES, encoding="utf-8")).get("stations", [])
    except (FileNotFoundError, json.JSONDecodeError):
        overrides = []
    for m in overrides:
        chargers = [c for c in chargers
                    if not (c.get("source") == "osm" and haversine(m["lat"], m["lon"], c["lat"], c["lon"]) < 0.4)]
        chargers.append({
            "lat": round(m["lat"], 6), "lon": round(m["lon"], 6),
            "name": (m.get("name") or m.get("operator") or "").strip(),
            "operator": norm_operator(m.get("operator") or ""),
            "address": (m.get("address") or "").strip(), "city": (m.get("city") or "").strip(),
            "power_kw": m.get("power_kw"), "price": m.get("price"),
            "sockets": m.get("sockets"), "ocpi_id": None, "source": "manual",
        })
    if overrides:
        print(f"[ok] applied {len(overrides)} manual override station(s)")

    # Don't clobber the committed file with an empty result if sources fail.
    if len(chargers) < 30:
        print(f"[error] only {len(chargers)} chargers collected — aborting WITHOUT "
              f"writing so the last good file survives.")
        sys.exit(2)

    payload = {
        "generated": dt.datetime.now(dt.timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z",
        "source": "Via Lietuva (OCPI/AFIR) + Ignitis ON + OpenStreetMap",
        "count": len(chargers),
        "ocpi_count": len(ocpi),
        "with_price": sum(1 for c in chargers if c["price"] is not None),
        "chargers": chargers,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[ok] wrote {OUT}: {len(chargers)} chargers "
          f"({payload['with_price']} with €/kWh, {len(ocpi)} support live status)")


if __name__ == "__main__":
    main()
