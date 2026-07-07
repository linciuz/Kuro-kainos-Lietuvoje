#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Loyalty-discount WATCHER for the Fuelis loyalty-card feature.

WHAT IT DOES (and what it deliberately does NOT do)
---------------------------------------------------
The app's loyalty hints/notes (LOYALTY_TYPICAL / LOYALTY_NOTES in app.js) are
hand-set from the official loyalty pages. Those pages occasionally change — a
promo ends, or Neste's Wednesday "nuolaidadienis" rotates its value/date. This
script visits each monitored page once a day and records:

  * availability  — is the page still up? An HTTP 404 means the promo page was
                    removed (the deal is probably over). Transient errors
                    (timeout / 403 datacenter-block / TLS) are NOT treated as
                    "gone": the previous reading is carried forward.
  * values        — the ct/l and €/L discount figures found in the page text
                    (where they exist as text — BP "Bakadienis" hides its number
                    in an image, so it legitimately extracts nothing).
  * date          — the Lithuanian date on rotating pages (Neste nuolaidadienis).

It then DIFFS against the previously-committed reading and lists what changed, so
a maintainer can review and (if warranted) update the app's hints. The output
`data/sources/loyalty_watch.json` is a MAINTENANCE artifact only — the app never
reads it, and no scraped number is ever shown to a user as a price. This keeps
the project's rule intact: a price app must never display a wrong price.

OUTPUT  data/sources/loyalty_watch.json
  {"generated","disclaimer","pages":{key:{network,url,kind,available,http_status,
   values:[...],date,checked,last_error}}, "changes":[{key,type,old,new}]}
"""

import datetime as dt
import gzip
import html as _html
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

OUT = os.path.join("data", "sources", "loyalty_watch.json")
UA_WEB = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"

# Pages to watch. `kind`: "standing" = a stable rule we only re-check occasionally;
# "dynamic" = rotates (value and/or date change) — the ones worth watching closely.
PAGES = [
    {"key": "viada_card",           "network": "Viada",            "kind": "standing",
     "url": "https://www.viada.lt/korteles-ir-lojalumas/viadaplus-lojalumas/"},
    {"key": "viada_weekend",        "network": "Viada",            "kind": "standing",
     "url": "https://www.viada.lt/akcija/nuolaidu-savaitgaliai/"},
    {"key": "neste_card",           "network": "Neste",            "kind": "standing",
     "url": "https://www.neste.lt/lt/neste-nuolaidu-kortele"},
    {"key": "neste_nuolaidadienis", "network": "Neste",            "kind": "dynamic",
     "url": "https://www.neste.lt/lt/nuolaidadienis"},
    {"key": "bp_card",              "network": "Baltic Petroleum", "kind": "standing",
     "url": "https://balticpetroleum.lt/korteles-privatiems"},
    {"key": "bp_bakadienis",        "network": "Baltic Petroleum", "kind": "dynamic",
     "url": "https://balticpetroleum.lt/akcijos/bakadienis/65"},
    {"key": "circlek_extra",        "network": "Circle K",         "kind": "standing",
     "url": "https://www.circlek.lt/privatiems/extra"},
    # Weekend/promo page and Viada's seasonal Wednesday page: both DYNAMIC — expect
    # them to appear/disappear (404 when a promo isn't running). Watched so a new
    # Wednesday/weekend special (e.g. the rumoured Circle K −12 Wed) gets flagged.
    {"key": "circlek_10ct",         "network": "Circle K",         "kind": "dynamic",
     "url": "https://www.circlek.lt/10ct"},
    {"key": "viada_treciadieniai",  "network": "Viada",            "kind": "dynamic",
     "url": "https://www.viada.lt/akcija/super-treciadieniai/"},
]

DISCLAIMER = ("Monitoring artifact — NOT read by the app and never shown as a price. "
              "Values are for a maintainer to review before updating the hand-set "
              "loyalty hints/notes in app.js. Some pages hide the figure in an image.")

# "3 ct/l", "3,5 ct/l", "-10 ct/l", "10 ct litrui", "7 ct nuolaida" -> capture the number.
CTL_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*ct\b", re.I)
# "0,03 EUR/litrui", "0.03 €/L" -> capture the number.
EURL_RE = re.compile(r"(\d+[.,]\d+)\s*(?:eur|€)\s*/?\s*(?:litr\w*|l\b)", re.I)
# Lithuanian date e.g. "2026 m. liepos 8 d."
DATE_RE = re.compile(r"\d{4}\s*m\.\s*[a-ząčęėėįšųūž]+\s*\d{1,2}\s*d\.", re.I)


def http_text(url):
    """(status, html) with a browser UA. TLS verified except for viada.lt, which
    presents a self-signed chain (per the announced-deals research)."""
    ctx = ssl.create_default_context()
    if "viada.lt" in url:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"User-Agent": UA_WEB, "Accept-Language": "lt"})
    resp = urllib.request.urlopen(req, timeout=40, context=ctx)
    raw = resp.read()
    if resp.headers.get("Content-Encoding") == "gzip":
        raw = gzip.decompress(raw)
    return resp.status, raw.decode("utf-8", "replace")


def main_content(html):
    """Strip chrome so nav/footer/menu wording can't pollute the extracted values."""
    h = re.sub(r"<(script|style|nav|header|footer|aside)[^>]*>.*?</\1>", " ",
               html, flags=re.S | re.I)
    return _html.unescape(re.sub(r"<[^>]+>", " ", h))


def _num(s):
    """Normalize a captured number: comma->dot, strip a trailing .0 (3.0 -> '3')."""
    v = float(s.replace(",", "."))
    return str(int(v)) if v == int(v) else str(v)


def extract(text):
    """Distinct discount figures + first date found, as stable, sorted strings."""
    vals = set()
    for m in CTL_RE.finditer(text):
        vals.add(f"{_num(m.group(1))} ct/l")
    for m in EURL_RE.finditer(text):
        vals.add(f"{_num(m.group(1))} €/l")
    values = sorted(vals, key=lambda s: (float(s.split()[0]), s))
    dm = DATE_RE.search(text)
    date = re.sub(r"\s+", " ", dm.group(0)).strip() if dm else None
    return values, date


def read_page(cfg):
    """Fetch + extract one page into a reading dict. HTTP 404 = removed (deal likely
    over); other failures = transient (caller carries the previous reading forward)."""
    entry = {"network": cfg["network"], "url": cfg["url"], "kind": cfg["kind"],
             "available": None, "http_status": None, "values": [], "date": None,
             "last_error": None}
    try:
        status, html = http_text(cfg["url"])
        entry["http_status"] = status
        if status == 200:
            entry["available"] = True
            entry["values"], entry["date"] = extract(main_content(html))
        else:
            entry["available"] = False
            entry["last_error"] = f"HTTP {status}"
    except urllib.error.HTTPError as e:
        entry["http_status"] = e.code
        if e.code == 404:
            entry["available"] = False           # definitive: page removed
        else:
            entry["last_error"] = f"HTTP {e.code}"   # transient (403/5xx/…)
    except Exception as e:
        entry["last_error"] = f"{type(e).__name__}: {e}"   # timeout/TLS/DNS = transient
    return entry


def load_existing():
    try:
        return json.load(open(OUT, encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def diff(prev_pages, key, cur):
    """Yield change dicts for one page vs its previous reading."""
    prev = (prev_pages or {}).get(key)
    if prev is None:
        yield {"key": key, "type": "new", "old": None, "new": cur.get("values") or cur.get("available")}
        return
    # availability transitions only when we have a definitive True/False now.
    if cur["available"] is not None and cur["available"] != prev.get("available"):
        yield {"key": key, "type": "went_offline" if not cur["available"] else "back_online",
               "old": prev.get("available"), "new": cur["available"]}
    if cur["available"]:   # only compare content on a good fetch
        if cur["values"] != (prev.get("values") or []):
            yield {"key": key, "type": "value_changed", "old": prev.get("values"), "new": cur["values"]}
        if cur["date"] != prev.get("date"):
            yield {"key": key, "type": "date_changed", "old": prev.get("date"), "new": cur["date"]}


def main():
    prev = load_existing()
    prev_pages = (prev or {}).get("pages") or {}

    pages, changes = {}, []
    any_ok = False
    for cfg in PAGES:
        cur = read_page(cfg)
        # Transient failure: keep the previous reading rather than losing/false-flagging it.
        if cur["available"] is None and cur["last_error"] and cfg["key"] in prev_pages:
            kept = dict(prev_pages[cfg["key"]])
            kept["last_error"] = cur["last_error"]
            kept["network"], kept["url"], kept["kind"] = cfg["network"], cfg["url"], cfg["kind"]
            cur = kept
            print(f"[warn] {cfg['key']}: {cur['last_error']} — keeping previous reading")
        else:
            any_ok = any_ok or cur["available"] is True
            for ch in diff(prev_pages, cfg["key"], cur):
                changes.append(ch)
        cur["checked"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z"
        pages[cfg["key"]] = cur
        tag = ("OK" if cur["available"] else "GONE") if cur["available"] is not None else "err"
        print(f"[{tag}] {cfg['key']}: values={cur['values']} date={cur['date']}")

    # Whole-run outage guard: if nothing fetched and we had a good file, keep it.
    if not any_ok and prev and not changes:
        print("[warn] no page fetched this run — keeping previous file unchanged")
        return

    for ch in changes:
        print(f"::warning::loyalty page changed [{ch['key']}] {ch['type']}: {ch['old']} -> {ch['new']}")
    print(f"[info] {len(changes)} change(s) detected")

    payload = {
        "generated": dt.datetime.now(dt.timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z",
        "disclaimer": DISCLAIMER,
        "changes": changes,
        "pages": pages,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[ok] wrote {OUT}")


if __name__ == "__main__":
    main()
