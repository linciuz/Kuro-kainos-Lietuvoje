#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Open-data endpoints + RSS feed + the human page that documents them.

WHY THIS EXISTS
---------------
Measured 2026-08-05: fuelis.lt has ZERO inbound links from anywhere on the web,
and Search Console shows most pages never crawled ("no referring page"). That is
the real bottleneck — not markup, not sitemaps, both of which are already
correct. New domains get crawl budget in proportion to the signals pointing at
them, and we have none.

You cannot honestly manufacture links. What you CAN do is make something worth
linking to. In Lithuania there is currently no free, machine-readable, daily
feed of pump prices: LEA publishes a portal and a Power BI dashboard, neither of
which you can point a script at without reverse-engineering it (we did; it took
weeks). A documented JSON/CSV endpoint is the kind of thing developers,
journalists, students, Wikipedia editors and hobby-bot authors cite BY URL —
which is exactly the signal that is missing.

It also serves the "get onto Discord/Reddit" goal in the one way that lasts: a
Discord or Telegram bot that posts daily prices needs a stable endpoint, and
bots credit their source. That is distribution that keeps working after the
launch post scrolls off the front page.

WHAT IT GENERATES
  api/prices.json    full station-level dataset, documented stable schema
  api/prices.csv     same, for spreadsheets and journalists
  api/summary.json   ~1 KB national averages + cheapest — what a bot actually wants
  api/history.json   daily national averages since 2026-04-08
  feed.xml           RSS: one item per price date (aggregators, readers, IFTTT)
  atviri-duomenys.html   the page humans land on and link to

LICENSING — stated carefully on purpose. The prices are LEA's public data; we do
not own them and therefore do not licence them. What we offer freely is the
COMPILATION: cleaned, geocoded, deduplicated, in a stable schema. Anyone may use
it; we ask for attribution and require crediting LEA as the origin. Claiming a
CC licence over someone else's public data would be both wrong and a liability.
"""

import csv
import datetime as dt
import io
import json
import os
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

SITE = "https://fuelis.lt"
API_DIR = "api"
STATIONS = os.path.join("data", "stations.json")
HISTORY = os.path.join("data", "price_history.json")
FUELS = ("petrol95", "diesel", "lpg")
FUEL_LT = {"petrol95": "Benzinas 95", "diesel": "Dyzelinas", "lpg": "Dujos (LPG)"}
LEA = "https://degalukainos.ena.lt/"
ATTRIB = ("Duomenys: Lietuvos energetikos agentura (LEA). "
          "Rinkinys: Fuelis (https://fuelis.lt).")

# Only these station fields go into the public payload. An explicit allow-list,
# not a blanket dump: internal bookkeeping (coord_source, approx, price_src)
# would become a schema promise the moment someone parsed it.
PUBLIC_FIELDS = ("network", "address", "municipality", "lat", "lon",
                 "petrol95", "diesel", "lpg", "price_updated")


def _w(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    open(path, "w", encoding="utf-8", newline="\n").write(text)
    print(f"[opendata] wrote {path} ({len(text.encode('utf-8')):,} bytes)")


def esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def load():
    d = json.load(open(STATIONS, encoding="utf-8"))
    return d, d.get("updated"), d.get("stations") or []


def public_rows(stations):
    out = []
    for s in stations:
        if not any(s.get(f) for f in FUELS):
            continue                      # registry-only stations: no price, no row
        out.append({k: s.get(k) for k in PUBLIC_FIELDS})
    out.sort(key=lambda r: (r["municipality"] or "", r["network"] or "", r["address"] or ""))
    return out


def cheapest(rows):
    """Cheapest station per fuel — the single most-asked question, precomputed so
    a consumer does not have to pull the 300 KB file to answer it."""
    best = {}
    for f in FUELS:
        priced = [r for r in rows if isinstance(r.get(f), (int, float))]
        if not priced:
            continue
        r = min(priced, key=lambda r: r[f])
        best[f] = {"price": r[f], "network": r["network"],
                   "address": r["address"], "municipality": r["municipality"]}
    return best


def build_prices_json(meta, updated, rows):
    doc = {
        "$schema_version": 1,
        "generated_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "price_date": updated,
        "currency": "EUR",
        "unit": "EUR per litre",
        "fuels": list(FUELS),
        "country": "LT",
        "source": "Lietuvos energetikos agentura (LEA)",
        "source_url": LEA,
        "compiled_by": SITE,
        "docs": f"{SITE}/atviri-duomenys.html",
        "attribution": ATTRIB,
        "terms": ("Free to use, including commercially. Please credit LEA as the "
                  "data source and link to https://fuelis.lt. Prices are LEA's "
                  "public data; Fuelis provides the cleaned, geocoded compilation. "
                  "No warranty - always confirm at the pump."),
        "summary": meta.get("summary") or {},
        "cheapest": cheapest(rows),
        "count": len(rows),
        "stations": rows,
    }
    _w(os.path.join(API_DIR, "prices.json"),
       json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    return doc


def build_prices_csv(updated, rows):
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["network", "address", "municipality", "lat", "lon",
                "petrol95_eur", "diesel_eur", "lpg_eur", "price_updated", "price_date"])
    for r in rows:
        w.writerow([r["network"], r["address"], r["municipality"], r["lat"], r["lon"],
                    r["petrol95"], r["diesel"], r["lpg"], r["price_updated"], updated])
    _w(os.path.join(API_DIR, "prices.csv"), buf.getvalue())


def build_summary_json(meta, updated, rows):
    """Deliberately tiny (~1 KB). A Discord/Telegram bot polling this every hour
    costs nothing; pointing it at prices.json would move 300 KB for 9 numbers."""
    doc = {
        "$schema_version": 1,
        "price_date": updated,
        "currency": "EUR",
        "stations_with_prices": len(rows),
        "national": meta.get("summary") or {},
        "cheapest": cheapest(rows),
        "source": "Lietuvos energetikos agentura (LEA)",
        "source_url": LEA,
        "compiled_by": SITE,
        "docs": f"{SITE}/atviri-duomenys.html",
        "attribution": ATTRIB,
    }
    _w(os.path.join(API_DIR, "summary.json"),
       json.dumps(doc, ensure_ascii=False, indent=1) + "\n")


def build_history_json():
    try:
        hist = (json.load(open(HISTORY, encoding="utf-8")).get("history") or [])
    except (OSError, json.JSONDecodeError):
        print("[opendata] no price history yet — skipping api/history.json")
        return []
    doc = {
        "$schema_version": 1,
        "description": "Daily national min/avg/max pump prices in Lithuania, EUR per litre.",
        "currency": "EUR",
        "source": "Lietuvos energetikos agentura (LEA)",
        "source_url": LEA,
        "compiled_by": SITE,
        "docs": f"{SITE}/atviri-duomenys.html",
        "attribution": ATTRIB,
        "days": len(hist),
        "history": hist,
    }
    _w(os.path.join(API_DIR, "history.json"),
       json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    return hist


def rfc822(date_str, hour=10):
    """RSS wants RFC-822. LEA publishes ~10:00 Lithuania; +03:00 is EEST, which
    is what Lithuania is on for the months this feed covers."""
    try:
        d = dt.date.fromisoformat(date_str)
    except (TypeError, ValueError):
        d = dt.date.today()
    return dt.datetime.combine(d, dt.time(hour), tzinfo=dt.timezone(dt.timedelta(hours=3))) \
             .strftime("%a, %d %b %Y %H:%M:%S %z")


def fmt(v):
    return f"{v:.3f}".replace(".", ",") if isinstance(v, (int, float)) else "—"


def build_feed(updated, rows, hist):
    """One item per price date, newest 30. Real content in each item (the day's
    numbers), because a feed of bare 'prices updated' lines is noise nobody keeps
    subscribed to."""
    by_date = {h.get("date"): h for h in hist if h.get("date")}
    if updated and updated not in by_date:
        # Today's snapshot may not be in the history file yet (append_history
        # runs on its own schedule) — build it from what we just published.
        summ = {}
        for f in FUELS:
            vals = [r[f] for r in rows if isinstance(r.get(f), (int, float))]
            if vals:
                summ[f] = {"min": min(vals), "avg": round(sum(vals) / len(vals), 3), "max": max(vals)}
        by_date[updated] = dict(date=updated, **summ)

    items = []
    for date in sorted(by_date, reverse=True)[:30]:
        h = by_date[date]
        lines = [f"<li><strong>{FUEL_LT[f]}</strong>: vid. {fmt(h.get(f, {}).get('avg'))} €/l "
                 f"(nuo {fmt(h.get(f, {}).get('min'))} iki {fmt(h.get(f, {}).get('max'))} €/l)</li>"
                 for f in FUELS if h.get(f)]
        desc = (f"<p>Oficialios LEA degalų kainos Lietuvoje {date}:</p><ul>{''.join(lines)}</ul>"
                f'<p><a href="{SITE}/">Žiūrėti visas degalines žemėlapyje</a> · '
                f'<a href="{SITE}/kainos/">kainos pagal savivaldybę</a></p>')
        cheap = " · ".join(f"{FUEL_LT[f]} {fmt(h[f]['avg'])} €/l" for f in FUELS if h.get(f))
        items.append(f"""  <item>
    <title>Degalų kainos {date}: {esc(cheap)}</title>
    <link>{SITE}/</link>
    <guid isPermaLink="false">{SITE}/#prices-{date}</guid>
    <pubDate>{rfc822(date)}</pubDate>
    <description>{esc(desc)}</description>
  </item>""")

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
  <title>Fuelis — degalų kainos Lietuvoje</title>
  <link>{SITE}/</link>
  <atom:link href="{SITE}/feed.xml" rel="self" type="application/rss+xml"/>
  <description>Oficialios LEA degalų kainos (benzinas 95, dyzelinas, dujos) Lietuvos degalinėse — kasdienė santrauka.</description>
  <language>lt</language>
  <copyright>{esc(ATTRIB)}</copyright>
  <lastBuildDate>{rfc822(updated)}</lastBuildDate>
  <ttl>360</ttl>
{chr(10).join(items)}
</channel>
</rss>
"""
    _w("feed.xml", xml)


def build_docs_page(updated, rows):
    """The page people actually link to. Written as documentation, not
    marketing: a developer deciding whether to depend on this needs the schema,
    the update cadence, the licence and the caveats, in that order."""
    n = len(rows)
    sample = json.dumps({k: (rows[0].get(k) if rows else None) for k in PUBLIC_FIELDS},
                        ensure_ascii=False, indent=1) if rows else "{}"
    html = f"""<!DOCTYPE html>
<html lang="lt">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Atviri duomenys — degalų kainų API | Fuelis</title>
<meta name="description" content="Nemokama, atvira Lietuvos degalų kainų API: JSON ir CSV, {n} degalinių, atnaujinama kasdien. Oficialūs LEA duomenys, paruošti programuotojams.">
<link rel="canonical" href="{SITE}/atviri-duomenys.html">
<meta name="robots" content="index, follow, max-snippet:-1">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Fuelis">
<meta property="og:title" content="Atviri duomenys — Lietuvos degalų kainų API">
<meta property="og:description" content="Nemokama JSON/CSV degalų kainų API: {n} degalinių, atnaujinama kasdien. Oficialūs LEA duomenys.">
<meta property="og:url" content="{SITE}/atviri-duomenys.html">
<meta property="og:image" content="{SITE}/og-image.png">
<meta name="twitter:card" content="summary_large_image">
<link rel="alternate" type="application/rss+xml" title="Fuelis — degalų kainos" href="{SITE}/feed.xml">
<script type="application/ld+json">
{{
 "@context": "https://schema.org",
 "@type": "Dataset",
 "name": "Lietuvos degalų kainos (Fuelis atviri duomenys)",
 "description": "Kasdien atnaujinamos oficialios LEA degalų kainos (benzinas 95, dyzelinas, dujos) {n} Lietuvos degalinių, su koordinatėmis. JSON ir CSV formatais.",
 "url": "{SITE}/atviri-duomenys.html",
 "keywords": ["degalų kainos", "kuro kainos", "Lietuva", "benzinas", "dyzelinas", "LPG", "open data"],
 "isAccessibleForFree": true,
 "temporalCoverage": "2026-04-08/..",
 "spatialCoverage": {{ "@type": "Place", "name": "Lietuva" }},
 "creator": {{ "@type": "Organization", "name": "Fuelis", "url": "{SITE}/" }},
 "includedInDataCatalog": {{ "@type": "DataCatalog", "name": "Fuelis" }},
 "distribution": [
  {{ "@type": "DataDownload", "encodingFormat": "application/json", "contentUrl": "{SITE}/api/prices.json" }},
  {{ "@type": "DataDownload", "encodingFormat": "text/csv", "contentUrl": "{SITE}/api/prices.csv" }},
  {{ "@type": "DataDownload", "encodingFormat": "application/json", "contentUrl": "{SITE}/api/summary.json" }},
  {{ "@type": "DataDownload", "encodingFormat": "application/json", "contentUrl": "{SITE}/api/history.json" }}
 ]
}}
</script>
<style>
 *{{margin:0;padding:0;box-sizing:border-box}}
 body{{font:16px/1.65 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#22303f;background:#f4f6fa;padding:18px}}
 .wrap{{max-width:860px;margin:0 auto;background:#fff;border-radius:16px;padding:26px 24px 34px;box-shadow:0 8px 30px rgba(20,40,80,.09)}}
 h1{{font-size:1.7rem;line-height:1.25;margin:.2em 0 .35em}}
 h2{{font-size:1.16rem;margin:1.7em 0 .5em;padding-top:.5em;border-top:1px solid #e6ebf3}}
 h3{{font-size:1rem;margin:1.2em 0 .35em}}
 p,li{{margin-bottom:.6em}} ul,ol{{padding-left:1.3em}}
 a{{color:#1560c4}}
 .bc{{font-size:.85rem;color:#6b7a8d;margin-bottom:.8em}}
 .lead{{font-size:1.05rem;color:#3d4c5e}}
 code{{background:#eef2f8;padding:.12em .38em;border-radius:5px;font-size:.9em;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}}
 pre{{background:#1d2632;color:#e7edf6;padding:14px 16px;border-radius:10px;overflow-x:auto;font-size:.83rem;line-height:1.5;margin:.6em 0 1em}}
 pre code{{background:none;padding:0;color:inherit;font-size:inherit}}
 table{{width:100%;border-collapse:collapse;margin:.5em 0 1em;font-size:.92rem;display:block;overflow-x:auto}}
 th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid #e6ebf3;vertical-align:top}}
 th{{background:#f7f9fc;font-weight:600;white-space:nowrap}}
 .ep{{font-family:ui-monospace,Menlo,monospace;font-size:.86rem;white-space:nowrap}}
 .note{{background:#f0f6ff;border-left:4px solid #1560c4;padding:11px 14px;border-radius:0 8px 8px 0;margin:1em 0;font-size:.94rem}}
 .warn{{background:#fff6e8;border-left-color:#e0891a}}
 footer{{margin-top:2em;padding-top:1em;border-top:1px solid #e6ebf3;font-size:.88rem;color:#6b7a8d}}
</style>
</head>
<body>
<div class="wrap">
<p class="bc"><a href="{SITE}/">Fuelis</a> › Atviri duomenys</p>
<h1>Atviri degalų kainų duomenys (API)</h1>
<p class="lead">Nemokama, atvira Lietuvos degalų kainų API. <strong>{n} degalinių</strong> su kainomis ir koordinatėmis,
atnaujinama kasdien (paskutinis rinkinys: <strong>{updated}</strong>). JSON ir CSV. Be registracijos, be raktų, be limitų.</p>

<div class="note">Kodėl tai egzistuoja: LEA kainas skelbia viešai, bet portale ir „Power BI“ skydelyje —
iš jų programiškai pasiimti duomenis nėra paprasta. Čia tie patys duomenys pateikiami
stabilia, dokumentuota schema, kad juos galėtum tiesiog <code>fetch</code>-inti.</div>

<h2>Galiniai taškai</h2>
<table>
<tr><th>URL</th><th>Kas tai</th><th>Dydis</th></tr>
<tr><td class="ep"><a href="{SITE}/api/summary.json">/api/summary.json</a></td><td>Šalies vidurkiai + pigiausia degalinė kiekvienam kurui. <strong>Pradėk nuo šito</strong> — botams ir valdikliams to paprastai užtenka.</td><td>~1 KB</td></tr>
<tr><td class="ep"><a href="{SITE}/api/prices.json">/api/prices.json</a></td><td>Visos degalinės: tinklas, adresas, savivaldybė, koordinatės, trys kainos, atnaujinimo laikas.</td><td>~{max(1, n * 230 // 1024)} KB</td></tr>
<tr><td class="ep"><a href="{SITE}/api/prices.csv">/api/prices.csv</a></td><td>Tas pats CSV — „Excel“, „Google Sheets“, R, pandas.</td><td>~{max(1, n * 130 // 1024)} KB</td></tr>
<tr><td class="ep"><a href="{SITE}/api/history.json">/api/history.json</a></td><td>Dienos šalies min./vid./maks. nuo 2026-04-08 — grafikams ir tendencijoms.</td><td>~30 KB</td></tr>
<tr><td class="ep"><a href="{SITE}/feed.xml">/feed.xml</a></td><td>RSS: kasdienė kainų santrauka.</td><td>—</td></tr>
</table>
<p>Visi failai atiduodami su <code>Access-Control-Allow-Origin: *</code>, tad juos gali kviesti tiesiai iš naršyklės.</p>

<h2>Pavyzdžiai</h2>
<h3>Šiandienos vidurkiai (curl)</h3>
<pre><code>curl -s {SITE}/api/summary.json | jq '.national'</code></pre>
<h3>Pigiausias dyzelinas (JavaScript)</h3>
<pre><code>const r = await fetch("{SITE}/api/summary.json").then(r =&gt; r.json());
console.log(r.cheapest.diesel);
// {{ price: 1.94, network: "…", address: "…", municipality: "…" }}</code></pre>
<h3>Į „pandas“ (Python)</h3>
<pre><code>import pandas as pd
df = pd.read_csv("{SITE}/api/prices.csv")
print(df.groupby("municipality")["diesel_eur"].mean().sort_values().head())</code></pre>

<h2>Laukai (<code>stations[]</code>)</h2>
<table>
<tr><th>Laukas</th><th>Tipas</th><th>Paaiškinimas</th></tr>
<tr><td><code>network</code></td><td>string</td><td>Įmonė / tinklas, kaip nurodyta LEA.</td></tr>
<tr><td><code>address</code></td><td>string</td><td>Degalinės adresas.</td></tr>
<tr><td><code>municipality</code></td><td>string</td><td>Savivaldybė (pvz. <code>Kauno m. sav.</code>).</td></tr>
<tr><td><code>lat</code>, <code>lon</code></td><td>number</td><td>WGS-84. Daugumai — oficialios operatoriaus koordinatės; likusios geokoduotos.</td></tr>
<tr><td><code>petrol95</code>, <code>diesel</code>, <code>lpg</code></td><td>number | null</td><td>EUR už litrą. <code>null</code> = degalinė to kuro neteikia arba kainos nepateikė.</td></tr>
<tr><td><code>price_updated</code></td><td>ISO 8601</td><td>Kada LEA įraše paskutinį kartą fiksuota ši kaina.</td></tr>
</table>
<pre><code>{esc(sample)}</code></pre>

<h2>Atnaujinimo dažnis</h2>
<p>LEA skelbia apie <strong>10:00</strong> Lietuvos laiku darbo dienomis. Mūsų konvejeris tikrina dažniau ir
paskelbia iškart, kai atsiranda naujesni duomenys, todėl <code>price_date</code> paprastai pasikeičia
per kelias minutes nuo LEA paskelbimo. Savaitgaliais ir per šventes LEA neskelbia — tuomet
lieka paskutinės darbo dienos kainos.</p>

<h2>Licencija ir nuorodos</h2>
<p>Kainos yra <strong>LEA vieši duomenys</strong> — jos mums nepriklauso, todėl jų ir nelicencijuojame.
Laisvai (taip pat ir komerciškai) siūlome <em>rinkinį</em>: išvalytą, geokoduotą, stabilios schemos.</p>
<ul>
<li>Naudok kam nori — programai, tyrimui, straipsniui, botui.</li>
<li>Nurodyk pirminį šaltinį: <strong>Lietuvos energetikos agentūra (LEA)</strong>, <a href="{LEA}" rel="nofollow">degalukainos.ena.lt</a>.</li>
<li>Būtume dėkingi už nuorodą į <a href="{SITE}/">fuelis.lt</a>.</li>
</ul>
<div class="note warn"><strong>Be garantijų.</strong> Duomenys teikiami tokie, kokie yra. Kainos degalinėje
gali skirtis nuo paskelbtų LEA. Prieš pildamas — pasitikrink kolonėlėje.</div>

<h2>Klausimai</h2>
<p>Radai klaidą, reikia kito formato ar lauko? Rašyk per <a href="{SITE}/">fuelis.lt</a> kontaktų formą.
Jei kuri kažką su šiais duomenimis — parodyk, mielai pasidalinsime.</p>

<footer>
<p><a href="{SITE}/">← Fuelis: degalų kainų žemėlapis</a> · <a href="{SITE}/kainos/">Kainos pagal savivaldybę</a> · <a href="{SITE}/privatumas.html">Privatumas</a></p>
<p>{esc(ATTRIB)}</p>
</footer>
</div>
</body>
</html>
"""
    _w("atviri-duomenys.html", html)


def main():
    meta, updated, stations = load()
    rows = public_rows(stations)
    if not rows:
        print("::warning::[opendata] no priced stations — refusing to publish empty endpoints.")
        return 1
    build_prices_json(meta, updated, rows)
    build_prices_csv(updated, rows)
    build_summary_json(meta, updated, rows)
    hist = build_history_json()
    build_feed(updated, rows, hist)
    build_docs_page(updated, rows)
    print(f"[opendata] OK — {len(rows)} priced stations, price_date={updated}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
