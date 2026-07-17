#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Local-file staleness & integrity gate — the "never again" system.

Runs as the FINAL step of both data workflows (and standalone anytime). Reads
ONLY committed files + the clock (no network), so it catches the failure class
that network checks structurally can't: a source site blocking the runner while
every fetch step shrugs it off with "|| echo non-fatal" and the run ends green.
That exact pattern silently starved the Viada Wednesday promo for a week
(viada.lt bot-block, 2026-07-08 → 07-15) and hid a stale LEA day earlier.

For each data file there is one mechanically-checkable rule (derived from a
2026-07-15 ten-source audit): when must fresh data exist, given that LEA and
our crons run Mon–Fri only, promos matter only on Wednesdays, and Lithuanian
public holidays suppress publication. Any violation prints ::error:: and the
step FAILS the run → red X + email. False-alarm avoidance is part of each
rule's design (weekends, holidays, pre-publication mornings never fire).

Exit codes: 0 all fresh/sane; 1 at least one source stale or corrupt.
"""

import datetime as dt
import json
import math
import re
import sys
from zoneinfo import ZoneInfo

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

VILNIUS = ZoneInfo("Europe/Vilnius")

FAILURES, WARNINGS = [], []


def fail(src, msg):
    FAILURES.append(f"[{src}] {msg}")


def warn(src, msg):
    WARNINGS.append(f"[{src}] {msg}")


def load(path):
    return json.load(open(path, encoding="utf-8"))


# ---------------------------------------------------------------- calendar --

def easter_sunday(year):
    """Gregorian computus (Meeus/Jones/Butcher)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)


def lt_holidays(year):
    """Lithuanian public holidays (LEA does not publish, our crons still run)."""
    fixed = [(1, 1), (2, 16), (3, 11), (5, 1), (6, 24), (7, 6),
             (8, 15), (11, 1), (11, 2), (12, 24), (12, 25), (12, 26)]
    days = {dt.date(year, m, d) for m, d in fixed}
    e = easter_sunday(year)
    days.add(e)                            # Velykos
    days.add(e + dt.timedelta(days=1))     # antroji Velykų diena
    return days


def is_business_day(d):
    return d.weekday() < 5 and d not in lt_holidays(d.year)


def prev_business_day(d):
    d -= dt.timedelta(days=1)
    while not is_business_day(d):
        d -= dt.timedelta(days=1)
    return d


def now_vilnius():
    return dt.datetime.now(VILNIUS)


def required_date(now, cutoff_hour):
    """The newest business date whose data MUST already be committed at `now`:
    today once past the cutoff on a business day, else the previous business
    day. Weekends/holidays therefore expect Friday's (pre-holiday's) data."""
    today = now.date()
    if is_business_day(today) and now.hour >= cutoff_hour:
        return today
    return prev_business_day(today)


def parse_utc(ts):
    """'2026-07-15T08:17:10Z' -> aware UTC datetime."""
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))


def sane(v, lo=0.3, hi=3.5):
    return isinstance(v, (int, float)) and math.isfinite(v) and lo < v < hi


# Lithuanian month names (stem match) for parsing pages' own stated dates.
LT_MONTHS = {"saus": 1, "vas": 2, "kov": 3, "baland": 4, "geguz": 5, "birz": 6,
             "liep": 7, "rugpj": 8, "rugs": 9, "spal": 10, "lapkri": 11, "gruod": 12}
_DEACC = str.maketrans("ąčęėįšųūž", "aceeisuuz")


def parse_stated_date(txt, ref):
    """Upstream pages state their price date as 'Liepos 16' or '16.07' (no
    year). Resolve against ref (today), rolling the year back across Jan 1.
    Returns a date or None - an unknown format must not fail the gate."""
    if not txt:
        return None
    txt = str(txt).strip().lower().translate(_DEACC)
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.?$", txt)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
    else:
        m = re.match(r"^([a-z]+)\s+(\d{1,2})", txt)
        if not m:
            return None
        month = next((n for stem, n in LT_MONTHS.items() if m.group(1).startswith(stem)), None)
        if not month:
            return None
        day = int(m.group(2))
    try:
        d = dt.date(ref.year, month, day)
    except ValueError:
        return None
    if d > ref + dt.timedelta(days=7):     # December date read on Jan 2
        d = d.replace(year=ref.year - 1)
    return d


# ----------------------------------------------------------------- sources --

def check_stations(now):
    src = "stations"
    try:
        d = load("data/stations.json")
        updated = dt.date.fromisoformat(d["updated"])
        stations = d["stations"]
        summary = d["summary"]
    except Exception as e:
        return fail(src, f"unreadable/malformed data/stations.json: {type(e).__name__}: {e}")
    # Parse-collapse guards (healthy file: ~660 priced + ~150 registry = ~800;
    # ~630 priced stations per fuel).
    if len(stations) < 400:
        fail(src, f"only {len(stations)} stations — parse collapse?")
    for f in ("petrol95", "diesel"):
        cnt = (summary.get(f) or {}).get("count", 0)
        if cnt < 300:
            fail(src, f"summary.{f}.count={cnt} (<300) — prices failed to parse?")
    # LEA publishes ~11:00-12:00 LT on business days; by 13:00 we must have today.
    need = required_date(now, cutoff_hour=13)
    if updated < need:
        fail(src, f"official prices are from {updated}, but {need} data must exist by now "
                  f"(LEA business-day rule) - users are seeing outdated prices.")
    # Plausibility bands: a column shift at LEA (e.g. a pre-tax price column)
    # would parse cleanly and publish ~25% low - averages must stay in band.
    BANDS = {"petrol95": (1.2, 2.6), "diesel": (1.2, 2.6), "lpg": (0.4, 1.2)}
    for f, (lo, hi) in BANDS.items():
        avg = (summary.get(f) or {}).get("avg")
        if avg is not None and not (lo <= avg <= hi):
            fail(src, f"summary.{f}.avg={avg} outside plausible band {lo}-{hi} EUR/l - "
                      f"column shift / unit change at LEA?")
    # Day-over-day continuity: real LT moves are cents; >8% in one step is a
    # data accident, not a market move.
    try:
        hist = load("data/price_history.json")["history"]
        prev_entry = next((h for h in reversed(hist) if h.get("date") < d["updated"]), None)
        for f in BANDS:
            cur = (summary.get(f) or {}).get("avg")
            prv = ((prev_entry or {}).get(f) or {}).get("avg")
            if cur and prv and abs(cur - prv) / prv > 0.08:
                fail(src, f"{f} national avg jumped {100 * (cur - prv) / prv:+.1f}% vs {prev_entry.get('date')} "
                          f"({prv} -> {cur}) - implausible one-step move; refusing to bless it.")
    except Exception:
        pass   # missing/short history must not block the price gate itself


def check_viada(now):
    src = "viada"
    try:
        d = load("data/sources/viada_promos.json")
        gen = parse_utc(d["generated"])
    except Exception as e:
        return fail(src, f"unreadable viada_promos.json: {type(e).__name__}: {e}")
    today = now.date()
    # Wednesday promo: if the Super trečiadieniai page exists (pointer present),
    # today's absolute prices must be in place by noon LT — this is the exact
    # user-visible regression of the 07-15 incident. When the seasonal promo
    # ends, its page 404s and the pointer drops out → clause skips (no alarm).
    has_wed_pointer = any(p.get("slug") == "super-treciadieniai" for p in d.get("promos", []))
    if today.weekday() == 2 and now.hour >= 12 and has_wed_pointer:
        wed = d.get("wednesday") or {}
        if wed.get("valid_date") != today.isoformat():
            fail(src, f"it is Wednesday {today} after noon LT and the Super trečiadieniai "
                      f"prices are for '{wed.get('valid_date')}' — the Wednesday discount is "
                      f"missing/stale in the app (runner blocked? page format change?).")
        else:
            pr = wed.get("prices") or {}
            if not any(sane(v) for v in pr.values()):
                fail(src, f"Wednesday prices present but implausible: {pr}")
    # Rot backstop, any day: fresh probes rewrite `generated` every weekday run,
    # so >96h (covers weekend + one holiday Monday) means the fetch has been
    # failing repeatedly — the silent-block signature, caught before Wednesday.
    age_h = (now - gen).total_seconds() / 3600
    if age_h > 96:
        fail(src, f"viada_promos.json generated {age_h:.0f}h ago (>96h) — every recent "
                  f"fetch failed (stale-keep loop; see data/sources/viada_debug.json).")
    elif d.get("generated_stale_kept") and age_h > 24:
        warn(src, f"stale-keep fallback active and file already {age_h:.0f}h old — "
                  f"direct+proxy fetches are failing on the runner.")


def check_neste(now):
    src = "neste"
    try:
        d = load("data/sources/neste_promo.json")
        gen = parse_utc(d["generated"])
    except Exception as e:
        return fail(src, f"unreadable neste_promo.json: {type(e).__name__}: {e}")
    today = now.date()
    if today.weekday() == 2 and now.hour >= 12:
        if d.get("valid_date") != today.isoformat():
            fail(src, f"Wednesday after noon LT but Nuolaidadienis valid_date is "
                      f"'{d.get('valid_date')}' — the Neste Wednesday discount is stale/missing.")
        else:
            c = d.get("cents")
            if not (isinstance(c, (int, float)) and 0 < c <= 30):
                fail(src, f"Nuolaidadienis cents implausible: {c!r}")
    age_h = (now - gen).total_seconds() / 3600
    if age_h > 96:
        fail(src, f"neste_promo.json generated {age_h:.0f}h ago (>96h) — fetch failing repeatedly.")


def _check_daily_pricefile(src, path, now, fuels=("petrol95", "diesel"), stated_lag_bd=0):
    """Shared rule for circlek / circlek_biz / orlen: `fetched` (ISO date,
    re-stamped on every successful run) must reach the newest business date by
    noon LT; petrol95+diesel must be plausible; and the page's OWN stated date
    must be current too - `fetched` alone only proves the scraper ran, not that
    the upstream moved (a frozen page would pass forever). stated_lag_bd:
    business days the upstream legitimately lags (Orlen posts prior-day)."""
    try:
        d = load(path)
        fetched = dt.date.fromisoformat(d["fetched"])
    except Exception as e:
        return fail(src, f"unreadable {path}: {type(e).__name__}: {e}")
    for f in fuels:
        if not sane((d.get("prices") or {}).get(f)):
            fail(src, f"prices.{f} missing/implausible: {(d.get('prices') or {}).get(f)!r}")
    need = required_date(now, cutoff_hour=12)
    if fetched < need:
        fail(src, f"fetched={fetched} but {need} data must exist by now - "
                  f"scraper failing silently (site change/block?).")
    stated = parse_stated_date(d.get("stated_date"), now.date())
    if stated is not None:
        allowed = need
        for _ in range(stated_lag_bd):
            allowed = prev_business_day(allowed)
        if stated < allowed:
            fail(src, f"upstream page still shows its own date as {stated} "
                      f"(needs >= {allowed}) - the SITE is frozen even though the "
                      f"scraper runs fine; users see an outdated reference price.")


def check_oil(now):
    src = "oil"
    try:
        d = load("data/oil.json")
        updated = dt.date.fromisoformat(d["updated"])
    except Exception as e:
        return fail(src, f"unreadable data/oil.json: {type(e).__name__}: {e}")
    if d.get("level") not in {"strong_up", "rise", "stable", "fall", "strong_down"}:
        fail(src, f"level invalid: {d.get('level')!r}")
    if len(d.get("history") or []) < 6:
        fail(src, f"history has {len(d.get('history') or [])} entries (<6)")
    if not (isinstance(d.get("price"), (int, float)) and 20 < d["price"] < 300):
        fail(src, f"Brent price implausible: {d.get('price')!r}")
    need = required_date(now, cutoff_hour=12)
    if updated < need:
        fail(src, f"updated={updated} but {need} expected - oil fetch failing silently.")
    # Frozen-upstream guard: the newest HISTORY point is the market's own clock.
    # Trading calendar differs from LT holidays - allow 2 business days of lag.
    try:
        last_close = dt.date.fromisoformat(d["history"][-1]["date"])
        if last_close < prev_business_day(prev_business_day(need)):
            fail(src, f"newest Brent close is {last_close} - the price API is serving "
                      f"frozen data (our fetch runs, the market feed does not move).")
    except (KeyError, IndexError, ValueError, TypeError):
        pass


def check_electricity(now):
    src = "electricity"
    try:
        d = load("data/electricity.json")
        ts = parse_utc(d["updated"])
    except Exception as e:
        return fail(src, f"unreadable data/electricity.json: {type(e).__name__}: {e}")
    for f in ("current_ct_kwh", "current_eur_mwh", "week_avg_ct_kwh", "week_avg_eur_mwh"):
        v = d.get(f)
        if not (isinstance(v, (int, float)) and math.isfinite(v)):   # negative spot is valid
            fail(src, f"{f} missing/non-finite: {v!r}")
    # Business day past noon LT: demand a stamp from after 08:00 LT TODAY.
    # Timestamp (not date) compare — the ~01:00 LT night run must not satisfy
    # the noon check, or a since-morning breakage would hide behind it.
    if is_business_day(now.date()) and now.hour >= 12:
        morning = now.replace(hour=8, minute=0, second=0, microsecond=0)
        if ts.astimezone(VILNIUS) < morning:
            fail(src, f"updated={d['updated']} predates today 08:00 LT - "
                      f"every fetch since morning has failed.")
    # Frozen-upstream guard: Nord Pool day-ahead publishes EVERY day, so the
    # newest market point must be recent regardless of weekday. (Field appears
    # once the updated fetcher ships - tolerate its absence meanwhile.)
    lp = d.get("last_point_utc")
    if lp:
        try:
            age_h = (now - parse_utc(lp)).total_seconds() / 3600
            if age_h > 30:
                fail(src, f"newest market point is {age_h:.0f}h old (>30h) - the Elering "
                          f"feed is frozen even though our fetch runs.")
        except ValueError:
            fail(src, f"last_point_utc unparseable: {lp!r}")


def check_ev(now):
    src = "ev"
    try:
        d = load("data/sources/ev_chargers.json")
        gen = parse_utc(d["generated"])
        chargers = d["chargers"]
    except Exception as e:
        return fail(src, f"unreadable ev_chargers.json: {type(e).__name__}: {e}")
    if len(chargers) < 1000:
        fail(src, f"only {len(chargers)} chargers (<1000) — OCPI fetch partially collapsed.")
    ocpi = d.get("ocpi_count") or 0
    if ocpi < 500:
        fail(src, f"ocpi_count={ocpi} (<500) — live OCPI AND its fallback gone.")
    elif (d.get("with_price") or 0) < 0.5 * ocpi:
        fail(src, f"with_price={d.get('with_price')} < half of ocpi_count={ocpi} — tariff parse broke.")
    age_h = (now - gen).total_seconds() / 3600
    if age_h > 96:   # 2 runs/business day; 96h clears weekend + holiday Monday
        fail(src, f"ev_chargers.json generated {age_h:.0f}h ago (>96h) - directory refresh failing.")
    # Per-source health (present once the updated fetcher ships): carried-forward
    # data with a fresh run stamp must not hide a dead upstream indefinitely.
    for name, h in (d.get("source_health") or {}).items():
        ls = h.get("last_success_utc")
        if not ls:
            continue
        try:
            days = (now - parse_utc(ls)).total_seconds() / 86400
        except ValueError:
            continue
        if days > 21:
            fail(src, f"source '{name}' last fetched LIVE {days:.0f} days ago (>21) - "
                      f"the app serves only carried-forward {name} data.")
        elif days > 7:
            warn(src, f"source '{name}' has not answered live in {days:.0f} days - "
                      f"running on carried-forward data.")


def check_chain(now):
    src = "chain"
    try:
        d = load("data/sources/chain_stations.json")
        gen = parse_utc(d["generated"])
    except Exception as e:
        return fail(src, f"unreadable chain_stations.json: {type(e).__name__}: {e}")
    if (d.get("count") or 0) < 100:
        fail(src, f"only {d.get('count')} chain stations (<100) - directory collapsed.")
    age_h = (now - gen).total_seconds() / 3600
    if age_h > 96:
        fail(src, f"chain_stations.json generated {age_h:.0f}h ago (>96h) - daily refresh failing.")
    ls = d.get("last_success_utc")
    if ls:
        try:
            days = (now - parse_utc(ls)).total_seconds() / 86400
            if days > 21:
                fail(src, f"no chain answered a LIVE fetch in {days:.0f} days (>21) - "
                          f"all coordinates are carried-forward.")
            elif days > 7:
                warn(src, f"no live chain fetch in {days:.0f} days - running on carried-forward directories.")
        except ValueError:
            pass


def check_history(now):
    src = "history"
    try:
        h = load("data/price_history.json")["history"]
        s_updated = load("data/stations.json")["updated"]
    except Exception as e:
        return fail(src, f"unreadable price_history/stations json: {type(e).__name__}: {e}")
    if not isinstance(h, list) or not h:
        return fail(src, "history array missing/empty")
    last = h[-1].get("date")
    if last != s_updated:
        fail(src, f"history last entry {last} != published stations date {s_updated} — "
                  f"today's snapshot was never appended (trend chart silently frozen).")
    try:
        days = (now.date() - dt.date.fromisoformat(last)).days
        if days > 5:
            fail(src, f"history last entry {last} is {days} days old (>5) — pipeline stalled.")
    except Exception:
        fail(src, f"history last date unparseable: {last!r}")


# -------------------------------------------------------------------- main --

def main():
    now = now_vilnius()
    print(f"[verify_sources] {now:%Y-%m-%d %H:%M} Vilnius "
          f"({'business day' if is_business_day(now.date()) else 'weekend/holiday'})")

    check_stations(now)
    check_viada(now)
    check_neste(now)
    _check_daily_pricefile("circlek", "data/sources/circlek.json", now)
    _check_daily_pricefile("circlek_biz", "data/sources/circlek_business.json", now)
    _check_daily_pricefile("orlen", "data/sources/orlen_wholesale.json", now, stated_lag_bd=1)
    check_oil(now)
    check_electricity(now)
    check_ev(now)
    check_chain(now)
    check_history(now)

    for w in WARNINGS:
        print(f"::warning::{w}")
    if FAILURES:
        for f in FAILURES:
            print(f"::error::{f}")
        print(f"[verify_sources] {len(FAILURES)} source(s) STALE/CORRUPT — failing the run "
              f"so this cannot pass as success.")
        return 1
    print(f"[verify_sources] all 11 sources fresh & sane"
          + (f" ({len(WARNINGS)} warning(s))" if WARNINGS else "") + ".")
    return 0


if __name__ == "__main__":
    sys.exit(main())
