# Dynamic / announced loyalty-discount sources — feasibility (2026-07-07)

Investigation for an **optional, opt-in "announced deals" layer** on top of the
loyalty-card effective-price feature (`LOYALTY_*` / `kk_loyalty` in `app.js`).
Guiding rule (same as the base loyalty feature): **never display a price we
aren't certain is right.** So any layer must be opt-in, clearly labelled, and —
per the findings below — carry **no auto-scraped price number**.

## Verdict per source

| Source | Reachable without login? | Carries the discount **number**? | ToS / reliability risk | Usable? |
|---|---|---|---|---|
| **Baltic Petroleum app API** (`mobileapi.fscc.lt/bp`) | Stations + product catalog: **yes**. Prices/coupons: **no (401)** | No — every price is member-gated | Low ToS risk, but data simply absent | ❌ prices unreachable |
| **Viada app API** (`mobileapi.fscc.lt/viadalt`) | Same as BP: catalog open, prices/coupons **401** | No — member-gated | Low ToS risk, data absent | ❌ prices unreachable |
| **Viada Facebook** | Public feed: no API; scraping only | Sometimes in post text/image | **High** — against Meta ToS, fragile, blocked | ❌ do not use |
| **Viada website** (`viada.lt/akcija/…`) | **Yes** | **No** — figure is baked into the promo **image**; `akcija` type not in `wp-json` | Low ToS risk; marketing-grade, seasonal | ⚠️ **existence + link only** |

## Details

### Baltic Petroleum — re-check (was: prices need Cash Price+ login)
Confirmed still auth-gated, and stronger than before:
- `GET /bp/api/stations?pageSize=500` → **200**, stations + GPS + fuel **catalog** — **no prices**.
- `GET /bp/api/products` → **200**, 45 products (fuel names, car-wash programs) — **no prices, no discounts**.
- `GET /bp/api/stations/{id}` → **401**. `GET /bp/api/coupons` → **401**.
- Guessed promo/price endpoints (`/promotions`, `/campaigns`, `/prices`, `/price-list`, …) → **404**.
- **The Friday "special price announced only in the app" sits behind member login. Not reachable.**

### Viada app — new finding (shares BP's backend)
The Viada app (`com.mediapark.viada`; Latvian sibling `lv.fscc.viadalv`) runs on the
**same FSCC platform** as Baltic Petroleum. Tenant `mobileapi.fscc.lt/viadalt` is
**byte-for-byte the same shape** as `/bp`:
- `/api/stations`, `/api/products` → **200**, catalog only, no prices.
- `/api/stations/{id}`, `/api/coupons` → **401**.
- No `/promotions|/campaigns|/prices|/news` (all 404).

So **both chains' dynamic day-ahead prices are gated behind the same login wall.** The app path is a dead end for machine-readable promo/price data.

### Viada Facebook — not viable
- Facebook removed public Page RSS years ago.
- The Graph API can read a Page you don't own only with **Page Public Content Access**
  (App Review + business verification) — a third party can't get this for Viada's Page.
- HTML scraping of facebook.com is against Meta's ToS, heavily bot-blocked, and would
  break constantly. **Not pursued.**

### Viada website — reachable, but price-free only
- `wp-json` **is** open, but the `akcija` custom post type is **not** REST-exposed (404);
  only `posts`, `pages`, and the `apsimoka` (store-snack deals) / `skelbimai` types are.
- The `/akcijos/` listing is **pure imagery** — tiles have no title/alt text, just a
  "Daugiau" button. The actual **"−X ct litrui" figure is baked into the promo image**,
  not the HTML — so it **cannot be text-extracted reliably**. Auto-parsing a number here
  would be exactly the wrong-price risk we must avoid.
- What the website *does* give cleanly: the **existence** of officially-published fuel
  deals as titled links, e.g.:
  - **Nuolaidų savaitgaliai** — standing **weekends + public holidays** deal, ViadaPLUS, all fuels (business cards excluded). A *rule*, not a day-ahead one-off.
  - **VIADA STUDY / AGRO card** deals (STUDY page states −10 ct/L for any fuel).
  - **Super trečiadieniai** (the bigger Wednesday deal) — **seasonal**; its page is **404 right now**, i.e. not currently running.

## What was built: `scripts/fetch_viada_promos.py` → `data/sources/viada_promos.json`

A **price-free pointer** collector, following the `fetch_*.py` conventions (UA,
self-signed-TLS handling, previously-committed fallback, no clobber on empty):
- Probes a **curated** set of known fuel-loyalty pages (weekend / STUDY / AGRO, plus
  seasonal Super-Wednesday & weekend-travel slugs) for liveness.
- Auto-discovers **new** fuel promos on the live `/akcijos/` listing, but only flags a
  promo as fuel-related when its **main content** (chrome stripped) uses **per-litre
  pricing wording** (`litrui`, `ct/l`, `benzin`, `dyzelin`, `dujų`). Calibrated so it
  fires on the fuel pages and **excludes** every food/coffee/car-wash deal.
- Emits `{title, url, slug, fuel_related, kind, active}` with a top-level `disclaimer`
  and **deliberately no price/cents field anywhere.**

Current output: 4 pointers (3 standing fuel deals + the ViadaPLUS T&C page).

## Recommended app integration (opt-in, price-free) — NOT yet wired

Fits the existing `LOYALTY_*` design, which already ships **no baked-in numbers**:
- Gate on the same loyalty opt-in (`LOYALTY.enabled`). When on, show a small
  **"💳 Skelbiamos akcijos / Announced deals"** note on Viada station cards: the promo
  **title + an outbound link** to the official viada.lt page (and/or "check the Viada app").
- **Never** render a cents value from this file. It only tells the user *"Viada has a fuel
  deal today — open the official page/app to see it,"* and they confirm/enter the number in
  the existing loyalty input, exactly as today.
- To keep it fresh, add `python scripts/fetch_viada_promos.py` to `update-fuel-prices.yml`
  (daily is plenty — these change weekly at most).

## Bottom line
- **No source exposes the actual day-ahead discount number without a member login** (BP + Viada apps) or without violating ToS (Facebook).
- The only clean, safe signal is **Viada's own website promo pages**, and only as
  **labelled links** — which is what the scraper produces. Baltic Petroleum has **no**
  unauthenticated promo signal at all.
