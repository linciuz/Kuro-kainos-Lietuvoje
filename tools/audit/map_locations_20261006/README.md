# Fuelis station location audit — 2026-10-06

All **809 source station records** were screened. These include legacy spelling
aliases of physical stations; 809 is not an independently counted number of
operating forecourts. This audit does **not** certify all809 addresses or pins as
ground truth.

The repair changes **44 record coordinates, four displayed municipality labels
and one displayed address**. Three additional, already supported Viada points
are pinned to their exact source identities so the conservative matcher cannot
expose old geocoding guesses on the next price refresh. Across all809 rows,
prices, price timestamps, dates, source identities, raw addresses, raw
municipalities, ordering and the national price summary remain unchanged.
`final-changes.json` contains the complete before/after geographic changes and
the input/output SHA-256 values.

## Findings and corrections

- Viada Šilagalis had been joined to Rokiškis, **83km** away. Vievis Kauno26A
  had selected Kauno55A. The new matcher requires full house suffix, street and
  locality/city identity, rejects ambiguous points and refuses different
  addresses competing for one point. Verified aliases use exact source keys.
- EMSI Spyglių2 had inherited Jeruzalės2, **13.2km** away. LEA's exact station
  and the independently mapped Spyglių premises agree; its newer generic
  operator marker is still487m away, so it was not imported.
- Jozita Kuosiškiai's July manual override was **12.3km** wrong. The current
  operator locator, LEA and OSM identify the corrected Kuosiškiai4 premises.
- Regusa Jūrė had inherited another Regusa station in Kaunas, **32km** away.
  Its own address, the Kazlų Rūda municipal station navigation and OSM agree.
- Nostrada/RV Transport Kazlavo's legacy approximate point was **145.4km**
  away in Vilnius. Its exact LEA sibling, the current AAD2026 premises record
  and OSM RV station/Kazlavas4 support the corrected premises area.
- Further case-specific corrections cover Saurida aliases, BP village/street
  variants, Alauša, Stateta, Boost Petrol, Narjanta, Bonsa, Tomega and four ORLEN
  records. Evidence grades and conflicting upstream markers are recorded per
  exact key in `correction-evidence.json`.
- BP Pasvalys is displayed as **Pasvalio r. sav., Aukštikalnių k., Mūšos g.19**,
  using the current [BP directory](https://mobileapi.fscc.lt/bp/api/stations?pageSize=500).
  Raw LEA Mūšos18 remains the source identity. List, popup, navigation, search,
  sharing, static pages and API carry the attributed correction.

The four new region-label corrections are Saurida Neveronys/Martinavos1
(Kauno city → district), both Jūrininkų29 aliases (Klaipėda district → city) and
legacy Rumšiškės/Lekavičiaus71 (Kauno district → Kaišiadorių district). The earlier
EMSI Ramygalos186A correction remains in place. Other region errors were repaired
by moving wrong pins, not by changing the source region to fit a bad point.

## Municipality audit

Source: [Registrų centras municipality boundaries](https://data.gov.lt/datasets/1345/),
[download](https://www.registrucentras.lt/aduomenys/?byla=adr_gra_savivaldybes.json),
licensed **CC BY4.0, VĮ Registrų centras**. All60 valid features have
`formav_dat=2026-10-01`, native EPSG:3346. Original payload SHA-256:
`231f235d496cbada473d161acbceecd9c45a300e0a51cdfdef5224bfaded7ed7`.

WGS84 station points are transformed to LKS94, checked against polygon/multipolygon
interiors and holes, and measured against the nearest boundary in metres.
All809 points belong to exactly one municipality; none are outside or overlapping.
Displayed-label mismatches fall from **17 to2**. The remaining two are inside the
100m review band, not evidence for an automatic region correction:

| Record | Boundary conflict | Margin |
|---|---|---:|
| BP Grigaičiai, Pavilnės1 | source Vilniaus district; point in city | 8.764m |
| EMSI Kaunas, Vandžiogalos86A | source Kauno district; point in city | 71.803m |

There are21 final pins within100m of a municipal boundary;19 labels agree.
The100m band is a review threshold, not a claimed measurement uncertainty or
legal/cadastral determination. The boundary test establishes the jurisdiction of
a supplied point, not whether that point actually belongs to the station.

To repeat the offline test, install the optional audit requirements in a separate
environment, download the current official JSON, then run from the repository:

```text
python tools/audit/audit_municipalities.py --stations data/stations.json --boundaries PATH_TO_RC_JSON --output-prefix .claude/map-audit-repeat
```

Outputs must stay inside `.claude` or `tools/audit/reports` and cannot overwrite
either input. The large
boundary geometry is not committed; its source URL/hash/date, full809 classifications
before and after, transformer and software versions are retained in the compressed
reports. Their Git HEAD records the checkout used during each audit; the input SHA identifies
the actual working data snapshot.

## Source and physical-position limits

Current public operator directories were read for CircleK, Neste, Viada, EMSI,
BP, Saurida, Jozita and Alauša, plus Stateta/BRO's partner directory. Franchise
brands and legal-company LEA names differ; joining them by geography alone is
unsafe. The conservative matcher currently joins374/455 relevant source rows to
500 recorded directory entries, skips78 incomplete/different identities and
three genuinely ambiguous EMSI rows. Those are row/entry counts, not a count of
independently verified premises.

The OSM snapshot contains773 `amenity=fuel` objects, with source base time and
capture time in the archive. It is community evidence and may omit stations,
retain old brands or contain approximate geometry. Way/relation coordinates are
bounding-box centres. Proximity screens do not prove station identity; they are
used to find cases for address/brand/source review. Public GPS points here refer
to a station/premises area, not guaranteed driveways, pumps or survey points.

Outstanding or conflicting evidence remains explicit:

- ORLEN's current SharePoint locator could not be extracted (REST404, timeout,
  SOAP500). Four specific LEA/OSM premises corrections were applied; a current
  operator-directory audit of all30 ORLEN records is not claimed. Šilutės94's
  portal point appears to select another ORLEN at26A and was not imported.
- EMSI Sembos5 has conflicting old directory, newer locator and LEA points;
  nearby OSM fuel is labelled CPB. No physical identity was invented.
- Osijos Gėlyno25/Gėlių25 pins conflict without independent station evidence.
  See `boundary-osijos-gelyno25-pending.json`.
- Saurida Visaginas Kosmoso1's operator point is near Alauša Kosmoso3; current
  mapped Kosmoso1 has an older Saitema brand. No wholesale operator import.
- Andopas Ramučių43/Barzdūnai remains an approximate, price-less registry point.
- Older Pušaloto, Naftrus and Lašų overrides have current location corroboration,
  but house-number/brand/age discrepancies are recorded in `old-override-review.json`.
- Some operator EMSI/Saurida markers are hundreds of metres or kilometres from
  stronger address/premises evidence. An official origin alone is not accuracy.

Only sanitized geographic station records, source metadata and derived audit
results are saved. No raw operator HTML/scripts or browser/API credentials are
retained. `artifact-manifest.json` records compressed-evidence hashes. The offline
fixture hygiene regression scans this directory, including decompressed archives.

Validation includes 39 passing offline regressions, actual Pasvalys address
and stale-region shared-link behavior, preservation of nine newly coalesced
legacy favourites, unchanged price/raw-identity projection,
all809 official boundary classifications, static/API generation, and real browser
list/map popup/navigation inspection. Deployment/live checks are recorded in the
pull request; local tests alone do not prove publication.

The repair incorporates scheduled refresh commit `3e85d104` without rolling back
its import timestamps, oil, electricity, promotion or diagnostic updates. Its
station coordinates, raw identities and prices agree with the audit's initial
`437182cc` snapshot. Final preservation checks compare against `3e85d104`;
the first-batch application reports retain the earlier operation's hashes.
