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
LT_BBOX = (53.7, 56.6, 20.8, 27.0)
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
OVERPASS_QUERY = ('[out:json][timeout:90];area["ISO3166-1"="LT"][admin_level=2]->.lt;'
                  'node["amenity"="charging_station"](area.lt);out;')
OCPI_LOCATIONS = "https://ev.vialietuva.lt/ocpi/2.3.0/locations"
OCPI_TARIFFS = "https://ev.vialietuva.lt/ocpi/2.3.0/tariffs"
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def in_lt(lat, lon):
    return LT_BBOX[0] <= lat <= LT_BBOX[1] and LT_BBOX[2] <= lon <= LT_BBOX[3]


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

    out = []
    for loc in getj(OCPI_LOCATIONS).get("data", []):
        co = loc.get("coordinates") or {}
        try:
            lat, lon = float(co["latitude"]), float(co["longitude"])
        except (KeyError, ValueError, TypeError):
            continue
        if not in_lt(lat, lon):
            continue
        powers, price = [], None
        for e in loc.get("evses", []):
            for c in e.get("connectors", []):
                mp = c.get("max_electric_power")
                if mp:
                    powers.append(mp / 1000.0)
                for tid in (c.get("tariff_ids") or []):
                    p = kwh_price(tid)
                    if p is not None and price is None:
                        price = p
        out.append({
            "lat": round(lat, 6), "lon": round(lon, 6),
            "name": (loc.get("name") or (loc.get("operator") or {}).get("name") or "").strip(),
            "operator": ((loc.get("operator") or {}).get("name") or "").strip(),
            "power_kw": round(max(powers), 1) if powers else None,
            "price": price,
            "sockets": len(loc.get("evses", [])) or None,
            "ocpi_id": str(loc.get("id")) if loc.get("id") is not None else None,
            "source": "vialietuva-ocpi",
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
        sockets = sum(int(re.search(r"\d+", str(v)).group()) for k, v in t.items()
                      if k.startswith("socket:") and not k.endswith(("output", "voltage", "current"))
                      and re.search(r"\d", str(v))) or None
        out.append({
            "lat": round(el["lat"], 6), "lon": round(el["lon"], 6),
            "name": (t.get("name") or operator or "Įkrovimo stotelė").strip(),
            "operator": operator.strip(),
            "power_kw": osm_power_kw(t),
            "price": None, "sockets": sockets, "ocpi_id": None, "source": "osm",
        })
    return out


def main():
    try:
        ocpi = fetch_ocpi()
        print(f"[ok] OCPI (official, live+price): {len(ocpi)} locations, "
              f"{sum(1 for c in ocpi if c['price'] is not None)} with €/kWh")
    except Exception as e:
        print(f"[warn] OCPI fetch failed: {e}")
        ocpi = []
    try:
        osm = fetch_osm()
        print(f"[ok] OSM: {len(osm)} chargers")
    except Exception as e:
        print(f"[warn] OSM fetch failed: {e}")
        osm = []

    # OCPI is authoritative (price + live status). Add OSM chargers only where
    # there isn't already an OCPI charger within ~150 m.
    chargers = list(ocpi)
    for o in osm:
        if not any(haversine(o["lat"], o["lon"], c["lat"], c["lon"]) < 0.15 for c in ocpi):
            chargers.append(o)

    # Don't clobber the committed file with an empty result if both sources fail.
    if len(chargers) < 30:
        print(f"[error] only {len(chargers)} chargers collected — aborting WITHOUT "
              f"writing so the last good file survives.")
        sys.exit(2)

    payload = {
        "generated": dt.datetime.now(dt.timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z",
        "source": "Via Lietuva (OCPI/AFIR) + OpenStreetMap",
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
