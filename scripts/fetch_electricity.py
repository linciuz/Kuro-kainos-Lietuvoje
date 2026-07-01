#!/usr/bin/env python3
"""
Lithuanian day-ahead electricity MARKET price -> data/electricity.json.

Shown in the EV section as a market reference (the wholesale spot price behind
what chargers cost), analogous to the Brent oil reference for fuel. Source:
Elering's open Nord Pool API (dashboard.elering.ee) — free, no key, covers the
LT bidding zone. Prices are €/MWh; we also expose ct/kWh (€/MWh ÷ 10) since EV
charging is priced per kWh.
"""

import datetime as dt
import json
import os
import ssl
import sys
import urllib.request

OUT = os.path.join("data", "electricity.json")
UA = "KuroKainosLietuvoje/1.0"
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def getj(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=45, context=_CTX) as r:
        return json.load(r)


def main():
    now = dt.datetime.now(dt.timezone.utc)
    start = now - dt.timedelta(days=7)
    url = ("https://dashboard.elering.ee/api/nps/price"
           f"?start={start.strftime('%Y-%m-%dT%H:00:00.000Z')}"
           f"&end={now.strftime('%Y-%m-%dT%H:59:59.999Z')}")
    rows = getj(url).get("data", {}).get("lt", [])
    series = [(d["timestamp"], d["price"]) for d in rows if d.get("price") is not None]
    if len(series) < 12:
        print(f"[error] only {len(series)} LT price points — aborting")
        sys.exit(2)

    nowts = now.timestamp()
    past = [p for ts, p in series if ts <= nowts] or [series[-1][1]]
    current = past[-1]                                  # current hour's price, €/MWh
    week_avg = sum(p for _, p in series) / len(series)  # 7-day average, €/MWh

    payload = {
        "updated": now.replace(microsecond=0, tzinfo=None).isoformat() + "Z",
        "currency": "EUR",
        "current_eur_mwh": round(current, 2),
        "week_avg_eur_mwh": round(week_avg, 2),
        "current_ct_kwh": round(current / 10, 2),
        "week_avg_ct_kwh": round(week_avg / 10, 2),
    }
    os.makedirs("data", exist_ok=True)
    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[ok] LT electricity: now {payload['current_ct_kwh']} ct/kWh "
          f"({payload['current_eur_mwh']} €/MWh) | week avg {payload['week_avg_ct_kwh']} ct/kWh")


if __name__ == "__main__":
    main()
