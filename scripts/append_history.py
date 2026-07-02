#!/usr/bin/env python3
"""
Append today's national fuel-price summary to data/price_history.json so the app
can show a price trend (each pipeline run overwrites stations.json, so without
this there is no history). One entry per day, idempotent (replaces same-day),
keeps ~1 year. Run after fetch_prices.py.
"""

import json
import os

STATIONS = os.path.join("data", "stations.json")
HIST = os.path.join("data", "price_history.json")


def main():
    data = json.load(open(STATIONS, encoding="utf-8"))
    date = data.get("updated")
    summ = data.get("summary") or {}
    if not date or not summ:
        print("[warn] no summary/date in stations.json — skipping history")
        return

    entry = {"date": date}
    for f in ("petrol95", "diesel", "lpg"):
        s = summ.get(f)
        if s:
            entry[f] = {"min": s.get("min"), "avg": s.get("avg"), "max": s.get("max")}

    try:
        prev = json.load(open(HIST, encoding="utf-8")).get("history", [])
        if not isinstance(prev, list):
            prev = []
    except (FileNotFoundError, json.JSONDecodeError):
        prev = []

    hist = [h for h in prev if h.get("date") != date]   # replace same-day
    hist.append(entry)
    hist.sort(key=lambda h: h.get("date", ""))
    hist = hist[-400:]                                  # ~1 year
    json.dump({"history": hist}, open(HIST, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[ok] price history: {len(hist)} day(s), latest {date}")


if __name__ == "__main__":
    main()
