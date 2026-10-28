#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Neste "Nuolaidadienis" (Wednesday discount day) announcement fetcher.

The old /lt/nuolaidadienis URL redirects to a removed page (verified 2026-10-06).
Use the official current offers index. A historical announcement had PLAIN TEXT
with the specific date (verified 2026-07-08):
    "Tik šį trečiadienį, liepos 8 d. - 7 ct nuolaida su NESTE programėle ir
     nuolaidų kortele visiems degalams"
That is Neste's own officially-announced figure, so we extract it under strict
validation (explicit day-month date + a sane 1-30 ct band + the word
'trečiadien'). The app applies it ONLY on the stated date as the Neste loyalty
discount (with the app/card — consistent with the opt-in discounts feature),
reverting to the user's configured cents any other day. The verified empty
index or reviewed non-Wednesday offers establish no announcement. Unknown markup or
fetch failure preserves the old success stamp and fails for review.

OUTPUT  data/sources/neste_promo.json
  {"generated","source_url","status":"announced","valid_date","cents"}
  {"generated","source_url","status":"no_announcement"} # successful empty listing
"""

import datetime as dt
import gzip
import html as _html
from html.parser import HTMLParser
import json
import os
import re
import ssl
import sys
import urllib.request
from zoneinfo import ZoneInfo

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

URL = "https://www.neste.lt/privatiems/klientu-naudos/specialus-pasiulymai"
VILNIUS = ZoneInfo("Europe/Vilnius")
OUT = os.path.join("data", "sources", "neste_promo.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"

# Lithuanian month names (genitive, as used in dates) -> month number.
LT_MONTHS = {
    "sausio": 1, "vasario": 2, "kovo": 3, "balandžio": 4, "gegužės": 5,
    "birželio": 6, "liepos": 7, "rugpjūčio": 8, "rugsėjo": 9, "spalio": 10,
    "lapkričio": 11, "gruodžio": 12,
}


def fetch(url):
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "lt"})
    with urllib.request.urlopen(req, timeout=40, context=ctx) as resp:
        if resp.geturl().rstrip("/") != url.rstrip("/"):
            raise RuntimeError(f"unexpected offers-page redirect: {resp.geturl()}")
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    return raw.decode("utf-8", "replace")


def text_content(html):
    txt = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", txt))).strip()


class _ListingImages(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.images = []
        self.unsupported_media = False

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            self.images.append(attrs)
        elif tag in ("picture", "source"):
            self.unsupported_media = True


def reviewed_images_only(markup, count, path_pattern):
    """Check every image a browser can choose, including responsive candidates."""
    parser = _ListingImages()
    parser.feed(markup)
    parser.close()
    if len(parser.images) != count or parser.unsupported_media:
        return False
    for attrs in parser.images:
        sources = [value for name, value in attrs if name == "src"]
        if len(sources) != 1 or not sources[0]:
            return False
        for name, srcset in attrs:
            if name != "srcset":
                continue
            if not srcset:
                return False
            for candidate in _html.unescape(srcset).split(','):
                parts = candidate.split()
                if not parts:
                    return False
                sources.append(parts[0])
        if any(not re.fullmatch(path_pattern, _html.unescape(src).split('?', 1)[0]) for src in sources):
            return False
    return True


def confirmed_no_announcement(html):
    """Recognize reviewed listings without a dated Wednesday fuel discount.

    Captured 2026-10-06: the index has its own heading/intro and its content
    body contains only the app-download section. The October 28 Rimi card is
    a grocery voucher, not a fuel discount. Unknown text, links or images
    still require review, including new image-only offers.
    """
    main = re.search(r"<main\b[^>]*>(.*?)</main>", html, re.S | re.I)
    if not main:
        return False
    main = main.group(1)
    h1 = re.search(r"<h1\b[^>]*>(.*?)</h1>", main, re.S | re.I)
    if not h1 or text_content(h1.group(1)) != "Specialūs pasiūlymai":
        return False
    if "Visi dabartiniai NESTE pasiūlymai ir akcijos vienoje vietoje." not in text_content(main):
        return False
    body = re.search(r'<div\b[^>]*class="[^"]*ContentBody_body__[^"]*"[^>]*>(.*)', main, re.S)
    if not body:
        return False
    body = body.group(1)
    expected = "Atsisiųskite Neste programėlę Atsisiųsti iš „App Store“ Atsisiųsti iš „Google Play“"
    rimi_path = "/privatiems/klientu-naudos/specialus-pasiulymai/rimi-gimtadienis"
    cards = re.findall(r'<a\b[^>]*\bhref="' + re.escape(rimi_path) + r'"[^>]*>.*?</a>', body, re.S | re.I)
    if cards:
        if len(cards) != 1:
            return False
        card = cards[0]
        if text_content(card) != (
            "Pilkis NESTE ir gauk RIMI dovanų! Kartu su mūsų draugais RIMI jų gimtadienio "
            "proga paruošėme specialų pasiūlymą „Mano Rimi“ lojalumo programos nariams! Daugiau"
        ):
            return False
        if re.findall(r'<a\b[^>]*\bhref="([^"]+)"', card) != [rimi_path]:
            return False
        image_path = (
            "https://images.ctfassets.net/guhb48q98ydd/6JF17RLqixZcBAGqsgE8fa/"
            "a41956b2139be3225f04c5f2f6288cd1/RIMI_reward_special_offer_page.jfif"
        )
        if not reviewed_images_only(card, 1, re.escape(image_path)):
            return False
        body = body.replace(card, "", 1)
        expected = "Šiuo metu vykstančios akcijos " + expected
    if text_content(body) != expected:
        return False
    links = [_html.unescape(x) for x in re.findall(r'<a\b[^>]*href="([^"]+)"', body)]
    if len(links) != 2 or not any(x.startswith("https://apps.apple.com/") for x in links) or not any(x.startswith("https://play.google.com/store/apps/") for x in links):
        return False
    return reviewed_images_only(body, 3,
        r"https://images\.ctfassets\.net/[^?]+/(?:AppStoreButton[^/?]*\.png|GooglePlayButton[^/?]*\.png|lataa-neste-appi-qr-koodi\.png)")


def parse(html, today=None):
    """Return (valid_date_iso, cents) or (None, None)."""
    txt = text_content(html)

    # "šį trečiadienį, liepos 8 d. - 7 ct nuolaida"
    m = re.search(
        r"trečiadien\w*[,\s]+(\w+)\s+(\d{1,2})\s*d\.?\s*[-–—]?\s*(\d{1,2}(?:[.,]\d)?)\s*ct\s*nuolaid",
        txt, re.I)
    if not m:
        print("[info] no dated Wednesday discount found on the page")
        return None, None
    month = LT_MONTHS.get(m.group(1).lower())
    day = int(m.group(2))
    cents = float(m.group(3).replace(",", "."))
    if not month or not (1 <= day <= 31) or not (1 <= cents <= 30):
        print(f"[warn] parsed values out of band: month={m.group(1)} day={day} cents={cents}")
        return None, None

    # Year: the promo is always same-week; handle the Dec/Jan wrap.
    today = today or dt.datetime.now(VILNIUS).date()
    year = today.year
    if month == 1 and today.month == 12:
        year += 1
    elif month == 12 and today.month == 1:
        year -= 1
    valid = dt.date(year, month, day).isoformat()
    return valid, cents


def announcement(html, today=None):
    valid, cents = parse(html, today)
    if valid and cents:
        return {"status": "announced", "valid_date": valid, "cents": cents}
    if confirmed_no_announcement(html):
        return {"status": "no_announcement"}
    raise RuntimeError("unrecognized Neste offers listing: no dated promo and no reviewed no-announcement listing")


def load_existing():
    try:
        with open(OUT, encoding="utf-8") as source:
            return json.load(source)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def main():
    print(f"[info] fetching {URL}")
    payload = {
        "generated": dt.datetime.now(dt.timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z",
        "source_url": URL,
    }
    try:
        html = fetch(URL)
        payload.update(announcement(html))
        if payload["status"] == "announced":
            print(f"[ok] Neste nuolaidadienis: −{payload['cents']} ct/L on {payload['valid_date']}")
        else:
            print("[ok] official offers listing checked: no dated Wednesday announcement")
    except Exception as e:
        # Carry-forward (the Viada pattern): a fetch hiccup must not clobber a
        # promo that is still valid today+, and must NOT re-stamp `generated` —
        # a fresh stamp on a failure run blinds verify_sources' rot rule.
        prev = load_existing()
        if prev:
            print(f"[warn] fetch failed: {type(e).__name__}: {e} — keeping previous generated stamp; stale-keep marked")
            prev["generated_stale_kept"] = True
            with open(OUT, "w", encoding="utf-8") as target:
                json.dump(prev, target, ensure_ascii=False, indent=2)
            return 1
        print(f"[warn] fetch failed: {type(e).__name__}: {e} — no previous confirmed reading; not publishing a success")
        return 1

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as target:
        json.dump(payload, target, ensure_ascii=False, indent=2)
    print(f"[ok] wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
