#!/usr/bin/env python3
"""
Brent crude oil price + weekly trend -> data/oil.json.

When the global oil price rises week-over-week, Lithuanian pump prices tend to
follow (with a lag), so the app shows an early "fuel prices may rise" alert.

Source: Yahoo Finance chart API (free, no key; needs a browser User-Agent or it
429s). Brent (BZ=F) is the right benchmark for European/Lithuanian pump prices
(EU refining/imports price off Brent, not US WTI). Weekly change = last close vs
5 trading days prior (trading days, so weekends don't null the comparison).
"""

import datetime as dt
import json
import os
import sys
import urllib.request

OUT = os.path.join("data", "oil.json")
HOSTS = ["query1", "query2"]
SYMBOL = "BZ=F"   # Brent
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"


def fetch():
    last = None
    for host in HOSTS:
        url = f"https://{host}.finance.yahoo.com/v8/finance/chart/{SYMBOL}?range=1mo&interval=1d"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:
            print(f"[warn] {host} failed: {e}")
            last = e
    raise last


def iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")


def main():
    res = fetch()["chart"]["result"][0]
    ts = res["timestamp"]
    close = res["indicators"]["quote"][0]["close"]
    currency = (res.get("meta") or {}).get("currency", "USD")
    series = [(t, c) for t, c in zip(ts, close) if c is not None]
    if len(series) < 6:
        print("[error] not enough oil data points")
        sys.exit(2)

    latest = series[-1][1]
    week_ref = series[-6][1]                      # 5 trading days prior
    month_ref = series[0][1]
    week_chg = round(100 * (latest - week_ref) / week_ref, 1)
    month_chg = round(100 * (latest - month_ref) / month_ref, 1)

    # Weekly average (last 5 trading days) + previous week, for a smoothed
    # week-over-week signal shown at the bottom of the app.
    last5 = [c for _, c in series[-5:]]
    prev5 = [c for _, c in series[-10:-5]]
    if len(prev5) < 5:            # <10 data points: no real prior week to compare
        prev5 = last5             # -> avg_chg ~ 0 (stable), not a skewed 1-vs-5 ratio
    week_avg = round(sum(last5) / len(last5), 2)
    prev_week_avg = round(sum(prev5) / len(prev5), 2)
    avg_chg = round(100 * (week_avg - prev_week_avg) / prev_week_avg, 1)

    # Symmetric ±5% bands on the SAME smoothed week-over-week AVERAGE change the
    # app shows: >= +5% likely pushes fuel prices UP, <= -5% DOWN, and in between
    # (-5%..+5%) the market is stable. Stronger wording past ±8%.
    ac = avg_chg
    if ac >= 8:
        level = "strong_up"      # prices likely to rise
    elif ac >= 5:
        level = "rise"           # prices may rise
    elif ac <= -8:
        level = "strong_down"    # prices likely to drop
    elif ac <= -5:
        level = "fall"           # prices may drop
    else:
        level = "stable"         # -5% .. +5%

    payload = {
        "updated": dt.date.today().isoformat(),
        "as_of": iso(series[-1][0]),
        "symbol": "Brent",
        "currency": currency,
        "price": round(latest, 2),
        "week_avg": week_avg,
        "prev_week_avg": prev_week_avg,
        "avg_change_pct": avg_chg,
        "week_change_pct": week_chg,
        "month_change_pct": month_chg,
        "level": level,
        "history": [{"date": iso(t), "close": round(c, 2)} for t, c in series],
    }
    os.makedirs("data", exist_ok=True)
    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[ok] Brent ${latest:.2f} {currency} | week {week_chg:+.1f}% month {month_chg:+.1f}% | level={level}")


if __name__ == "__main__":
    main()
