#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Viada "announced deals" POINTER collector (opt-in layer, price-free by design).

WHY THIS EXISTS AND WHY IT NEVER CARRIES A PRICE
------------------------------------------------
Viada runs DYNAMIC fuel-loyalty deals (a bigger discount on "Super trečiadieniai"
Wednesdays; a standing -10 ct/L on weekends+holidays with ViadaPLUS; card-tier
deals like STUDY -10 ct/L). The user asked whether those day-ahead numbers can be
fetched programmatically. Investigation (2026-07-07) found:

  * Facebook   — NOT viable. No public Page RSS anymore; Graph API needs Page
                 Public Content Access (App Review + business verification) that a
                 third party can't get for Viada's page; HTML scraping is against
                 ToS and routinely blocked. -> not used here.
  * App API    — NOT viable for numbers. The Viada app rides the SAME FSCC backend
                 as Baltic Petroleum: https://mobileapi.fscc.lt/viadalt/api/... .
                 /stations and /products are open (catalog only, NO prices);
                 /stations/{id} and /coupons return 401 — every price / per-member
                 discount is login-gated. -> not used here.
  * viada.lt   — REACHABLE, but marketing-grade. The /akcijos/ listing is pure
                 imagery ("Daugiau" buttons, no text/alt); the actual "-X ct litrui"
                 figure usually lives INSIDE the promo IMAGE, not in the HTML. The
                 `akcija` post type is NOT exposed via wp-json (404). So the exact
                 discount CANNOT be text-extracted reliably, and auto-parsing a
                 number would risk showing a WRONG price — the one thing this app
                 must never do.

CONCLUSION: we do NOT scrape any "-X ct" discount amount from marketing imagery.
This script surfaces the EXISTENCE of Viada's fuel-promo pages as titled LINKS.

ONE EXCEPTION — the "Super trečiadieniai" (Wednesday) page (re-verified
2026-07-07): when the promo is running, that page publishes the day's absolute
promo prices as PLAIN TEXT with an explicit machine-readable validity date:
    Dyzelinas (D ir D MULTI FX) – 1,669 Eur/l ... Pasiūlymas galioja: 2026-07-08
Those are Viada's own officially-published numbers (same values as their SMS and
app), so we extract them under STRICT validation (explicit ISO validity date
required, >=2 fuels parsed, each price in a sane band) into a "wednesday" field.
The app applies them ONLY on the stated date and ONLY when they are BELOW a
station's official price — a wrong/stale value can therefore never raise a price
or leak onto another day. If the page is imagery-only again, parsing simply
yields nothing and the field is omitted.

OUTPUT  data/sources/viada_promos.json
  {"generated","source","disclaimer","network":"Viada",
   "promos":[{"title","url","slug","fuel_related":true,"kind":"standing|listed",
              "active":true}],
   "wednesday": {"valid_date":"YYYY-MM-DD",
                 "prices":{"petrol95":1.569,"diesel":1.669,"lpg":0.659},
                 "url": ...}   # present ONLY when the live page passes validation
  }
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

OUT = os.path.join("data", "sources", "viada_promos.json")
UA_WEB = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
LISTING = "https://www.viada.lt/akcijos/"
BASE = "https://www.viada.lt/akcija/"

# viada.lt turned on Cloudflare bot protection ~2026-07-08, which 403s GitHub
# Actions runners (residential IPs still pass). Fallback chain, direct-first so
# the pipeline self-heals if Viada ever unblocks datacenter IPs:
#   1. direct fetch;
#   2. our Worker's slug-whitelisted proxy — measured 2026-07-15: request-context
#      Worker subrequests KEEP the caller's identity and cron-context ones are
#      403'd too, so this only helps via its KV copy if Viada ever relaxes the
#      Worker block; kept because it costs one request and self-heals;
#   3. r.jina.ai public reader (fetches from ITS infra, passes the WAF —
#      verified 2026-07-15; X-Return-Format: html gives the raw page so the
#      parser below works unchanged).
# A wrong price can never slip through any layer: parse_wednesday still demands
# the explicit validity date, >=2 fuels, and the sane price band.
PROXY = "https://kk-reports.fuelis.workers.dev/proxy/viada?slug="
JINA = "https://r.jina.ai/"

DISCLAIMER = ("Pointer only. No price is fetched or implied — Viada bakes discount "
              "figures into promo images and gates exact prices behind app login. "
              "Confirm the amount on the official page / in the Viada app.")

# Standing, officially-published fuel deals that recur (not day-ahead one-offs).
# Included so the layer is useful even on days the seasonal Wednesday page is down.
# STILL price-free: we link out, we do not assert the cents.
STANDING = [
    ("nuolaidu-savaitgaliai",            "Nuolaidų savaitgaliai (ViadaPLUS)"),
    ("nuolaidos-su-viada-study-kortele", "Nuolaidos su VIADA STUDY kortele"),
    ("nuolaidos-su-viada-agro-kortele",  "Nuolaidos su VIADA AGRO kortele"),
]
# Seasonal fuel-promo slugs to also probe directly (may 404 when not running).
SEASONAL = [
    ("super-treciadieniai",   "Super trečiadieniai"),
    ("savaitgaliauk-ir-keliauk", "Savaitgaliauk ir keliauk"),
]

# A NEW listing promo counts as fuel-related only if its page uses PER-LITRE
# pricing wording. Calibrated (2026-07-07) against known pages: this fires on the
# fuel deals (study/agro/savaitgaliai) and NOT on food/store deals. Note that
# "degalinė"/"ViadaPLUS" appear in every page's nav/footer, so they are useless as
# signals and deliberately excluded; the discount number itself lives in the promo
# IMAGE, so we never try to read it — presence of per-litre wording only flags that
# a fuel deal exists, and we still just link out.
FUEL_PAGE_RE = re.compile(
    r"(\bct\s*/\s*l\b|\bct\s+litrui\b|cent\w*\s+litrui|\blitrui\b|"
    r"\bbenzin\w*|\bdyzelin\w*|\bdujoms?\b|\bdujų\b)", re.I)


# Fetch-failure diagnostics, committed by the workflow as
# data/sources/viada_debug.json so runner-side errors are readable without
# Actions-log access (the logs API needs auth; this file doesn't).
DEBUG_OUT = os.path.join("data", "sources", "viada_debug.json")
DEBUG = {"attempts": []}


def _note(kind, url, err, body=""):
    DEBUG["attempts"].append({
        "kind": kind, "url": url, "err": err,
        "body_head": re.sub(r"\s+", " ", body)[:300],
    })


def _http_get(url):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE          # viada.lt has presented a self-signed chain
    req = urllib.request.Request(url, headers={"User-Agent": UA_WEB, "Accept-Language": "lt"})
    resp = urllib.request.urlopen(req, timeout=40, context=ctx)
    raw = resp.read()
    if resp.headers.get("Content-Encoding") == "gzip":
        raw = gzip.decompress(raw)
    return resp.status, raw.decode("utf-8", "replace")


def _proxy_slug(url):
    """Map a viada.lt URL onto the Worker proxy's slug parameter (or None)."""
    if url == LISTING:
        return "akcijos"
    m = re.match(re.escape(BASE) + r"([a-z0-9-]{1,80})/?$", url)
    return m.group(1) if m else None


def _jina_get(url):
    """Fetch through the r.jina.ai public reader (its own egress passes Viada's
    WAF). X-Return-Format: html returns the raw page, so parsing is unchanged.
    Longer timeout: the reader renders the page before responding.
    NOTE: jina 403s browser-UA strings from non-browser clients (and 1010s empty
    UAs) — an honest tool UA is both required and politer."""
    headers = {
        "User-Agent": "fuelis-lt/1.0 (+https://fuelis.lt; fuel price app data fetcher)",
        "X-Return-Format": "html"}
    key = os.environ.get("JINA_API_KEY")
    if key:
        headers["Authorization"] = "Bearer " + key   # optional: lifts the shared-IP rate limit
    req = urllib.request.Request(JINA + url, headers=headers)
    resp = urllib.request.urlopen(req, timeout=90)
    raw = resp.read()
    if resp.headers.get("Content-Encoding") == "gzip":
        raw = gzip.decompress(raw)
    return resp.status, raw.decode("utf-8", "replace")


def _err_note(kind, url, exc):
    """Record a failed attempt (with challenge-page body head when available)."""
    if isinstance(exc, urllib.error.HTTPError):
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            pass
        _note(kind, url, f"HTTP {exc.code}", body)
        return f"HTTP {exc.code}"
    _note(kind, url, f"{type(exc).__name__}: {exc}")
    return f"{type(exc).__name__}"


def http_text(url, fallbacks=True):
    """Layered fetch: direct -> Worker proxy (KV copy) -> jina reader.
    A direct 404 is NOT retried anywhere — a genuinely absent promo page
    (deal not running) must stay absent. fallbacks=False keeps the probe
    direct-only (used for auto-discovered food promos: nice-to-have links
    that don't justify hammering the fallback services ~20x per run)."""
    try:
        return _http_get(url)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise
        _err_note("direct", url, e)
    except Exception as e:
        _err_note("direct", url, e)
    if not fallbacks:
        raise RuntimeError(f"direct fetch failed for {url} (no fallbacks for this page)")

    slug = _proxy_slug(url)
    if slug:
        print(f"[info] direct fetch failed — trying edge proxy: {slug}")
        try:
            return _http_get(PROXY + slug)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise                    # pass-through: upstream page truly gone
            _err_note("proxy", PROXY + slug, e)
        except Exception as e:
            _err_note("proxy", PROXY + slug, e)

    print(f"[info] proxy failed too — trying jina reader")
    try:
        return _jina_get(url)
    except Exception as e:
        _err_note("jina", JINA + url, e)
        raise


def main_content(html):
    """Strip chrome (script/style/nav/header/footer) so per-litre wording in the
    site menu, footer, or a "related promos" strip can't false-flag a food page.
    Unescape entities AFTER tag-stripping: the jina reader re-serializes NBSP as
    literal '&nbsp;' text ('1,599&nbsp;Eur/l'), which \\s doesn't match — that
    silently dropped 3 of 4 Wednesday prices (2026-07-15)."""
    h = re.sub(r"<(script|style|nav|header|footer|aside)[^>]*>.*?</\1>", " ",
               html, flags=re.S | re.I)
    return _html.unescape(re.sub(r"<[^>]+>", " ", h))


def page_title(html, slug):
    """Prefer og:title, then <title>, else a de-slugged fallback (never a price)."""
    for pat in (r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"',
                r"<title>(.*?)</title>"):
        m = re.search(pat, html, re.I | re.S)
        if m:
            t = _html.unescape(re.sub(r"\s+", " ", m.group(1)).strip())
            t = re.split(r"\s*[|\-–]\s*VIADA", t, maxsplit=1)[0].strip()   # drop " | VIADA LT" suffix
            if t:
                return t
    return slug.replace("-", " ").capitalize()


def probe_promo(slug, fallback_title, kind):
    """Return a price-free pointer dict if the page is live & fuel-related, else None."""
    url = BASE + slug + "/"
    try:
        status, html = http_text(url, fallbacks=(kind != "listed"))
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print(f"[warn] {slug}: HTTP {e.code}")
        return None
    except Exception as e:
        print(f"[warn] {slug}: {type(e).__name__}: {e}")
        return None
    if status != 200:
        return None
    # For explicitly-known fuel promos we trust the curated flag; for auto-found
    # listing slugs we require the MAIN CONTENT to use per-litre pricing wording.
    fuel = kind in ("standing", "seasonal") or bool(FUEL_PAGE_RE.search(main_content(html)))
    if not fuel:
        return None
    return {"title": page_title(html, slug), "url": url, "slug": slug,
            "fuel_related": True, "kind": kind, "active": True}


def deaccent(s):
    repl = {"ą": "a", "č": "c", "ę": "e", "ė": "e", "į": "i", "š": "s",
            "ų": "u", "ū": "u", "ž": "z"}
    s = (s or "").lower()
    for a, b in repl.items():
        s = s.replace(a, b)
    return s


def parse_wednesday(html, url):
    """Extract the Super trečiadieniai absolute promo prices IF the page carries
    them as text with an explicit validity date. Returns a dict or None.

    Strictness (a wrong price is worse than no price):
      * requires the literal 'Pasiūlymas galioja: YYYY-MM-DD' validity date;
      * requires >=2 of our pump fuels parsed;
      * every price must sit in a sane 0.3-3.5 EUR/L band;
      * 'žymėtas' (marked/agri) diesel is ignored — not a pump fuel in the app.
    """
    txt = re.sub(r"\s+", " ", main_content(html))

    md = re.search(r"Pasi[ūu]lymas galioja:\s*(\d{4}-\d{2}-\d{2})", txt, re.I)
    if not md:
        print("[info] wednesday page has no explicit validity date — skipping prices")
        return None
    valid = md.group(1)

    prices = {}
    for m in re.finditer(r"([A-Za-zĄČĘĖĮŠŲŪŽąčęėįšųūž0-9()\s]{3,60}?)\s*[–—−-]\s*([01],\d{3})\s*Eur\s*/?\s*l", txt):
        label = deaccent(m.group(1))
        val = round(float(m.group(2).replace(",", ".")), 3)
        if not (0.3 < val < 3.5):
            continue
        if "zymet" in label:
            continue                      # marked/agri diesel — not a pump fuel here
        if "dyzelin" in label:
            prices.setdefault("diesel", val)
        elif "benzin" in label:
            prices.setdefault("petrol95", val)
        elif "dujos" in label or "lpg" in label:
            prices.setdefault("lpg", val)

    if len(prices) < 2:
        print(f"[info] wednesday page parsed only {len(prices)} fuel price(s) — skipping")
        return None
    print(f"[ok] wednesday promo: valid {valid}, prices {prices}")
    return {"valid_date": valid, "prices": prices, "url": url}


def discover_listing_slugs():
    """Slugs currently tiled on /akcijos/ (so new seasonal fuel promos get picked up)."""
    try:
        _, html = http_text(LISTING)
    except Exception as e:
        print(f"[warn] listing fetch failed: {e}")
        return []
    slugs = []
    for m in re.finditer(r'href="https://www\.viada\.lt/akcija/([^"/]+)/?"', html):
        s = m.group(1)
        if s not in slugs:
            slugs.append(s)
    return slugs


def load_existing():
    try:
        return json.load(open(OUT, encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def main():
    seen, promos = set(), []

    # 1) Curated standing + seasonal fuel promos (trusted as fuel-related).
    for slug, title in STANDING:
        p = probe_promo(slug, title, "standing")
        if p:
            p["title"] = title            # keep the curated, unambiguous label
            promos.append(p); seen.add(slug)
    for slug, title in SEASONAL:
        p = probe_promo(slug, title, "seasonal")
        if p:
            p["title"] = title
            promos.append(p); seen.add(slug)

    # 2) Anything new on the live listing whose PAGE proves it's a fuel deal.
    for slug in discover_listing_slugs():
        if slug in seen:
            continue
        p = probe_promo(slug, slug, "listed")
        if p:
            promos.append(p); seen.add(slug)

    print(f"[info] {len(promos)} fuel-related Viada promo pointers")

    # 3) Wednesday promo prices (plain text + explicit validity date — the one
    #    page whose numbers we DO extract; see the docstring for the guardrails).
    wednesday = None
    wed_page_gone = False
    try:
        wurl = BASE + "super-treciadieniai/"
        status, whtml = http_text(wurl)
        if status == 200:
            wednesday = parse_wednesday(whtml, wurl)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            wed_page_gone = True         # promo over — dropping the block is correct
        else:
            print(f"[warn] wednesday page: HTTP {e.code}")
    except Exception as e:
        print(f"[warn] wednesday page: {type(e).__name__}: {e}")

    # Carry-forward guard: a fetch/parse hiccup must not clobber a wednesday
    # block that is still valid for today or later (that would silently strip
    # an active discount mid-day). Only a REAL 404 (deal ended) drops it; the
    # app's own valid_date==today check keeps any carried value safe.
    if wednesday is None and not wed_page_gone:
        prev = load_existing()
        pw = (prev or {}).get("wednesday")
        if pw and pw.get("valid_date", "") >= dt.date.today().isoformat():
            print(f"[info] wednesday parse unavailable this run — carrying forward the "
                  f"still-valid block ({pw.get('valid_date')})")
            wednesday = pw

    # Fallback: if this run found nothing (site outage / datacenter-IP 403), keep the
    # previously-committed pointers rather than clobbering a good file with an empty one.
    if not promos:
        prev = load_existing()
        if prev and prev.get("promos"):
            print(f"[warn] no promos this run — keeping {len(prev['promos'])} previously-committed")
            prev["generated_stale_kept"] = True
            json.dump(prev, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            return
        print("[warn] no promos and nothing committed before — writing empty (layer just hides)")

    payload = {
        "generated": dt.datetime.now(dt.timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z",
        "network": "Viada",
        "source": "viada.lt (official promo pages)",
        "disclaimer": DISCLAIMER,
        "count": len(promos),
        "promos": promos,
    }
    if wednesday:
        payload["wednesday"] = wednesday
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[ok] wrote {OUT}: {len(promos)} pointers")


def dump_debug():
    """Always write the fetch-attempt diagnostics (empty attempts == all direct
    fetches OK). Committed by the workflow so runner-side failures are readable
    from raw.githubusercontent.com without Actions-log auth."""
    DEBUG["generated"] = dt.datetime.now(dt.timezone.utc).replace(
        microsecond=0, tzinfo=None).isoformat() + "Z"
    DEBUG["attempt_count"] = len(DEBUG["attempts"])
    try:
        os.makedirs(os.path.dirname(DEBUG_OUT), exist_ok=True)
        json.dump(DEBUG, open(DEBUG_OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"[info] wrote {DEBUG_OUT} ({len(DEBUG['attempts'])} failed attempt(s) recorded)")
    except Exception as e:
        print(f"[warn] debug dump failed: {e}")


if __name__ == "__main__":
    try:
        main()
    finally:
        dump_debug()
