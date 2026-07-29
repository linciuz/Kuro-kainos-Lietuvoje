# Kuro Kainos Lietuvoje
Static PWA (HTML/CSS/vanilla JS + Leaflet) showing official LT fuel prices from LEA (ena.lt).

Data pipeline — LEA has a public JSON API since 2026-07-28:
- **PRIMARY: scripts/fetch_prices.py -> portal_stations()** reads LEA's self-service portal API
  `https://api-degalukainos.ena.lt/api/v1/read/prices?per_page=3000` (Bearer token scraped from
  degalukainos.ena.lt's own JS bundle each run, so a rotation self-heals). On 2026-07-28 LEA
  REMOVED the SharePoint links from ena.lt and migrated here; portal price coverage went 7% -> 94%
  overnight and matched the final Excel to the 3rd decimal. `updated` = newest `submitted_at`.
  The payload records `price_source: "portal" | "sharepoint"` so you can see which fed a file.
- FALLBACK: `_sharepoint_stations()` — the old daily Excel, if LEA ever relinks it. The CURRENT
  one is labelled "Naujausios degalų kainos (YYYY-MM-DD)" (LONG format: Įmonė/Savivaldybė/Adresas/
  Degalų tipas/Kaina/Pateikimo data); the FIRST sharepoint link is a stale May snapshot in WIDE
  format — do not use it. `updated` comes from the file's date column, never today().
- scripts/fetch_lea_portal.py — same API, folded per station: official operator-registered
  lat/lon (fixed 120 wrong pins), consumer brand, logo, per-station submit timestamps.
  merge_chain_coords.py applies those coords last (coord_source="lea_portal").
- scripts/geocode.py — geocodes each station address via OpenStreetMap Nominatim, cached in
  data/geocode_cache.json (so daily runs only geocode NEW stations), writes lat/lon into stations.json.
- .github/workflows/update-fuel-prices.yml — runs both daily (Mon–Fri), commits stations.json +
  geocode_cache.json.

App (app.js): fuel selector (95/diesel/LPG), list + Leaflet map views, browser geolocation for
"nearest to me" + distance sorting (haversine), price-labeled map POIs, and per-station Google Maps
+ Waze navigation deep links. Municipality filter/search are the fallback when location is off.

Deploy: GitHub Pages (https://linciuz.github.io/Kuro-kainos-Lietuvoje/). Icons: tools/gen_icons.py.
Android APK: TWA via Bubblewrap against the live manifest (loads the live site, auto-updates).
