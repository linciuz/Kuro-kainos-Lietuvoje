#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ground-truth freshness guard.

The recurring failure was NOT the fetcher (it correctly picks the newest file on
the page) — it was believing "we're current" without checking LEA. This script
compares what WE serve (data/stations.json `updated`) against the newest
"Naujausios degalų kainos (YYYY-MM-DD)" PRICE file actually listed on ena.lt
RIGHT NOW, and:

  * if LEA has a newer file than ours  -> we MISSED it. Re-run fetch_prices to
    self-heal; if still behind, emit a GitHub ::error:: (fails the run -> the
    owner gets an email) so a stale state can NEVER masquerade as success.
  * if LEA's newest == ours            -> we're current. OK.
  * if LEA simply hasn't published newer (their file == ours) -> OK, not a miss.

So it only ever complains when we are genuinely behind the live LEA site — the
exact thing that kept being wrongly reported as "everything's fine, it's LEA".

Run standalone anytime (`python scripts/verify_freshness.py`) to get the honest
answer, or as a workflow step after fetch_prices.
"""

import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_prices as fp   # PAGE_URL, BROWSER_UA, deaccent, main()

import requests

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

STATIONS = os.path.join("data", "stations.json")


def lea_newest_price_date():
    """Newest date among the live 'Naujausios degalų kainos (DATE)' PRICE files
    on ena.lt (excludes the 'pranešimas' analysis report). None if unreachable."""
    bust = f"?_={int(time.time())}"
    try:
        html = requests.get(fp.PAGE_URL + bust, headers={
            "User-Agent": fp.BROWSER_UA, "Cache-Control": "no-cache", "Pragma": "no-cache",
            "Accept-Language": "lt",
        }, timeout=40).text
    except Exception as e:
        print(f"[freshness] could not reach LEA page: {type(e).__name__}: {e}")
        return None
    dates = []
    for href, text in re.findall(
            r'<a[^>]+href="(https://[^"]*sharepoint\.com/[^"]+)"[^>]*>(.*?)</a>', html, re.I | re.S):
        label = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text)).strip()
        td = fp.deaccent(label)
        m = re.search(r"(20\d\d-\d\d-\d\d)", label)
        if m and "naujausios" in td and "kain" in td and "pranesim" not in td:
            dates.append(m.group(1))
    return max(dates) if dates else None


def our_date():
    try:
        return json.load(open(STATIONS, encoding="utf-8")).get("updated")
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def check_only():
    """Just compare — no re-fetch. Exit 1 (fail the CI job -> owner gets an
    email) if we are genuinely behind LEA's newest published file. Used as the
    final GATE after commit so a stale publish can never end as a green run."""
    lea, ours = lea_newest_price_date(), our_date()
    print(f"[freshness gate] LEA newest PRICE file: {lea or '?'} | our data: {ours or '?'}")
    if lea is None:
        # Don't silently pass: unreachable-from-runner is exactly how the viada.lt
        # datacenter-IP block looked. The local-file gate (verify_sources.py)
        # still enforces business-day freshness, so this stays a warning.
        print("::warning::LEA page unreachable from this runner — could not ground-truth "
              "the published date (possible bot-block; verify_sources.py still enforces age).")
        return 0
    if not ours or ours < lea:
        print(f"::error::STALE PUBLISH: LEA has {lea} but we published {ours}. "
              f"The site is showing old prices while LEA has newer — fetch_prices "
              f"could not pick it up (bot-block? SharePoint link/format change?). "
              f"Run `python scripts/verify_freshness.py` locally to reproduce.")
        return 1
    print("[freshness gate] OK — we are current with LEA.")
    return 0


def self_heal():
    """Re-check against LEA and re-fetch if we're behind (fixes a stale-page
    race where the fetch grabbed an old listing). Non-fatal — always returns 0
    so the pipeline continues to commit whatever we have; the post-commit gate
    is what turns a genuine, unrecoverable miss into a loud failure."""
    for attempt in range(3):
        lea, ours = lea_newest_price_date(), our_date()
        print(f"[freshness] LEA newest PRICE file: {lea or '?'} | our data: {ours or '?'}")
        if lea is None:
            print("[freshness] LEA unreachable — cannot verify; leaving data as-is.")
            return 0
        if ours and ours >= lea:
            print("[freshness] OK — we are current with LEA (nothing newer to fetch).")
            return 0
        print(f"::warning::Behind LEA — we have {ours}, LEA has {lea}. Re-fetching (attempt {attempt + 1}/3).")
        try:
            fp.main()   # re-run the fetch; picks the newest file on the page
        except SystemExit:
            pass
        except Exception as e:
            print(f"[freshness] re-fetch raised {type(e).__name__}: {e}")
        time.sleep(3)
    print("[freshness] still behind after 3 attempts — leaving self-heal to the post-commit gate.")
    return 0


if __name__ == "__main__":
    sys.exit(check_only() if "--check-only" in sys.argv else self_heal())
