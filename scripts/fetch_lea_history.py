#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Backfill data/price_history.json with LEA's OWN historical national prices.

The ena.lt Power BI report carries a `degalu_kainos_senos` ("old prices") table
with a real per-station daily history (Data + Degalų tipas + Kaina) going back
to when LEA launched the portal (2026-04-08). We aggregate it to the daily
national min/avg/max per fuel — the exact shape append_history.py accumulates
going forward — and MERGE it in (only dates not already present, so today's
retail snapshots are never overwritten). Verified: these are retail prices
(April diesel avg €2.30 → June €1.87), consistent with the live history's basis,
NOT the pre-tax figure the wide `degalu_kainos.Kaina` sometimes shows.

Idempotent: re-running just re-adds the same historical days, deduped.
Reuses fetch_lea_powerbi.py for the resource-key scrape + host/ids.
"""

import datetime
import gzip
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_lea_powerbi as pbi   # resource_key(), _get, PBI_HOST, DATASET_ID, REPORT_ID, MODEL_ID, UA, _CTX

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

OUT = os.path.join("data", "price_history.json")
FUEL_MAP = {"95 benzinas": "petrol95", "Dyzelinas": "diesel", "SND": "lpg"}


def _post(url, body, hdr):
    req = urllib.request.Request(url, data=body, headers=hdr)
    with urllib.request.urlopen(req, timeout=120, context=pbi._CTX) as r:
        raw = r.read()
    return gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw


def fetch_history():
    rk = pbi.resource_key()
    hdr = {"User-Agent": pbi.UA, "Content-Type": "application/json;charset=UTF-8",
           "X-PowerBI-ResourceKey": rk, "Origin": "https://app.powerbi.com",
           "Referer": "https://app.powerbi.com/", "Accept-Encoding": "gzip"}

    def col(prop, name):
        return {"Column": {"Expression": {"SourceRef": {"Source": "d"}}, "Property": prop}, "Name": name}

    def agg(prop, name, fn):
        return {"Aggregation": {"Expression": {"Column": {"Expression": {"SourceRef": {"Source": "d"}},
                "Property": prop}}, "Function": fn}, "Name": name}   # 3=Min 1=Avg 4=Max

    q = {"version": "1.0.0", "queries": [{"Query": {"Commands": [{"SemanticQueryDataShapeCommand": {
        "Query": {"Version": 2, "From": [{"Name": "d", "Entity": "degalu_kainos_senos", "Type": 0}],
            "Select": [col("Data", "data"), col("Degalų tipas", "tipas"),
                       agg("Kaina", "mn", 3), agg("Kaina", "av", 1), agg("Kaina", "mx", 4)]},
        "Binding": {"Primary": {"Groupings": [{"Projections": [0, 1, 2, 3, 4]}]},
                    "DataReduction": {"DataVolume": 6, "Primary": {"Window": {"Count": 30000}}}, "Version": 1}}}]},
        "CacheKey": "", "QueryId": "",
        "ApplicationContext": {"DatasetId": pbi.DATASET_ID, "Sources": [{"ReportId": pbi.REPORT_ID}]}}],
        "cancelQueries": [], "modelId": pbi.MODEL_ID}

    resp = _post(pbi.PBI_HOST + "/public/reports/querydata?synchronous=true", json.dumps(q).encode(), hdr)
    d = json.loads(resp)["results"][0]["result"]["data"]["dsr"]["DS"][0]
    fuel_dict = d.get("ValueDicts", {}).get("D0", [])
    dm0 = d["PH"][0]["DM0"]

    # Decode: 5 group columns (date, fuelIdx, min, avg, max); R bitmask carries
    # the previous value for an unchanged column (the date repeats per fuel).
    prev = [None] * 5
    by_date = {}
    for row in dm0:
        R = row.get("R", 0)
        C = row.get("C", [])
        ci = 0
        cur = []
        for i in range(5):
            if R & (1 << i):
                cur.append(prev[i])
            else:
                cur.append(C[ci]); ci += 1
        prev = cur
        date = (datetime.datetime(1970, 1, 1) + datetime.timedelta(milliseconds=cur[0])).date().isoformat()
        fuel = FUEL_MAP.get(fuel_dict[cur[1]] if isinstance(cur[1], int) and cur[1] < len(fuel_dict) else "")
        if not fuel:
            continue
        by_date.setdefault(date, {})[fuel] = {
            "min": round(float(cur[2]), 3), "avg": round(float(cur[3]), 3), "max": round(float(cur[4]), 3)}
    return by_date


def main():
    by_date = fetch_history()
    if not by_date:
        print("[warn] no historical rows decoded — leaving price_history.json unchanged")
        return
    try:
        doc = json.load(open(OUT, encoding="utf-8"))
        hist = doc.get("history", [])
    except (FileNotFoundError, json.JSONDecodeError):
        doc, hist = {}, []

    have = {h["date"] for h in hist}
    added = 0
    for date in sorted(by_date):
        if date in have:
            continue
        entry = {"date": date}
        entry.update(by_date[date])
        hist.append(entry)
        added += 1

    hist.sort(key=lambda h: h["date"])
    doc["history"] = hist
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(doc, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    span = f"{hist[0]['date']} → {hist[-1]['date']}" if hist else "empty"
    print(f"[ok] backfilled {added} historical day(s); price_history.json now {len(hist)} days ({span})")


if __name__ == "__main__":
    main()
