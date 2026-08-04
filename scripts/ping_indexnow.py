#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IndexNow — push "this page changed" to search engines instead of waiting.

WHY THIS EXISTS
---------------
Measured 2026-08-05 in Search Console: 5 URLs indexed, 9 not, and inspecting
https://fuelis.lt/kainos/kauno-m-sav.html returned "URL nera Google" with BOTH
"no referring sitemaps" and "no referring page". The page is fine (HTTP 200,
self-canonical, index,follow, listed in sitemap.xml, linked from the homepage) —
it simply had never been crawled. On a young domain with no inbound links,
crawlers ration attention: a sitemap is a hint they may act on in weeks.

IndexNow flips that from pull to push. One HTTP POST says "these URLs changed,
come now". It is a real open protocol (indexnow.org), free, no signup, no quota
paperwork; the shared endpoint fans out to every participant.

HONEST SCOPE — read this before expecting miracles:
  * WORKS:        Bing, Yandex, Seznam, Naver, Yep. Bing's index also feeds
                  DuckDuckGo, Ecosia, and several AI answer engines, so this is
                  worth real traffic, not just a checkbox.
  * DOES NOTHING: Google. Google has never joined IndexNow and says so publicly.
                  Google discovery still depends on sitemap.xml + Search Console
                  + inbound links. Nothing here changes that; do not read a
                  successful submit as "Google will index us now".
  * NOT A RANKING SIGNAL either way. It affects WHETHER and HOW FAST a page is
    crawled, not where it places. Being crawled is the precondition, though —
    at 5/14 indexed, crawl coverage is the actual bottleneck, not ranking.

RESTRAINT (the part that keeps us in good standing)
---------------------------------------------------
IndexNow is for pages that ACTUALLY changed. Spraying the same 63 URLs every
15-minute price run would be abuse and gets a host quietly de-prioritised. So we
submit at most once per published price date, plus immediately for genuinely new
URLs (a new municipality page). State lives in data/_indexnow.json, committed
with the data, so the whole pipeline shares one memory of what was sent.

Always exits 0 — a search-engine ping must never fail the data pipeline.
"""

import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

HOST = "fuelis.lt"
SITE = f"https://{HOST}"
ENDPOINT = "https://api.indexnow.org/IndexNow"
SITEMAP = "sitemap.xml"
STATIONS = os.path.join("data", "stations.json")
STATE = os.path.join("data", "_indexnow.json")
# The key is public by design: ownership is proven by serving <key>.txt from the
# site root, so anyone can read it and nobody but us can host it. Not a secret,
# and deliberately not in Actions secrets — it must ship in the repo to be served.
KEY_FILE_RE = re.compile(r"^[0-9a-f]{8,128}\.txt$")
MAX_URLS = 10000            # protocol limit per request; we send ~63


def find_key():
    """The key IS the filename of the <key>.txt in the repo root (and the file's
    own contents must match). Discovering it rather than hardcoding means a
    rotation is just: drop in a new file, delete the old one."""
    for name in sorted(os.listdir(".")):
        if not KEY_FILE_RE.match(name):
            continue
        try:
            body = open(name, encoding="utf-8").read().strip()
        except OSError:
            continue
        if body and name == f"{body}.txt":
            return body, f"{SITE}/{name}"
    return None, None


def sitemap_urls():
    try:
        xml = open(SITEMAP, encoding="utf-8").read()
    except OSError as e:
        print(f"[indexnow] cannot read {SITEMAP}: {e}")
        return []
    return re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)


def data_date():
    try:
        return json.load(open(STATIONS, encoding="utf-8")).get("updated")
    except (OSError, json.JSONDecodeError):
        return None


def read_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_state(state):
    json.dump(state, open(STATE, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, sort_keys=True)


def submit(key, key_location, urls):
    """POST the batch. Returns (http_status, text). IndexNow's codes:
    200 OK · 202 accepted, key still being validated · 400 malformed ·
    403 key invalid/not served · 422 URL not on this host · 429 slow down."""
    payload = json.dumps({
        "host": HOST,
        "key": key,
        "keyLocation": key_location,
        "urlList": urls[:MAX_URLS],
    }).encode("utf-8")
    req = urllib.request.Request(
        ENDPOINT, data=payload, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8",
                 "User-Agent": f"fuelis-lt/1.0 (+{SITE}; indexnow)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, (r.read(400) or b"").decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, (e.read(400) or b"").decode("utf-8", "replace")
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def main():
    key, key_location = find_key()
    if not key:
        print("::warning::[indexnow] no <key>.txt in the repo root — skipping. "
              "Create one whose name matches its contents (e.g. abc123.txt containing abc123).")
        return 0

    urls = sitemap_urls()
    if not urls:
        print("[indexnow] sitemap has no URLs — nothing to submit.")
        return 0

    state = read_state()
    today = data_date()
    sent_before = set(state.get("submitted_urls") or [])
    new_urls = [u for u in urls if u not in sent_before]

    # Two independent reasons to ping, so neither a quiet day nor a new page is missed:
    #   1. a NEW price date  -> every page's content genuinely changed
    #   2. NEW urls          -> a page that has never been announced at all
    date_changed = bool(today) and state.get("last_date") != today
    if date_changed:
        batch, why = urls, f"new price date {today}"
    elif new_urls:
        batch, why = new_urls, f"{len(new_urls)} new URL(s)"
    else:
        print(f"[indexnow] nothing new (date {today}, {len(urls)} URLs already submitted) — "
              "not re-pinging. Spamming unchanged URLs is what gets a host throttled.")
        return 0

    status, body = submit(key, key_location, batch)
    stamp = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat() + "Z"
    print(f"[indexnow] submitted {len(batch)} URL(s) ({why}) -> HTTP {status} {body[:160]!r}")

    if status in (200, 202):
        state.update(last_date=today, last_submit_utc=stamp, last_status=status,
                     last_count=len(batch),
                     submitted_urls=sorted(sent_before | set(batch)),
                     key_location=key_location)
        write_state(state)
        if status == 202:
            print(f"[indexnow] 202 = accepted, key still being verified. Confirm "
                  f"{key_location} is publicly reachable; it becomes 200 once it is.")
    else:
        # Record the failure but do NOT advance last_date, so the next run retries.
        state.update(last_error=f"HTTP {status}: {body[:200]}", last_error_utc=stamp)
        write_state(state)
        hint = {403: "the key file isn't being served at keyLocation (check GitHub Pages deployed it)",
                422: "a URL in the batch isn't on this host",
                400: "malformed request",
                429: "rate limited — back off"}.get(status, "see the response body")
        print(f"::warning::[indexnow] submit failed ({hint}) — will retry next run. Not fatal.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
