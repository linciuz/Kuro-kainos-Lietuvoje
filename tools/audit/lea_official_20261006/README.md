# Official LEA source audit, 2026-10-06

Observed on October 6, 2026. The retained portal fixture was captured at
13:20:05.670684 UTC (16:20 Vilnius); the report-page HTML was saved at
13:23:01.813301 UTC. These are dated observations, not a promise of future source health.

## Portal representations

Official map: https://www.ena.lt/dk-zemelapis/ links to https://degalukainos.ena.lt/.
Current assets were `index-DJRK8e46.js` and `FuelPriceSiteApp-CU1Kqk4B.js`.
The latter's SHA256 was `6b3aff87188239585909343979ff8c1624473adc23e41625ee2b57dde565a8a7`.
Its actual fetch calls `https://api-degalukainos.ena.lt/api/v1/read/prices/latest`.

The existing `/api/v1/read/prices?per_page=3000` endpoint and `/read/prices/latest`
had identical 2,081 company/address/municipality/fuel keys and prices: zero missing
keys or mismatched prices. There were 765 station text keys, 757 priced stations,
21 null-price rows, 60 municipalities and 764 stations with coordinates. All old-feed
rows were active. Source counts: csv 1,740, manual 54, automatic 142, api 145.

Latest adds `fuel_types` and `logo_url` (1,039 rows), but omits `gas_station_id`,
`company_id`, `id`, `is_active`, `source`, `created_at`, `updated_at` and pagination.
Its 2,081 unique UUIDs match the old feed and identify fuel rows, not stations.
Old `submitted_at` is UTC ISO; latest uses naive strings such as
`2026-10-06 10:00:00`, with `last_updated` observed as `2026-10-06 13:00:20`.
Runtime keeps the richer old feed. A future migration needs explicit timestamp semantics.
The portal's XLSX button creates a workbook in the browser from these same rows.

## Power BI registry and price limits

https://www.ena.lt/dk-irankis/ contains the report iframe; the old
`/degalu-kainos-degalinese/` landing page no longer does. Public report resource UUID:
`60850ad8-c1ee-47ef-8a08-339eaee7bff4`. The existing model/query endpoint still works:
`https://wabi-west-europe-e-primary-api.analysis.windows.net/public/reports/querydata?synchronous=true`.

The master query returned 2,324 fuel rows and 851 company/municipality/address keys.
The existing company/address fold yields 848 registry stations, versus 847 cached.
Exactly one addition, zero removals, and all 847 previous records unchanged:
UAB Osijos dujos, Marijampolės sav., `Marijampolė, Gėlyno g. 25,`, LPG.
This addition exists in today's portal and the October 6 Power BI rows. Only registry
fields were saved to `data/sources/lea_powerbi.json`; no Power BI prices were saved.
Refreshed cache SHA256: `acdbc61ed4ae54a650b5c499aa3c1f782403a86197bff530645fa10dc30a153f`.

The existing undated registry SUM aggregates historical prices. An explicit October 6
date filter produced 2,030 keys present in the portal: 1,973 prices matched to three
decimals and 57 differed, including doubled sums (Alauša 3.958 versus portal 1.979).
Some exact-date values are retail prices; a universal pre-tax description is unsupported.
Date filtering alone does not make this SUM query safe for live prices. The separate
`degalu_kainos_senos` history query returned 44 dates, April 8 through June 10.
All 44 date keys already exist in `data/price_history.json`; the existing history
fetcher skips existing dates, so this discovery repair adds zero historical days
from the observed response. No history file was written during this check.

## Restored annual archive

https://www.ena.lt/dk-pr-pr-duomenys/ states that daily raw data moved to one annual
Excel file beginning September 9, 2026. Current linked workbook:
https://ltenergagen.sharepoint.com/:x:/s/intra/doc/IQBfrNG1U7XYR5LAZBxiytLDARSs2Dah4n5ehD0IaSg0dUs?e=7t6GjA

Direct `download=1` returned HTTP 403; the existing public OneDrive fallback returned
HTTP 400. Workbook contents, date coverage and schema are unverified. The archive is
not integrated. The current engine only searches the older landing page, so its
"retired" result describes its discovery limitation, not current official availability.

## Retained evidence

- `dk-irankis.html`: 37,608 bytes, SHA256
  `33cf20c55b17485fa3fb9d35896f8ee79e17512b1b0205764b70abd33eba6431`.
- `tests/fixtures/lea-prices-20261006.json.gz`, decompressed payload SHA256
  `5faca85bc920e2c80812045e1d7b128fe7bd8398d7a1bbc99e709f0aee56051f`.
  Its metadata records the exact capture time and excludes bearer credentials.
- `pbi-history-date-check.json`: the 44 returned date keys, 44 existing matches,
  zero new dates and an explicit no-history-write result.

The latest full refresh, GitHub run 37461724389 (completed 12:18:49 UTC), was green
despite failed Power BI discovery/history, Baltic Petroleum and Viada directory 403s,
OSM mirror 504s and the known Neste promo 404. Caches and repeat-alarm suppression
explain that result; a green workflow alone is not proof that every source refreshed.
