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

import datetime as dt
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
    # Shared two-markup parser (anchor labels AND block text): LEA moved the
    # label OUT of the anchor on 2026-07-17 and this gate went date-blind.
    dates = fp.lea_price_dates(html)
    return max(dates) if dates else None


def our_date():
    try:
        return json.load(open(STATIONS, encoding="utf-8")).get("updated")
    except (FileNotFoundError, json.JSONDecodeError):
        return None


# Cross-run memory of an observed LEA gap, committed by the workflows.
# MEASURED 2026-07-16 ~11:00 LT: at the instant LEA publishes, their page can
# show the new file while the download still serves yesterday's (CDN/SharePoint
# propagation race) — the next 15-min run healed it. A gap younger than GRACE
# therefore only WARNS; only a gap that PERSISTS goes red (one honest email,
# no publish-moment alarm spam). verify_sources.py's 13:00 rule stays the
# independent hard backstop.
GAP_MARKER = os.path.join("data", "_lea_gap.json")
GRACE_MINUTES = 45


def _read_gap():
    try:
        return json.load(open(GAP_MARKER, encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _note_gap(lea, ours):
    """Record when we FIRST saw this specific LEA date without matching it.
    Keeps the original first_seen while the gap is unchanged."""
    g = _read_gap()
    if not g or g.get("lea") != lea:
        g = {"lea": lea, "ours": ours,
             "first_seen_utc": dt.datetime.now(dt.timezone.utc)
                 .replace(microsecond=0).isoformat()}
        json.dump(g, open(GAP_MARKER, "w", encoding="utf-8"), indent=2)
        print(f"[freshness] gap marker written: behind {lea} since {g['first_seen_utc']}")
    return g


def _clear_gap():
    if os.path.exists(GAP_MARKER):
        os.remove(GAP_MARKER)
        print("[freshness] gap resolved — marker cleared.")


def _gap_age_minutes(g):
    try:
        first = dt.datetime.fromisoformat(g["first_seen_utc"])
        return (dt.datetime.now(dt.timezone.utc) - first).total_seconds() / 60
    except (KeyError, ValueError):
        return None


def check_only():
    """Just compare — no re-fetch. Used as the final GATE after commit.
    Behind LEA and the gap has PERSISTED past GRACE_MINUTES -> exit 1 (red run,
    owner gets an email). A younger gap -> ::warning:: only, because the dense
    15-min schedule usually heals the publish-moment race on the next run."""
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
        g = _read_gap() or {}
        age = _gap_age_minutes(g) if g.get("lea") == lea else None
        if age is not None and age > GRACE_MINUTES:
            print(f"::error::STALE PUBLISH: LEA has {lea} but we still publish {ours} "
                  f"after {age:.0f} min of retries. fetch_prices cannot pick it up "
                  f"(bot-block? SharePoint link/format change?). "
                  f"Run `python scripts/verify_freshness.py` locally to reproduce.")
            return 1
        print(f"::warning::Behind LEA ({ours} vs {lea}) — publish-moment race, within the "
              f"{GRACE_MINUTES}-min grace (age: {age if age is not None else 0:.0f} min). "
              f"The next scheduled run should heal it; persisting past grace turns this red.")
        return 0
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
            _clear_gap()
            return 0
        print(f"[freshness] behind LEA — we have {ours}, LEA has {lea}. Re-fetching (attempt {attempt + 1}/3).")
        try:
            fp.main()   # re-run the fetch; picks the newest file on the page
        except SystemExit:
            pass
        except Exception as e:
            print(f"[freshness] re-fetch raised {type(e).__name__}: {e}")
        time.sleep(3)
    # Still behind: stamp (or keep) the gap marker so the gate can tell a fresh
    # publish-moment race from a persistent failure. Committed with the data.
    lea, ours = lea_newest_price_date(), our_date()
    if lea and (not ours or ours < lea):
        _note_gap(lea, ours)
    print("[freshness] still behind after 3 attempts — the post-commit gate decides loudness.")
    return 0


if __name__ == "__main__":
    sys.exit(check_only() if "--check-only" in sys.argv else self_heal())
