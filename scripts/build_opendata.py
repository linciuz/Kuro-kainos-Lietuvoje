#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Open-data endpoints + RSS feed + the human page that documents them.

The documented compilation supports developers, researchers and readers.
It keeps row-level source attribution when a verified newer operator price
supplements LEA. API availability does not establish source-data licence rights.

WHAT IT GENERATES
  api/prices.json    full station-level dataset, documented stable schema
  api/prices.csv     same, for spreadsheets and journalists
  api/summary.json   ~1 KB national averages + cheapest — what a bot actually wants
  api/history.json   daily national averages since 2026-04-08
  feed.xml           RSS: one item per price date (aggregators, readers, IFTTT)
  atviri-duomenys.html   the page humans land on and link to

No licence over underlying LEA or operator prices is invented by this generator.
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
DATA_SOURCE = "LEA ir pažymėtos naujesnės operatorių kainos, kai pateiktos"
ATTRIB = ("Duomenys: Lietuvos energetikos agentura (LEA) ir pažymėti kainų operatoriai. "
          "Rinkinys: Fuelis (https://fuelis.lt).")

# Only these station fields go into the public payload. An explicit allow-list,
# not a blanket dump: internal bookkeeping (coord_source, approx)
# would become a schema promise the moment someone parsed it.
PUBLIC_FIELDS = ("network", "address", "municipality", "lat", "lon",
                 "petrol95", "diesel", "lpg", "price_updated", "price_src",
                 "display_municipality", "display_municipality_source",
                 "display_address", "display_address_source")


def _w(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    open(path, "w", encoding="utf-8", newline="\n").write(text)
    print(f"[opendata] wrote {path} ({len(text.encode('utf-8')):,} bytes)")


def esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def sz(path):
    """Real on-disk size for schema.org contentSize. Called only after the file
    has been written (build_docs_page runs last), so this never guesses."""
    try:
        b = os.path.getsize(path)
    except OSError:
        return ""
    return f"{b / 1024:.0f} KB" if b >= 1024 else f"{b} B"


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
        if r.get("display_address") and r.get("display_address_source"):
            best[f].update({k: r[k] for k in ("display_address", "display_address_source")})
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
        "source": DATA_SOURCE,
        "source_url": LEA,
        "compiled_by": SITE,
        "docs": f"{SITE}/atviri-duomenys.html",
        "attribution": ATTRIB,
        "terms": ("Fuelis offers its cleaned, geocoded compilation free to use, including "
                  "commercially. Credit the indicated source (LEA or marked operator) "
                  "and link to https://fuelis.lt. This is the compilation offer, not "
                  "a new licence over underlying source data. "
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
        "source": DATA_SOURCE,
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
        "source": DATA_SOURCE,
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
        desc = (f"<p>Degalų kainų rinkinio suvestinė Lietuvoje {date} "
                f"(LEA ir pažymėtų operatorių duomenys):</p><ul>{''.join(lines)}</ul>"
                f'<p><a href="{SITE}/">Žiūrėti visas degalines žemėlapyje</a> · '
                f'<a href="{SITE}/kainos/">kainos pagal savivaldybę</a></p>')
        cheap = " · ".join(f"{FUEL_LT[f]} {fmt(h[f]['avg'])} €/l" for f in FUELS if h.get(f))
        # Snapshots and row source times do not prove when a feed item was
        # published. RSS permits omitting pubDate; do not invent that time.
        items.append(f"""  <item>
    <title>Degalų kainos {date}: {esc(cheap)}</title>
    <link>{SITE}/</link>
    <guid isPermaLink="false">{SITE}/#prices-{date}</guid>
    <description>{esc(desc)}</description>
  </item>""")

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
  <title>Fuelis — degalų kainos Lietuvoje</title>
  <link>{SITE}/</link>
  <atom:link href="{SITE}/feed.xml" rel="self" type="application/rss+xml"/>
  <description>LEA duomenys ir naujesnės patikrintos operatorių kainos, kai pateiktos: benzinas 95, dyzelinas, dujos Lietuvos degalinėse.</description>
  <language>lt</language>
  <copyright>{esc(ATTRIB)}</copyright>
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
    diesel_sample = json.dumps(cheapest(rows).get("diesel"), ensure_ascii=False, indent=1)
    source_counts = {source: sum((row.get("price_src") or "unknown") == source for row in rows)
                     for source in sorted({row.get("price_src") or "unknown" for row in rows})}
    source_line = ", ".join(f"{esc(source)}: {count}" for source, count in source_counts.items())
    html = f"""<!DOCTYPE html>
<html lang="lt">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Atviri duomenys — degalų kainų API | Fuelis</title>
<meta name="description" content="Nemokama Lietuvos degalų kainų API: JSON ir CSV, {n} degalinių su kainomis, rinkinys {updated}. LEA ir pažymėti operatorių duomenys.">
<link rel="canonical" href="{SITE}/atviri-duomenys.html">
<meta name="robots" content="index, follow, max-snippet:-1">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Fuelis">
<meta property="og:title" content="Atviri duomenys — Lietuvos degalų kainų API">
<meta property="og:description" content="Nemokama JSON/CSV degalų kainų API: {n} degalinių su kainomis, {updated}. LEA ir pažymėti operatorių duomenys.">
<meta property="og:url" content="{SITE}/atviri-duomenys.html">
<meta property="og:image" content="{SITE}/og-image.png">
<meta name="twitter:card" content="summary_large_image">
<link rel="alternate" type="application/rss+xml" title="Fuelis — degalų kainos" href="{SITE}/feed.xml">
<script type="application/ld+json">
{{
 "@context": "https://schema.org",
 "@type": "Dataset",
 "name": "Lietuvos degalų kainos (Fuelis atviri duomenys)",
 "description": "LEA ir pažymėtos naujesnės operatorių kainos: benzinas 95, dyzelinas, LPG, {n} Lietuvos degalinių su kainomis. JSON ir CSV, rinkinio data {updated}.",
 "url": "{SITE}/atviri-duomenys.html",
 "keywords": ["degalų kainos", "kuro kainos", "Lietuva", "benzinas", "dyzelinas", "LPG", "open data"],
 "isAccessibleForFree": true,
 "identifier": "{SITE}/atviri-duomenys.html",
 "sameAs": "https://github.com/linciuz/Kuro-kainos-Lietuvoje",
 "license": "{SITE}/atviri-duomenys.html#licencija",
 "temporalCoverage": "2026-04-08/..",
 "spatialCoverage": {{ "@type": "Place", "name": "Lietuva" }},
 "variableMeasured": [
  {{ "@type": "PropertyValue", "name": "Benzinas 95", "description": "Petrol RON 95 pump price", "unitText": "EUR/L" }},
  {{ "@type": "PropertyValue", "name": "Dyzelinas", "description": "Diesel pump price", "unitText": "EUR/L" }},
  {{ "@type": "PropertyValue", "name": "Dujos (LPG)", "description": "Autogas/LPG pump price", "unitText": "EUR/L" }}
 ],
 "creator": {{ "@type": "Organization", "name": "Fuelis", "url": "{SITE}/",
               "sameAs": "https://github.com/linciuz/Kuro-kainos-Lietuvoje" }},
 "includedInDataCatalog": {{ "@type": "DataCatalog", "name": "Fuelis" }},
 "distribution": [
  {{ "@type": "DataDownload", "name": "Visos degalinės (JSON)", "encodingFormat": "application/json", "contentUrl": "{SITE}/api/prices.json", "contentSize": "{sz('api/prices.json')}" }},
  {{ "@type": "DataDownload", "name": "Visos degalinės (CSV)", "encodingFormat": "text/csv", "contentUrl": "{SITE}/api/prices.csv", "contentSize": "{sz('api/prices.csv')}" }},
  {{ "@type": "DataDownload", "name": "Dienos santrauka (JSON)", "encodingFormat": "application/json", "contentUrl": "{SITE}/api/summary.json", "contentSize": "{sz('api/summary.json')}" }},
  {{ "@type": "DataDownload", "name": "Kainų istorija (JSON)", "encodingFormat": "application/json", "contentUrl": "{SITE}/api/history.json", "contentSize": "{sz('api/history.json')}" }}
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
<p class="lead">Nemokama, atvira Lietuvos degalų kainų API. <strong>{n} degalinių</strong> su paskelbtomis kainomis, adresais ir turimomis koordinatėmis.
Naujausios gautos darbo dienų kainos (rinkinio data: <strong>{updated}</strong>). JSON ir CSV. Be registracijos, be raktų, be limitų.</p>
<p>Kainų šaltiniai šiame rinkinyje: <strong>{source_line}</strong>. Pagrindas — LEA duomenys;
<code>price_src</code> pažymi naujesnę patikrintą operatoriaus kainą, kai ji naudojama.
Rinkinio data (<code>price_date</code>) ir atskiro kainos įrašo laikas (<code>price_updated</code>) yra skirtingi.</p>

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
</code></pre>
<p>Šio <strong>{updated}</strong> rinkinio tikras atsakymo pavyzdys:</p>
<pre><code>{esc(diesel_sample)}</code></pre>
<h3>Į „pandas“ (Python)</h3>
<pre><code>import pandas as pd
df = pd.read_csv("{SITE}/api/prices.csv")
print(df.groupby("municipality")["diesel_eur"].mean().sort_values().head())</code></pre>

<h2>Laukai (<code>stations[]</code>)</h2>
<table>
<tr><th>Laukas</th><th>Tipas</th><th>Paaiškinimas</th></tr>
<tr><td><code>network</code></td><td>string</td><td>Įmonė / tinklas, kaip nurodyta LEA.</td></tr>
<tr><td><code>address</code></td><td>string</td><td>Originalus degalinės adresas iš LEA; išsaugomas šaltinio įrašų tapatumui.</td></tr>
<tr><td><code>municipality</code></td><td>string</td><td>Savivaldybė (pvz. <code>Kauno m. sav.</code>).</td></tr>
<tr><td><code>lat</code>, <code>lon</code></td><td>number</td><td>WGS-84. Daugumai — oficialios operatoriaus koordinatės; likusios geokoduotos.</td></tr>
<tr><td><code>petrol95</code>, <code>diesel</code>, <code>lpg</code></td><td>number | null</td><td>EUR už litrą. <code>null</code> = degalinė to kuro neteikia arba kainos nepateikė.</td></tr>
<tr><td><code>price_updated</code></td><td>ISO 8601</td><td>Kainos šaltinio įrašo laikas; operatoriaus kainai tai gali būti jos surinkimo laikas. Tai nėra rinkinio sugeneravimo laikas.</td></tr>
<tr><td><code>price_src</code></td><td>string | null</td><td>Kainos šaltinis: <code>portal</code>, <code>sharepoint</code> arba naujesnė patikrinta operatoriaus kaina (<code>saurida</code>).</td></tr>
<tr><td><code>display_municipality</code>, <code>display_municipality_source</code></td><td>string | null</td><td>Patikslinta rodoma savivaldybė ir jos šaltinis, jei LEA klasifikacija skiriasi nuo patikrintos vietos. <code>municipality</code> išsaugo LEA klasifikaciją.</td></tr>
<tr><td><code>display_address</code>, <code>display_address_source</code></td><td>string | null</td><td>Pasirenkamas patikslintas rodomas adresas ir jo šaltinis JSON rinkiniuose. Naudojamas tik kai abu laukai pateikti; <code>address</code> išsaugo originalų LEA adresą.</td></tr>
</table>
<pre><code>{esc(sample)}</code></pre>

<h2>Atnaujinimo dažnis</h2>
<p>LEA pateikia darbo dienų kainų rinkinius. Tikriname juos kelis kartus per dieną;
tikslus naujo rinkinio paskelbimo laikas ir jo pasirodymo Fuelis vėlavimas nėra garantuojami.
Savaitgaliais, per šventes ar dar negavus naujo rinkinio gali likti ankstesnės darbo dienos kainos.
Vertinkite <code>price_date</code> ir kiekvieno įrašo <code>price_updated</code>.
<code>generated_utc</code> žymi tik API failo paruošimo laiką.</p>

<h2 id="licencija">Licencija ir nuorodos</h2>
<p>Fuelis nemokamai, taip pat ir komerciniam naudojimui, siūlo savo išvalytą,
geokoduotą, stabilios schemos <em>rinkinį</em>. Pirminiai šaltiniai — LEA ir
atskiruose įrašuose pažymėti operatoriai. Šis rinkinio naudojimo pasiūlymas
nėra nauja pirminių LEA ar operatorių kainų duomenų licencija.</p>
<ul>
<li>Išsaugok kainos šaltinį ir datą, kai duomenis rodai programoje, tyrime ar straipsnyje.</li>
<li>Nurodyk pirminį šaltinį: <strong>Lietuvos energetikos agentūra (LEA)</strong>,
<a href="{LEA}">degalukainos.ena.lt</a>, arba įraše pažymėtą operatorių.</li>
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
