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


def portal_newest_price_date():
    """Newest submit date visible in LEA's portal API — the ground truth since
    2026-07-28, when LEA removed the SharePoint links from ena.lt and migrated
    everything to degalukainos.ena.lt. Independent of what WE published: it asks
    the source directly. None if unreachable."""
    try:
        import fetch_lea_portal as portal
        base, token = portal.discover_credentials()
        raw = json.loads(portal.get(
            f"{base}/read/prices?per_page=3000",
            {"Authorization": f"Bearer {token}", "Accept": "application/json"}))
    except Exception as e:
        print(f"[freshness] could not reach the portal API: {type(e).__name__}: {e}")
        return None
    dates = set()
    for r in raw.get("data") or []:
        m = re.search(r"20\d\d-\d\d-\d\d", str(r.get("submitted_at") or ""))
        if m:
            dates.add(m.group(0))
    return max(dates) if dates else None


def sharepoint_newest_price_date():
    """Newest 'Naujausios degalų kainos (DATE)' label still on ena.lt, if any.
    LEA stripped these on 2026-07-28; kept because they may return, and because
    a second independent opinion is worth having. None if absent/unreachable."""
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


def lea_newest_price_date():
    """Newest price date LEA offers ANYWHERE — portal first (the live feed),
    SharePoint second. Taking the max means a working source can't be masked by
    a retired one: the 07-28 migration made the old page return nothing, which
    would otherwise read as 'unverifiable' forever."""
    p = portal_newest_price_date()
    s = sharepoint_newest_price_date()
    both = [d for d in (p, s) if d]
    if p and s and p != s:
        print(f"[freshness] portal says {p}, ena.lt page says {s} — using the newer.")
    return max(both) if both else None


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
            # One red per gap: only the run whose self-heal ARMED the alarm
            # (alarmed_at within ~20 min) fails; later runs warn on green so a
            # persistent external outage sends one email, not four an hour.
            recent_alarm = False
            try:
                aat = g.get("alarmed_at")
                recent_alarm = bool(aat) and (dt.datetime.now(dt.timezone.utc)
                    - dt.datetime.fromisoformat(aat)).total_seconds() < 20 * 60
            except (TypeError, ValueError):
                pass
            if recent_alarm or not g.get("alarmed"):
                print(f"::error::STALE PUBLISH: LEA has {lea} but we still publish {ours} "
                      f"after {age:.0f} min of retries. fetch_prices cannot pick it up "
                      f"(bot-block? SharePoint link/format change?). "
                      f"Run `python scripts/verify_freshness.py` locally to reproduce.")
                return 1
            print(f"::warning::[repeat — already alarmed] still behind LEA ({ours} vs {lea}, "
                  f"{age:.0f} min) — retries continue every run; a NEW gap re-alarms red.")
            return 0
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
    # Once the gap outlives the grace period, mark it alarmed (with a timestamp)
    # so the gate reds exactly ONCE per gap instead of every 15-min run.
    lea, ours = lea_newest_price_date(), our_date()
    if lea and (not ours or ours < lea):
        g = _note_gap(lea, ours)
        age = _gap_age_minutes(g)
        if age is not None and age > GRACE_MINUTES and not g.get("alarmed"):
            g["alarmed"] = True
            g["alarmed_at"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
            json.dump(g, open(GAP_MARKER, "w", encoding="utf-8"), indent=2)
            print("[freshness] gap outlived the grace period — alarm armed for this run's gate.")
    print("[freshness] still behind after 3 attempts — the post-commit gate decides loudness.")
    return 0


if __name__ == "__main__":
    sys.exit(check_only() if "--check-only" in sys.argv else self_heal())
