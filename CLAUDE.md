# Kuro Kainos Lietuvoje
Static PWA (HTML/CSS/vanilla JS + Leaflet) showing official LT fuel prices from LEA (ena.lt).

Data pipeline (no public API yet):
- scripts/fetch_prices.py — downloads LEA's daily SharePoint Excel (share URL + `?download=1`),
  adaptively parses it -> data/stations.json (per-station 95/diesel/lpg + national summary).
  IMPORTANT: the page lists several files; the CURRENT one is the anchor labelled
  "Naujausios degalų kainos (YYYY-MM-DD)" (a LONG-format file: Įmonė/Savivaldybė/Adresas/Degalų
  tipas/Kaina/Pateikimo data). The FIRST sharepoint link is a stale historical (May) snapshot in
  WIDE format — do not use it. `updated` is read from the file's date column, not today().
- scripts/geocode.py — geocodes each station address via OpenStreetMap Nominatim, cached in
  data/geocode_cache.json (so daily runs only geocode NEW stations), writes lat/lon into stations.json.
- .github/workflows/update-fuel-prices.yml — runs both daily (Mon–Fri), commits stations.json +
  geocode_cache.json.

App (app.js): fuel selector (95/diesel/LPG), list + Leaflet map views, browser geolocation for
"nearest to me" + distance sorting (haversine), price-labeled map POIs, and per-station Google Maps
+ Waze navigation deep links. Municipality filter/search are the fallback when location is off.

Deploy: GitHub Pages (https://linciuz.github.io/Kuro-kainos-Lietuvoje/). Icons: tools/gen_icons.py.
Android APK: TWA via Bubblewrap against the live manifest (loads the live site, auto-updates).
