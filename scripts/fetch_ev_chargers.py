#!/usr/bin/env python3
"""
EV charging stations in Lithuania, from OpenStreetMap via the Overpass API
(free, no API key). Writes data/sources/ev_chargers.json:

  {"generated", "count", "chargers": [{lat, lon, name, operator, power_kw, price, sockets}]}

The app shows these as a toggleable ⚡ layer on the map. OSM has good coverage of
operator/power; explicit €/kWh price is only on some stations (tags charge/fee),
so price is best-effort.
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

OUT = os.path.join("data", "sources", "ev_chargers.json")
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
QUERY = ('[out:json][timeout:90];area["ISO3166-1"="LT"][admin_level=2]->.lt;'
         'node["amenity"="charging_station"](area.lt);out;')


def overpass():
    last = None
    for url in OVERPASS_MIRRORS:
        try:
            req = urllib.request.Request(url, data=QUERY.encode(),
                                         headers={"User-Agent": "KuroKainosLietuvoje/1.0"})
            return json.load(urllib.request.urlopen(req, timeout=120))
        except Exception as e:
            print(f"[warn] overpass mirror failed ({url}): {e}")
            last = e
    raise last


def power_kw(tags):
    """Best guess at max charging power (kW)."""
    best = 0.0
    for k, v in tags.items():
        if "output" in k or k in ("maxpower", "charging_station:output"):
            m = re.search(r"(\d+(?:\.\d+)?)", str(v))
            if m:
                best = max(best, float(m.group(1)))
    return round(best, 1) if best else None


def price_eur_kwh(tags):
    """Extract a €/kWh price from free-text charge/fee tags, if present."""
    for k in ("charge", "fee", "fee:conditional", "price"):
        v = str(tags.get(k, ""))
        m = re.search(r"(\d+[.,]\d+)\s*(?:eur|€)?\s*/?\s*kwh", v, re.I)
        if m:
            return round(float(m.group(1).replace(",", ".")), 3)
    return None


def main():
    data = overpass()
    chargers = []
    for el in data.get("elements", []):
        if el.get("type") != "node":
            continue
        t = el.get("tags", {})
        if t.get("access") == "private":          # skip private/home chargers
            continue
        operator = t.get("operator") or t.get("network") or t.get("brand") or ""
        # normalise the many Ignitis spellings
        if re.search(r"ignitis", operator, re.I):
            operator = "Ignitis ON"
        sockets = sum(int(re.search(r"\d+", str(v)).group()) for k, v in t.items()
                      if k.startswith("socket:") and not k.endswith(("output", "voltage", "current"))
                      and re.search(r"\d", str(v))) or None
        chargers.append({
            "lat": round(el["lat"], 6), "lon": round(el["lon"], 6),
            "name": (t.get("name") or operator or "Įkrovimo stotelė").strip(),
            "operator": operator.strip(),
            "power_kw": power_kw(t),
            "price": price_eur_kwh(t),
            "sockets": sockets,
        })

    payload = {
        "generated": dt.datetime.now(dt.timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z",
        "source": "OpenStreetMap (Overpass)",
        "count": len(chargers),
        "with_price": sum(1 for c in chargers if c["price"] is not None),
        "chargers": chargers,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[ok] wrote {OUT}: {len(chargers)} chargers ({payload['with_price']} with €/kWh)")


if __name__ == "__main__":
    main()
