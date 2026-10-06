#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Static fuel comparisons, regenerated from committed real station prices.

  kainos/<municipality-slug>.html   municipal statistics and independent
                                    cheapest stations for each fuel.
  kainos/index.html                 national comparisons and municipal links.
  sitemap.xml                       canonical pages with content-hash lastmod.
  index.html                        compact dated national snapshot and metadata.

Pure local-file work (no network) — safe to run in every pipeline pass.
"""

import datetime as dt
import hashlib
import html
import json
import os
import math
import re
import sys
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lt_places import place

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

SITE = "https://fuelis.lt"
OUTDIR = "kainos"
FUELS = [("petrol95", "Benzinas 95"), ("diesel", "Dyzelinas"), ("lpg", "Dujos (LPG)")]
FUEL_GENITIVE = {"petrol95": "benzino 95", "diesel": "dyzelino", "lpg": "LPG"}
LEA = "https://degalukainos.ena.lt/"
SAURIDA = "https://www.saurida.lt/kuro-kainos-degalinese/"
LT = ZoneInfo("Europe/Vilnius")

_DEACC = str.maketrans("ąčęėįšųūžĄČĘĖĮŠŲŪŽ", "aceeisuuzACEEISUUZ")


def slugify(muni):
    s = muni.translate(_DEACC).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


def esc(s):
    return html.escape(str(s or ""), quote=True)


def station_address(station):
    """Attributed display text; the source address remains untouched."""
    return (station["display_address"] if station.get("display_address") and
            station.get("display_address_source") else station.get("address") or "")


def station_address_html(station):
    shown = esc(station_address(station))
    if not (station.get("display_address") and station.get("display_address_source")):
        return shown
    return (f'<span title="{esc("LEA: " + (station.get("address") or ""))}">{shown}</span> '
            f'<a href="{esc(station["display_address_source"])}" target="_blank" '
            'rel="noopener" title="Šaltinis" aria-label="Šaltinis">↗</a>')


def station_municipality(station):
    return (station["display_municipality"] if station.get("display_municipality") and
            station.get("display_municipality_source") else station.get("municipality") or "")


def municipality_place(muni):
    # The source prefix is already genitive, so district pages can describe
    # the actual whole district rather than only its administrative centre.
    if muni.endswith(" r. sav."):
        prefix = muni.removesuffix(" r. sav.")
        return f"{prefix} rajonas", f"{prefix} rajone"
    return place(muni)


def municipality_name(muni):
    return municipality_place(muni)[0]


def fmt(v):
    return f"€{v:.3f}" if isinstance(v, (int, float)) else "–"


def muni_stats(rows):
    out = {}
    for key, _ in FUELS:
        vals = [s[key] for s in rows if isinstance(s.get(key), (int, float))]
        if vals:
            out[key] = {"min": min(vals), "avg": sum(vals) / len(vals), "max": max(vals), "n": len(vals)}
    return out


PAGE_CSS = """body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;margin:0;background:#f4f6fa;color:#1b2430}
.wrap{max-width:860px;margin:0 auto;padding:18px 14px 40px}
a{color:#0062CC}.card{background:#fff;border:1px solid #e2e6ec;border-radius:12px;padding:16px;margin:14px 0}
h1{font-size:22px;margin:10px 0 2px}h2{font-size:16px;margin:0 0 10px}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:7px 8px;text-align:left;border-bottom:1px solid #eef1f4}th{color:#55606d;font-weight:600}
td.n{font-variant-numeric:tabular-nums;white-space:nowrap}.lo{color:#15803d;font-weight:700}
.meta{color:#6b7280;font-size:12.5px}.cta{display:inline-block;background:#0062CC;color:#fff;padding:11px 18px;
border-radius:10px;text-decoration:none;font-weight:600;margin-top:6px}
.bc{font-size:12.5px;color:#6b7280;margin-bottom:4px}.bc a{color:#6b7280}
.nb{margin:0;padding-left:18px;font-size:14px;line-height:1.7}
.grid{display:flex;flex-wrap:wrap;gap:8px}.grid a{background:#fff;border:1px solid #e2e6ec;border-radius:10px;
padding:9px 13px;text-decoration:none;font-size:14px}
.table-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
.table-scroll th,.table-scroll td{overflow-wrap:normal}
.ranking td:last-child{font-size:12px;color:#55606d}.ranking time{display:block}
.lead{font-size:15px;line-height:1.6}.note{font-size:13px;line-height:1.6}
h3{font-size:15px;margin:16px 0 8px}
@media(max-width:480px){table{font-size:12px}th,td{padding:7px 5px}}"""


def breadcrumb(trail):
    """BreadcrumbList for a [(name, url), ...] trail.

    Every page already RENDERS a breadcrumb ("Fuelis › Kainos pagal savivaldybę
    › Kaunas") with no markup behind it. Marking it up is what tells a crawler
    the hub is the parent of all 60 children — the hub is the single chokepoint
    those children are discovered through, and it carried no structured data at
    all while its children carried ItemList/GasStation."""
    return {
        "@type": "BreadcrumbList",
        "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": n, "item": u}
                            for i, (n, u) in enumerate(trail)],
    }


def page_shell(title, desc, canonical, body, jsonld=None):
    # A list of nodes becomes a @graph; a single dict is emitted as-is.
    if isinstance(jsonld, list):
        jsonld = {"@context": "https://schema.org", "@graph": jsonld}
    ld = f'<script type="application/ld+json">{json.dumps(jsonld, ensure_ascii=False)}</script>' if jsonld else ""
    return f"""<!DOCTYPE html>
<html lang="lt">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{canonical}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{canonical}">
<meta property="og:image" content="{SITE}/og-image.png">
<meta name="robots" content="index, follow">
{ld}
<style>{PAGE_CSS}</style>
</head>
<body><div class="wrap">{body}</div></body>
</html>"""


def centroid(rows):
    pts = [(r["lat"], r["lon"]) for r in rows
           if isinstance(r.get("lat"), (int, float)) and isinstance(r.get("lon"), (int, float))]
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def km(a, b):
    dlat = (a[0] - b[0]) * 111.0
    dlon = (a[1] - b[1]) * 111.0 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dlat, dlon)


def plural(n, one, few, many):
    """Lithuanian numeral agreement: 1/21/31 -> singular, 2-9/22-29 -> plural
    nominative, 0 and 10-19 -> genitive plural. ("1 degalinės" is wrong.)"""
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 9 and not (11 <= n % 100 <= 19):
        return few
    return many


def priced_rows(rows):
    """Stations that actually carry a price — the only ones the tables can show.
    The headline count MUST use this too: 36 of 60 pages previously announced a
    total that included price-less registry stations and then contradicted
    themselves two lines below."""
    return [r for r in rows if any(isinstance(r.get(k), (int, float))
                                   for k in ("petrol95", "diesel", "lpg"))]


def cheapest_rows(rows, fuel, limit=3):
    """Rank each fuel independently; an LPG-only station must be eligible."""
    return sorted((r for r in rows if isinstance(r.get(fuel), (int, float))),
                  key=lambda r: (r[fuel], r.get("network") or "", station_address(r)))[:limit]


def app_link(muni="", fuel="", view="list"):
    params = {"view": view}
    if muni:
        params["muni"] = muni
    if fuel:
        params["fuel"] = fuel
    return f"{SITE}/?{urlencode(params)}"


def source_text(rows):
    text = "LEA duomenys"
    if any(r.get("price_src") == "saurida" for r in rows):
        text += ", papildyti naujesnėmis patikrintomis Saurida kainomis"
    return text


def source_html(rows):
    text = f'<a href="{LEA}" target="_blank" rel="noopener">LEA duomenys</a>'
    if any(r.get("price_src") == "saurida" for r in rows):
        text += (f', papildyti naujesnėmis patikrintomis '
                 f'<a href="{SAURIDA}" target="_blank" rel="noopener">Saurida kainomis</a>')
    return text


def row_source_html(station):
    source = station.get("price_src")
    if source == "saurida":
        label, url = "Saurida", SAURIDA
    elif source in ("portal", "sharepoint"):
        label, url = "LEA", LEA
    else:
        label, url = "Šaltinis nenurodytas", None
    shown = f'<a href="{url}" target="_blank" rel="noopener">{label}</a>' if url else label
    stamp = station.get("price_updated")
    if stamp:
        try:
            recorded = dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            # Never assign an assumed timezone to a source's naive timestamp.
            if recorded.tzinfo is not None:
                local = recorded.astimezone(LT)
                caption = "Nuskaityta" if source == "saurida" else "Įrašas"
                title = "Kainos nuskaitymo laikas" if source == "saurida" else "LEA įrašo / suvestinės laikas"
                shown += (f'<time datetime="{esc(stamp)}" title="{title}">'
                          f'{caption}: {local:%Y-%m-%d %H:%M} LT</time>')
        except (ValueError, TypeError):
            pass
    return shown


def stats_html(rows):
    stats = muni_stats(rows)
    body = "".join(
        f"<tr><th scope='row'>{label}</th><td class='n lo'>{fmt(s['min'])}</td>"
        f"<td class='n'>{fmt(s['avg'])}</td><td class='n'>{fmt(s['max'])}</td>"
        f"<td class='n'>{s['n']}</td></tr>"
        for fuel, label in FUELS if (s := stats.get(fuel)))
    return (f'<div class="table-scroll"><table><thead><tr><th>Kuras</th>'
            '<th>Mažiausia</th><th>Vidurkis</th><th>Didžiausia</th><th>Su kaina</th>'
            f'</tr></thead><tbody>{body}</tbody></table></div>')


def rankings_html(rows, muni=""):
    blocks, nodes = [], []
    for fuel, label in FUELS:
        cheapest = cheapest_rows(rows, fuel)
        if not cheapest:
            continue
        table_rows = "".join(
            f'<tr><td>{esc(r.get("network"))}</td><td>{station_address_html(r)}</td>'
            f'<td class="n lo">{fmt(r[fuel])}</td><td>{row_source_html(r)}</td></tr>'
            for r in cheapest)
        blocks.append(
            f'<section id="{fuel}"><h3>{label}: mažiausios pateiktos kainos</h3>'
            f'<div class="table-scroll"><table class="ranking"><thead><tr>'
            '<th>Tinklas</th><th>Adresas</th><th>Kaina, €/l</th><th>Šaltinis ir laikas</th>'
            f'</tr></thead><tbody>{table_rows}</tbody></table></div>'
            f'<p class="meta"><a href="{esc(app_link(muni, fuel))}">'
            f'Visos {FUEL_GENITIVE[fuel]} kainos ir degalinės →</a></p></section>')
        nodes.append({
            "@type": "ItemList", "name": f"{label}: mažiausios pateiktos kainos",
            "numberOfItems": len(cheapest),
            "itemListElement": [
                {"@type": "ListItem", "position": i + 1,
                 "item": {"@type": "GasStation", "name": r.get("network") or "Degalinė",
                          "address": {"@type": "PostalAddress", "streetAddress": station_address(r),
                                      "addressRegion": station_municipality(r), "addressCountry": "LT"}}}
                for i, r in enumerate(cheapest)],
        })
    return "".join(blocks), nodes


def build_muni_page(muni, rows, updated, neighbours=()):
    slug = slugify(muni)
    stats = muni_stats(rows)
    if not stats:
        return None
    name, loc = municipality_place(muni)
    priced = priced_rows(rows)
    n = len(priced)
    missing = [label for fuel, label in FUELS if fuel not in stats]
    missing_note = (f'<p class="meta">Nepateikta kainų šioms kuro rūšims: '
                    f'{esc(", ".join(missing))}.</p>' if missing else "")

    title = f"Kuro kainos {loc} — degalinės ir kainos | Fuelis"
    d_avg = stats.get("diesel", {}).get("avg")
    p_avg = stats.get("petrol95", {}).get("avg")
    desc = (f"Kuro kainos {loc} ({updated}), "
            f"{n} {plural(n, 'degalinė', 'degalinės', 'degalinių')} su kainomis. "
            + (f"Dyzelinas vid. {fmt(d_avg)}, " if d_avg else "")
            + (f"benzinas 95 vid. {fmt(p_avg)}. " if p_avg else "")
            + "Mažiausios kainos pagal kuro rūšį, degalinių sąrašas ir žemėlapis.")
    rankings, jsonld = rankings_html(rows, muni)

    # Neighbouring municipalities: genuine added value (where to drive if local
    # prices are bad) AND the internal linking the cluster completely lacked.
    nb = "".join(
        f'<li><a href="{SITE}/kainos/{slugify(m)}.html">{esc(municipality_name(m))}</a>'
        f' — dyzelinas nuo {fmt(v)}{"" if d is None else f" ({d:.0f} km)"}</li>'
        for m, v, d in neighbours)
    nb_block = (f'<div class="card"><h2>Netolimų savivaldybių kainos</h2><ul class="nb">{nb}</ul>'
                '<p class="meta">Atstumai apytiksliai, tiesia linija tarp savivaldybių degalinių '
                'centrų. Tai nėra kelionės keliu atstumai ar atstumas nuo jūsų vietos.</p></div>'
                if nb else "")

    app_url = esc(app_link(muni, view="map"))
    body = f"""<p class="bc"><a href="{SITE}/">Fuelis</a> › <a href="{SITE}/kainos/">Kainos pagal savivaldybę</a> › {esc(name)}</p>
<h1>Kuro kainos {esc(loc)}</h1>
<p class="lead">Palyginkite pateiktas degalų kainas ir degalinių adresus.
Šiame puslapyje pateikiama visa <strong>{esc(muni)}</strong> teritorija pagal rinkinio savivaldybės klasifikaciją.</p>
<p class="meta">Kainų rinkinio data: <time datetime="{esc(updated)}">{esc(updated)}</time> ·
{n} {plural(n, "degalinė", "degalinės", "degalinių")} su kainomis · {source_html(rows)}</p>
<div class="card"><h2>Degalų kainų suvestinė, €/l</h2>{stats_html(rows)}
{missing_note}<p class="meta">Vidurkis skaičiuojamas iš pateiktų šios kuro rūšies kainų; tai nėra pardavimų svertinis vidurkis.</p></div>
<div class="card"><h2>Pigesnės degalinės pagal kuro rūšį</h2>{rankings}
<p class="note">Kiekviena kuro rūšis rikiuojama atskirai; rodomos iki trijų mažiausių pateiktų kainų.
Kainos degalinėje dienos eigoje gali keistis. Trūkstama kaina neįtraukiama į palyginimą.</p>
<a class="cta" href="{app_url}">Atidaryti žemėlapyje →</a></div>
{nb_block}
<p class="meta">Visos savivaldybės: <a href="{SITE}/kainos/">kainos pagal savivaldybę</a> ·
<a href="{SITE}/">Fuelis — degalų kainų žemėlapis</a></p>"""
    jsonld.append(breadcrumb([
        ("Fuelis", f"{SITE}/"),
        ("Kainos pagal savivaldybę", f"{SITE}/kainos/"),
        (name, f"{SITE}/kainos/{slug}.html"),
    ]))
    return slug, page_shell(title, desc, f"{SITE}/kainos/{slug}.html", body, jsonld)


def build_directory(slugs_munis, updated, rows):
    rankings, ranking_nodes = rankings_html(rows)
    links = "".join(f'<a href="{SITE}/kainos/{slug}.html">{esc(municipality_name(muni))}</a>'
                    for slug, muni in sorted(slugs_munis, key=lambda x: place(x[1])[0]))
    body = f"""<p class="bc"><a href="{SITE}/">Fuelis</a> › Kainos pagal savivaldybę</p>
<h1>Kuro kainos Lietuvoje</h1>
<p class="lead">Benzino 95, dyzelino ir LPG degalų kainų palyginimas pagal pateiktus duomenis.
Raskite pigesnę degalinę pagal kuro rūšį arba pasirinkite savo savivaldybę.</p>
<p class="meta">Kainų rinkinio data: <time datetime="{esc(updated)}">{esc(updated)}</time> ·
{len(priced_rows(rows))} {plural(len(priced_rows(rows)), "degalinė", "degalinės", "degalinių")} su kainomis ·
{source_html(rows)}</p>
<div class="card"><h2>Lietuvos degalų kainų suvestinė, €/l</h2>{stats_html(rows)}
<p class="meta">Vidurkis skaičiuojamas iš pateiktų kainų, be kainos esančios degalinės neįtraukiamos.
Tai nėra pardavimų svertinis vidurkis.</p></div>
<div class="card"><h2>Mažiausios pateiktos kainos pagal kuro rūšį</h2>{rankings}
<p class="note">Kiekvienai kuro rūšiai rodoma iki trijų pigiausių įrašų.
Kainos degalinėje gali pasikeisti; prieš pildami pasitikrinkite kolonėlėje.</p></div>
<div class="card"><h2>Kuro kainos pagal miestus ir savivaldybes</h2><div class="grid">{links}</div></div>
<div class="card"><h2>Kaip naudotis kuro kainų paieška?</h2>
<p class="note">Pasirinkite savivaldybę ir kuro rūšį, palyginkite kainas bei adresus.
<a href="{SITE}/">Interaktyvioje paieškoje ir žemėlapyje</a> galite ieškoti pagal tinklą ar adresą;
leidę nustatyti vietą, rikiuoti pagal atstumą ir atidaryti navigaciją.</p>
<p class="note">Rinkinio data ir atskiro šaltinio įrašo laikas reiškia skirtingus dalykus.
Paskutiniai paskelbti duomenys gali būti ankstesnės darbo dienos; naujas rinkinys nėra
garantija, kad kiekvienos kolonėlės kaina šiuo metu tokia pati.
<a href="{SITE}/atviri-duomenys.html">Duomenų šaltiniai ir atnaujinimo paaiškinimas</a>.</p></div>
<p class="meta"><a href="{SITE}/">← Fuelis žemėlapis ir paieška</a> ·
<a href="{SITE}/atviri-duomenys.html">Atviri duomenys (JSON/CSV API)</a></p>"""
    ordered = sorted(slugs_munis, key=lambda x: place(x[1])[0])
    jsonld = [
        {
            "@type": "CollectionPage",
            "@id": f"{SITE}/kainos/",
            "name": "Kuro kainos Lietuvoje",
            "inLanguage": "lt",
            "isPartOf": {"@type": "WebSite", "@id": f"{SITE}/#website"},
            # Naming every child here makes the parent/child relationship
            # explicit instead of leaving it implied by 60 anchor tags.
            "mainEntity": {
                "@type": "ItemList",
                "numberOfItems": len(ordered),
                "itemListElement": [
                    {"@type": "ListItem", "position": i + 1,
                     "name": municipality_name(muni), "url": f"{SITE}/kainos/{slug}.html"}
                    for i, (slug, muni) in enumerate(ordered)
                ],
            },
        },
        breadcrumb([("Fuelis", f"{SITE}/"),
                    ("Kainos pagal savivaldybę", f"{SITE}/kainos/")]),
    ]
    jsonld.extend(ranking_nodes)
    return page_shell("Kuro kainos Lietuvoje — benzinas, dyzelinas, LPG | Fuelis",
                      f"Kuro kainų palyginimas Lietuvoje ({updated}): benzinas 95, dyzelinas, LPG. "
                      f"{len(priced_rows(rows))} "
                      f"{plural(len(priced_rows(rows)), 'degalinė', 'degalinės', 'degalinių')} "
                      "su kainomis ir paieška pagal savivaldybę.",
                      f"{SITE}/kainos/", body, jsonld)


# Per-URL lastmod memory: {url: {"hash": ..., "lastmod": ...}}. Committed with
# the data so the whole pipeline shares one honest record of when each page
# genuinely last changed.
LASTMOD_STATE = os.path.join("data", "_page_lastmod.json")


def page_lastmods(url_to_path, updated):
    """lastmod driven by the page's actual CONTENT HASH, not by "today".

    Every URL previously carried the same lastmod, which was false: a privacy
    policy does not change daily. Google only uses lastmod when it is
    "consistently and verifiably accurate" — one demonstrably wrong entry
    teaches it to discount the signal for the whole file, and on a domain with
    no inbound links that hint is the only crawl-scheduling leverage we have.

    Double duty: the same hashes are what let a future run tell a genuinely
    changed page from an unchanged one, instead of re-announcing all 64 URLs.
    """
    try:
        state = json.load(open(LASTMOD_STATE, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    out, changed = {}, 0
    for url, path in url_to_path.items():
        try:
            content = open(path, encoding="utf-8").read()
        except OSError:
            # Page not built this run — keep whatever we last knew about it
            # rather than inventing a fresh timestamp.
            out[url] = (state.get(url) or {}).get("lastmod", updated)
            continue
        h = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        prev = state.get(url) or {}
        if prev.get("hash") == h and prev.get("lastmod"):
            out[url] = prev["lastmod"]
        else:
            out[url] = updated
            state[url] = {"hash": h, "lastmod": updated}
            changed += 1
    json.dump(state, open(LASTMOD_STATE, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, sort_keys=True)
    print(f"[sitemap] {changed}/{len(url_to_path)} pages changed content this run")
    return out


def build_sitemap(slugs, updated):
    url_to_path = {
        f"{SITE}/": "index.html",
        f"{SITE}/kainos/": os.path.join(OUTDIR, "index.html"),
        f"{SITE}/atviri-duomenys.html": "atviri-duomenys.html",
        f"{SITE}/privatumas.html": "privatumas.html",
    }
    for s in sorted(slugs):
        url_to_path[f"{SITE}/kainos/{s}.html"] = os.path.join(OUTDIR, f"{s}.html")
    lastmod = page_lastmods(url_to_path, updated)
    # <changefreq> and <priority> are dropped deliberately: Google's own
    # documentation states it ignores both. They were 128 lines of dead bytes.
    entries = "\n".join(
        f"  <url>\n    <loc>{u}</loc>\n    <lastmod>{lastmod[u]}</lastmod>\n  </url>"
        for u in url_to_path)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{entries}\n</urlset>\n'


def refresh_index_html(summary, updated, n_stations):
    """Rewrite the two marker blocks in index.html in place (idempotent)."""
    p = "index.html"
    s = open(p, encoding="utf-8").read()
    avg = lambda k: (summary.get(k) or {}).get("avg")
    parts = []
    if avg("diesel"): parts.append(f"dyzelinas vid. €{avg('diesel'):.3f}")
    if avg("petrol95"): parts.append(f"benzinas 95 vid. €{avg('petrol95'):.3f}")
    if avg("lpg"): parts.append(f"dujos €{avg('lpg'):.3f}")
    line = ", ".join(parts)

    desc = ("Kuro kainų paieška Lietuvoje: palyginkite benzino 95, dyzelino ir LPG kainas, "
            f"raskite pigesnę degalinę žemėlapyje. {updated}: {n_stations} degalinių su kainomis.")
    s = re.sub(r'(<meta name="description" content=")[^"]*(")', lambda m: m.group(1) + esc(desc) + m.group(2), s)
    s = re.sub(r'(<meta property="og:description" content=")[^"]*(")', lambda m: m.group(1) + esc(desc) + m.group(2), s)
    s = re.sub(r'(<meta name="twitter:description" content=")[^"]*(")', lambda m: m.group(1) + esc(desc) + m.group(2), s)

    block = (f'<!-- FUELIS:STATIC-PRICES --><div class="crawl-prices" id="crawl-prices" lang="lt">'
             f'<p><strong>Lietuvos kainų suvestinė · {esc(updated)}</strong>: {esc(line)} (€/l) — '
             f'{n_stations} {plural(n_stations, "degalinė", "degalinės", "degalinių")} su kainomis. '
             'LEA ir, kai naujesnės, pažymėtos operatorių kainos. '
             f'<a href="{SITE}/kainos/">Kuro kainos pagal savivaldybę</a> · '
             f'<a href="{SITE}/atviri-duomenys.html">Atviri duomenys (API)</a>.'
             f'</p></div><!-- /FUELIS:STATIC-PRICES -->')
    if "FUELIS:STATIC-PRICES" in s:
        s = re.sub(r'<!-- FUELIS:STATIC-PRICES -->.*?<!-- /FUELIS:STATIC-PRICES -->', block, s, flags=re.S)
    else:
        s = s.replace('<div id="list-view">', block + '\n\n        <div id="list-view">')
    open(p, "w", encoding="utf-8", newline="\n").write(s)


def main():
    d = json.load(open("data/stations.json", encoding="utf-8"))
    updated = d.get("updated")
    if not updated:
        raise ValueError("Missing real price date; refusing to label unknown prices as today's")
    stations = d.get("stations") or []
    by_muni = {}
    for s in stations:
        m = station_municipality(s)
        if m:
            by_muni.setdefault(m, []).append(s)

    os.makedirs(OUTDIR, exist_ok=True)
    # Nearest municipalities by station centroid — powers the cross-links.
    cents = {m: centroid(r) for m, r in by_muni.items()}
    best_diesel = {}
    for m, r in by_muni.items():
        vals = [x["diesel"] for x in r if isinstance(x.get("diesel"), (int, float))]
        if vals:
            best_diesel[m] = min(vals)

    def neighbours_of(muni):
        c = cents.get(muni)
        if not c:
            return []
        near = sorted(((km(c, cents[o]), o) for o in by_muni
                       if o != muni and cents.get(o) and o in best_diesel),
                      key=lambda t: t[0])[:5]
        return [(o, best_diesel[o], d) for d, o in near]

    slugs_munis = []
    for muni, rows in by_muni.items():
        built = build_muni_page(muni, rows, updated, neighbours_of(muni))
        if not built:
            continue
        slug, html_page = built
        open(os.path.join(OUTDIR, f"{slug}.html"), "w", encoding="utf-8", newline="\n").write(html_page)
        slugs_munis.append((slug, muni))
    open(os.path.join(OUTDIR, "index.html"), "w", encoding="utf-8", newline="\n").write(
        build_directory(slugs_munis, updated, stations))
    # The headline count must be PRICED stations, not the whole registry. The
    # description read "Oficialios degalų kainos 808 Lietuvos degalinių" while
    # only 729 of those 808 carry a price — the other 79 are price-less registry
    # entries we plot on the map. The municipality pages already got this right
    # via priced_rows(); the homepage meta did not, and it's the one line
    # search results and link previews actually quote.
    refresh_index_html(d.get("summary") or {}, updated, len(priced_rows(stations)))
    # Sitemap LAST: page_lastmods() hashes the files on disk, so index.html must
    # already be rewritten or its hash would be one build stale every run.
    open("sitemap.xml", "w", encoding="utf-8", newline="\n").write(
        build_sitemap([s for s, _ in slugs_munis], updated))
    print(f"[ok] built {len(slugs_munis)} municipality pages + directory + sitemap; index.html refreshed")


if __name__ == "__main__":
    main()
