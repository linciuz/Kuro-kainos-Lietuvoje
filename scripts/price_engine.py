#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Multi-source price engine — always serve the FRESHEST price LEA has anywhere.

WHY THIS EXISTS
---------------
LEA publishes the same prices through several channels that do NOT update in
lockstep (owner's observation, 2026-07-29, confirmed by measurement):

  * PORTAL  degalukainos.ena.lt  — operators self-service. Measured 111 distinct
    submitted_at values in one day, including stations updating at 12:39 in the
    AFTERNOON. This is a genuine intraday feed, not a daily dump.
  * SPREADSHEET  the daily SharePoint Excel — the official snapshot. Sometimes
    carries a new day before the other channels show it.
  * POWER BI  the /dk-irankis/ monitoring tool — often LAGS the spreadsheet.

Nominal publication is 10:00 LT, "sometimes a little later". So whichever
channel happens to lead at a given minute should win. This engine polls every
source it can, then merges PER STATION AND PER FUEL, taking the value with the
newest timestamp. A single source being late, broken, or blocked can no longer
hold the whole app back — it just loses the race for those rows.

TIMESTAMP MODEL (the part that makes the merge honest)
------------------------------------------------------
  * portal rows carry an exact per-station submitted_at -> used as-is.
  * a spreadsheet dated D is LEA's official snapshot published ~10:00 LT that
    day -> treated as D 10:00 Europe/Vilnius.
So a station that self-reported at 12:39 correctly beats that morning's
snapshot, while tomorrow's snapshot correctly beats today's self-reports.
Every published price records WHERE it came from and WHEN, so the app can show
"updated 12:39" instead of a blanket "prices may change during the day".

DELIBERATELY NOT A PRICE SOURCE: Power BI. Its `Kaina` column was measured
~25-30% BELOW pump price (a pre-tax basis) — using it would silently publish
wrong prices, the one thing this app must never do. It stays what it already
is: the station REGISTRY (fetch_lea_powerbi.py) and the history backfill
(fetch_lea_history.py). Revisit only if its basis is proven to have changed.

OUTPUT: same station shape the rest of the pipeline expects, plus per-station
`price_updated` (ISO) / `price_src`, and a `sources` block for the gates.
"""

import datetime as dt
import json
import os
import re
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

try:
    from zoneinfo import ZoneInfo
    VILNIUS = ZoneInfo("Europe/Vilnius")
except Exception:                                   # pragma: no cover
    VILNIUS = dt.timezone.utc

FUELS = ("petrol95", "diesel", "lpg")
PORTAL_FUELS = {"benzinas_95": "petrol95", "dyzelinas": "diesel", "snd": "lpg"}
# Known SharePoint share links, newest first. LEA removed these from ena.lt on
# 2026-07-28, so discovery from the page no longer works; we keep the last known
# ones and still re-scan the page in case they come back.
LINKS_PATH = os.path.join("data", "sources", "lea_sharepoint_links.json")
PUBLISH_HOUR_LT = 10          # LEA's nominal publication time


def _now_utc():
    return dt.datetime.now(dt.timezone.utc)


def _parse_ts(s):
    """'2026-07-28T12:39:45.000000Z' / '2026-07-28 12:39:45' -> aware UTC."""
    if not s:
        return None
    t = str(s).strip().replace("Z", "+00:00")
    try:
        d = dt.datetime.fromisoformat(t)
    except ValueError:
        m = re.search(r"20\d\d-\d\d-\d\d", t)
        if not m:
            return None
        d = dt.datetime.fromisoformat(m.group(0))
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def snapshot_ts(date_str):
    """A file/snapshot dated D counts as published D at 10:00 Vilnius."""
    try:
        d = dt.date.fromisoformat(date_str)
    except (TypeError, ValueError):
        return None
    return dt.datetime.combine(d, dt.time(PUBLISH_HOUR_LT), tzinfo=VILNIUS).astimezone(dt.timezone.utc)


def _result(name, ok, stations=None, date=None, error=None):
    return {"source": name, "ok": ok, "stations": stations or [],
            "date": date, "error": error,
            "fetched_utc": _now_utc().replace(microsecond=0).isoformat() + "Z"}


# ------------------------------------------------------------------ sources --

def from_portal():
    """Per-station, per-fuel prices with exact submitted_at timestamps."""
    import fetch_lea_portal as portal
    import fetch_prices as fp
    base, token = portal.discover_credentials()
    raw = json.loads(portal.get(f"{base}/read/prices?per_page=3000",
                                {"Authorization": f"Bearer {token}",
                                 "Accept": "application/json"}))
    rows = raw.get("data") or []
    if len(rows) < 500:
        raise RuntimeError(f"portal returned only {len(rows)} rows")
    st, dates = {}, set()
    for r in rows:
        net = (r.get("company_name") or "").strip()
        if not net:
            continue
        k = fp.station_key(net, (r.get("address") or "").strip(),
                           (r.get("municipality") or "").strip())
        s = st.setdefault(k, {"network": net, "address": (r.get("address") or "").strip(),
                              "municipality": (r.get("municipality") or "").strip(),
                              "locality": "", "petrol95": None, "diesel": None, "lpg": None,
                              "_ts": {}})
        fuel = PORTAL_FUELS.get(r.get("fuel_type"))
        price = fp.to_float(r.get("price"))
        if fuel and price and 0.3 < price < 3.5:
            s[fuel] = price
            ts = _parse_ts(r.get("submitted_at"))
            if ts:
                s["_ts"][fuel] = ts
                dates.add(ts.astimezone(VILNIUS).date().isoformat())
    priced = [s for s in st.values() if any(s[f] is not None for f in FUELS)]
    if len(priced) < 400:
        raise RuntimeError(f"portal has only {len(priced)} priced stations")
    return _result("portal", True, list(st.values()), max(dates) if dates else None)


def _known_links():
    try:
        return json.load(open(LINKS_PATH, encoding="utf-8")).get("links") or []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _remember_link(url):
    links = [l for l in _known_links() if l != url]
    links.insert(0, url)
    os.makedirs(os.path.dirname(LINKS_PATH), exist_ok=True)
    json.dump({"note": "LEA SharePoint daily-price share links, newest first. "
                       "The page stopped listing them on 2026-07-28; kept so the "
                       "engine can still race the spreadsheet against the portal.",
               "links": links[:8]},
              open(LINKS_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def from_sharepoint():
    """The official daily Excel. Links vanished from ena.lt on 2026-07-28, so we
    try any that reappear on the page FIRST, then the last-known ones."""
    import fetch_prices as fp
    import requests
    urls = []
    try:
        html = requests.get(fp.PAGE_URL + f"?_={int(_now_utc().timestamp())}",
                            headers={"User-Agent": fp.BROWSER_UA}, timeout=40).text
        urls += [c["href"] for c in fp.find_price_candidates(html)]
    except Exception as e:
        print(f"[engine] ena.lt page scan failed: {type(e).__name__}: {e}")
    urls += [u for u in _known_links() if u not in urls]
    if not urls:
        raise RuntimeError("no SharePoint link known or discoverable")
    last = None
    for u in urls[:4]:
        try:
            stations, fd = fp.parse_workbook(fp.download_shared_xlsx(u))
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            continue
        if stations and fd:
            _remember_link(u)
            return _result("sharepoint", True, stations, fd)
        last = "parsed but empty/undated"
    raise RuntimeError(f"no usable spreadsheet ({last})")


SOURCES = {"portal": from_portal, "sharepoint": from_sharepoint}


# ------------------------------------------------------------------- merge ---

def merge(results):
    """Per station AND per fuel, keep the price with the newest timestamp."""
    import fetch_prices as fp
    merged, prov = {}, {}
    for res in results:
        if not res["ok"]:
            continue
        snap = snapshot_ts(res["date"])
        for s in res["stations"]:
            k = fp.station_key(s.get("network") or "", s.get("address") or "",
                               s.get("municipality") or "")
            tgt = merged.setdefault(k, {"network": s.get("network") or "",
                                        "address": s.get("address") or "",
                                        "municipality": s.get("municipality") or "",
                                        "locality": s.get("locality") or "",
                                        "petrol95": None, "diesel": None, "lpg": None})
            if not tgt.get("locality") and s.get("locality"):
                tgt["locality"] = s["locality"]
            for f in FUELS:
                price = s.get(f)
                if price is None:
                    continue
                ts = (s.get("_ts") or {}).get(f) or snap
                if ts is None:
                    continue
                cur = prov.get((k, f))
                if cur is None or ts > cur[0]:
                    prov[(k, f)] = (ts, res["source"])
                    tgt[f] = price
    # attach provenance: newest stamp across the station's fuels
    for k, s in merged.items():
        stamps = [(prov[(k, f)][0], prov[(k, f)][1]) for f in FUELS if (k, f) in prov]
        if stamps:
            newest = max(stamps, key=lambda x: x[0])
            ts = newest[0]
            local = ts.astimezone(VILNIUS)
            s["price_updated"] = local.replace(microsecond=0).isoformat()
            s["price_src"] = newest[1]
            # INTRADAY = this operator re-reported AFTER the day's official
            # 10:00 snapshot, so it is genuinely newer than the daily file.
            # Computed here (not in the app) so the UI stays dumb and this stays
            # testable: the app just shows a clock when the flag is set.
            snap = snapshot_ts(local.date().isoformat())
            if snap and ts > snap:
                s["price_intraday"] = True
                s["price_time"] = local.strftime("%H:%M")
    return list(merged.values())


def resolve():
    """Poll every source, merge, and report. Returns (stations, updated, meta)."""
    results = []
    for name, fn in SOURCES.items():
        try:
            r = fn()
            print(f"[engine] {name}: OK, date {r['date']}, {len(r['stations'])} stations")
        except Exception as e:
            r = _result(name, False, error=f"{type(e).__name__}: {e}")
            print(f"[engine] {name}: FAILED — {r['error']}")
        results.append(r)

    ok = [r for r in results if r["ok"]]
    if not ok:
        raise RuntimeError("every price source failed")
    stations = merge(results)
    priced = [s for s in stations if any(s.get(f) is not None for f in FUELS)]
    if len(priced) < 400:
        raise RuntimeError(f"merged result has only {len(priced)} priced stations")

    updated = max(r["date"] for r in ok if r["date"])
    by_src = {}
    for s in stations:
        by_src[s.get("price_src")] = by_src.get(s.get("price_src"), 0) + 1
    meta = {
        "resolved_utc": _now_utc().replace(microsecond=0).isoformat() + "Z",
        "winner_counts": by_src,
        "sources": [{k: r[k] for k in ("source", "ok", "date", "error", "fetched_utc")}
                    for r in results],
    }
    print(f"[engine] merged {len(stations)} stations ({len(priced)} priced), "
          f"date {updated}, winners: {by_src}")
    return stations, updated, meta


if __name__ == "__main__":
    st, upd, meta = resolve()
    print(json.dumps(meta, ensure_ascii=False, indent=1))
