# Kuro Kainos Lietuvoje
Static PWA (HTML/CSS/vanilla JS + Leaflet) showing official LT fuel prices from LEA (ena.lt).

Data pipeline — LEA has a public JSON API since 2026-07-28:
- **PRIMARY: scripts/fetch_prices.py -> price_engine.resolve() -> from_portal()** reads LEA's self-service portal API
  `https://api-degalukainos.ena.lt/api/v1/read/prices?per_page=3000` (Bearer token scraped from
  degalukainos.ena.lt's own JS bundle each run, so a rotation self-heals). On 2026-07-28 LEA
  REMOVED the SharePoint links from ena.lt and migrated here; portal price coverage went 7% -> 94%
  overnight and matched the final Excel to the 3rd decimal. `updated` = newest `submitted_at`.
  The payload records `price_source` and per-station `price_src` provenance.
- **Official portal changed (verified 2026-10-06):** its current JS calls
  `/read/prices/latest`. It matched the existing `/read/prices` feed on all 2,081 station/fuel
  keys and prices in this audit. It adds logos and declared fuels but omits stable numeric station
  IDs, active flags and source types; timestamps are naive strings rather than UTC ISO. Keep the
  current richer feed until a deliberate schema and timestamp migration is verified. The portal's
  Excel export is generated in the browser from the same rows, not another price authority.
- **Annual SharePoint archive restored:** `https://www.ena.lt/dk-pr-pr-duomenys/` says daily raw
  data moved to one annual Excel file on 2026-09-09. The old landing page still has no SharePoint
  link, so the current `from_sharepoint()` discovery reports it retired. The newly linked workbook
  returned HTTP 403 for direct download and 400 for the public OneDrive fallback on 2026-10-06;
  its contents and schema are unverified and it is NOT integrated. Never feed an entire annual
  archive to a parser expecting one daily snapshot.
- **No independent redundancy for most stations.** Both portal read routes are the same LEA
  origin. Saurida supplies a limited, independently sourced per-station overlay. Power BI remains
  excluded from live prices: the existing undated SUM query aggregates history, and even an
  October 6 date filter matched 1,973/2,030 portal quotes while 57 differed (including duplicate
  sums). Do not describe all Power BI values as universally pre-tax. Registry and history access
  now discover the report iframe at `/dk-irankis/`; `/dk-zemelapis/` links to the portal. The
  never-regress guard and freshness gates remain essential. Audit evidence and limits:
  `tools/audit/lea_official_20261006/README.md`.
- scripts/fetch_lea_portal.py — same API, folded per station: official operator-registered
  lat/lon (fixed 120 wrong pins), consumer brand and per-station submit timestamps. Current
  `/read/prices` rows omit logos; the new `/read/prices/latest` representation includes them.
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
