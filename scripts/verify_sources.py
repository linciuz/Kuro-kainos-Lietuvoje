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
import os
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


def check_scheduler(now):
    """Is the PRIMARY scheduler still alive? Cloudflare cron delivery has died
    twice (2026-07-17, and again after 07-23T22:00 — unnoticed for a day because
    the traffic dead-man silently covered it). The Worker's /health reports the
    last cron fire; a stale one means we are running on the backup layer only.
    WARN, never fail: prices are still fresh, but the redundancy is degraded and
    that must be visible. Network errors are ignored (this gate is local-first).
    """
    src = "scheduler"
    try:
        import urllib.request
        # An identifying UA is required — Cloudflare 403s UA-less requests.
        req = urllib.request.Request(
            "https://kk-reports.fuelis.workers.dev/health",
            headers={"User-Agent": "fuelis-gate/1.0 (+https://fuelis.lt)"})
        with urllib.request.urlopen(req, timeout=20) as r:
            h = json.load(r)
    except Exception as e:
        return warn(src, f"could not read Worker /health ({type(e).__name__}) — "
                         f"scheduler liveness unknown this run.")
    last = h.get("last_cron_fire")
    if not last:
        return warn(src, "Worker /health reports no cron fire on record — Cloudflare cron "
                         "delivery may be dead; the traffic dead-man is carrying updates.")
    try:
        age_h = (dt.datetime.now(dt.timezone.utc) - parse_utc(last)).total_seconds() / 3600
    except ValueError:
        return warn(src, f"unparseable last_cron_fire: {last!r}")
    # Crons run Mon-Fri only, so raw age is the wrong measure (a normal weekend
    # gap is ~62h, while a MISSED FRIDAY is only ~34h and must not hide behind a
    # weekend allowance — that is exactly what happened 2026-07-24). Instead:
    # find the most recent business day whose cron window has already passed;
    # at least one fire must have landed on or after that day's first slot.
    day = now.date()
    if not (is_business_day(day) and now.hour >= 12):
        day = prev_business_day(day)
    window_start = dt.datetime.combine(day, dt.time(4, 0), tzinfo=dt.timezone.utc)
    if parse_utc(last) < window_start:
        warn(src, f"no Cloudflare cron fire since {last} — nothing fired during {day}, "
                  f"a business day ({age_h:.0f}h ago). The PRIMARY scheduler is dead again; "
                  f"updates are riding on the traffic dead-man. Fix: change the cron set in "
                  f"worker/wrangler.toml and redeploy to force re-registration (that revived "
                  f"it on 2026-07-17).")
    disp = h.get("last_dispatch") or {}
    if disp.get("status") not in (None, 204):
        warn(src, f"last GitHub dispatch returned {disp.get('status')} {disp.get('error') or ''} — "
                  f"GH_TOKEN may be expired/revoked.")


def check_portal(now):
    """LEA's operator self-service portal (fetch_lea_portal.py). Today it is a
    COORDINATE source, not a price source (7% price coverage, 2026-07-27), so
    the rule guards the coordinate payload and watches adoption: if operators
    start submitting, this becomes a fresher price feed than the daily Excel
    and we should switch — that is a WARNING, not a failure."""
    src = "lea_portal"
    try:
        d = load("data/sources/lea_portal.json")
        gen = parse_utc(d["generated"])
    except Exception as e:
        return fail(src, f"unreadable lea_portal.json: {type(e).__name__}: {e}")
    if (d.get("with_coords") or 0) < 300:
        fail(src, f"only {d.get('with_coords')} stations with official coords (<300) — "
                  f"portal feed degraded; our pins fall back to geocoding.")
    age_h = (now - gen).total_seconds() / 3600
    if age_h > 96:      # refreshed by the DAILY workflow only
        fail(src, f"lea_portal.json generated {age_h:.0f}h ago (>96h) — the portal fetch "
                  f"has been failing (token rotated? API moved?).")
    share = 100 * (d.get("priced") or 0) / max(1, d.get("count") or 1)
    if share >= 25:
        warn(src, f"operators now self-report prices at {share:.0f}% of stations "
                  f"(was 7% on 2026-07-27) — the portal is becoming a real-time price feed; "
                  f"consider promoting it above the once-daily LEA Excel.")


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

def run_all_checks(now):
    """THE canonical rule set. Both the evaluate pass and any direct run go
    through here — a second copy of this list once drifted (Circle K's
    stated_lag_bd relaxation was applied to one copy only, 2026-07-22), which
    recorded a failure fingerprint for a condition the gate considered healthy
    and could burn an episode's only alarm. Never duplicate this list."""
    check_stations(now)
    check_viada(now)
    check_neste(now)
    # stated_lag_bd=1: Circle K bumps its page's own date only when prices
    # change (measured 2026-07-22: "Liepos 21" still shown at 12:14 the next
    # day, prices simply unchanged) — a one-business-day label lag is normal
    # operation, not a freeze. Day TWO of a frozen label still alarms.
    _check_daily_pricefile("circlek", "data/sources/circlek.json", now, stated_lag_bd=1)
    _check_daily_pricefile("circlek_biz", "data/sources/circlek_business.json", now, stated_lag_bd=1)
    _check_daily_pricefile("orlen", "data/sources/orlen_wholesale.json", now, stated_lag_bd=1)
    check_oil(now)
    check_electricity(now)
    check_ev(now)
    check_chain(now)
    check_portal(now)
    check_history(now)
    check_scheduler(now)


def main():
    now = now_vilnius()
    print(f"[verify_sources] {now:%Y-%m-%d %H:%M} Vilnius "
          f"({'business day' if is_business_day(now.date()) else 'weekend/holiday'})")

    run_all_checks(now)

    for w in WARNINGS:
        print(f"::warning::{w}")
    if FAILURES:
        fresh, repeats = split_by_episode(FAILURES, now)
        for f in fresh:
            print(f"::error::{f}")
        for f in repeats:
            print(f"::warning::[repeat — already alarmed this episode] {f}")
        if fresh:
            print(f"[verify_sources] {len(fresh)} NEW stale/corrupt finding(s) — failing the run "
                  f"so this cannot pass as success. ({len(repeats)} known repeat(s).)")
            return 1
        print(f"[verify_sources] {len(repeats)} known stale finding(s), all already alarmed this "
              f"episode — run stays green to avoid alarm spam; a NEW day or a NEW problem "
              f"re-alarms. Truth remains visible above, in /health, and in the app's date line.")
        return 0
    print(f"[verify_sources] all 12 sources fresh & sane"
          + (f" ({len(WARNINGS)} warning(s))" if WARNINGS else "") + ".")
    return 0


# --- one-red-per-episode damping ---------------------------------------------
# 2026-07-17 (LEA page outage + restored 15-min crons): an unhealable external
# failure produced an IDENTICAL red email every 15 minutes. Fix: the FIRST run
# that records a failure fingerprint goes red (the alarm); repeats of the same
# fingerprint downgrade to warnings on green runs. The state file is committed
# by the pipeline's normal `git add -A data/` — but that happens in the COMMIT
# step, which runs BEFORE this gate. So the workflows call `--record-state`
# pre-commit (writes the file, always exits 0) and the post-commit gate only
# READS it: a fingerprint first seen within the last 20 min (i.e. by THIS run's
# record step) alarms red; older ones are known repeats. Healing a source
# clears its fingerprints, ending the episode.
ALARM_STATE = os.path.join("data", "_alarm_state.json")
# Deliberately OUTSIDE data/ so it is never committed — it is a within-run
# handoff from the evaluate step to the final gate step.
GATE_VERDICT = "_gate_verdict.json"


def _fingerprint(msg, now):
    """Digit-runs normalized (ages/dates vary run-to-run) + keyed by business
    day: the same persisting problem alarms once per day, a different problem
    or a new day alarms immediately."""
    head = re.sub(r"\d+", "N", msg)[:140]
    return f"{head}|{required_date(now, 13).isoformat()}"


def _load_alarm_state():
    try:
        return json.load(open(ALARM_STATE, encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def split_by_episode(failures, now):
    """Split current failures into NEW (alarm red) vs already-alarmed repeats.
    A fingerprint counts as already-alarmed only if a PREVIOUS run persisted it
    (>20 min ago); anything this run just discovered is fresh."""
    state = _load_alarm_state()
    fresh, repeats = [], []
    for f in failures:
        first = state.get(_fingerprint(f, now))
        try:
            age_min = (now - dt.datetime.fromisoformat(first)).total_seconds() / 60 if first else 0
        except (TypeError, ValueError):
            age_min = 0
        (repeats if first and age_min > 20 else fresh).append(f)
    return fresh, repeats


def evaluate(now):
    """PRE-COMMIT pass: decide red/green ONCE, persist both the decision and the
    alarm state together, then let the commit step publish the state file.

    This replaces the old two-step design (a --record-state pass that guessed
    what the later gate would do). That guess was unsound: the state said "an
    alarm was emitted" whenever a fingerprint was WRITTEN, so any divergence
    between the two passes — or a gate step that never ran — silently burned an
    episode's only red. Now one evaluation produces both artifacts, and the
    final step just replays them.
    """
    run_all_checks(now)
    state = _load_alarm_state()
    fresh, repeats = split_by_episode(FAILURES, now)
    # Persist ONLY currently-failing fingerprints: a healed source drops out, so
    # a recurrence alarms again instead of being mistaken for an old episode.
    new_state = {}
    for f in FAILURES:
        fp = _fingerprint(f, now)
        new_state[fp] = state.get(fp) or now.isoformat()
    json.dump(new_state, open(ALARM_STATE, "w", encoding="utf-8"), indent=1)

    verdict = {
        "rc": 1 if fresh else 0,
        "errors": fresh,
        "warnings": WARNINGS + [f"[repeat — already alarmed this episode] {f}" for f in repeats],
        "checked_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
    }
    json.dump(verdict, open(GATE_VERDICT, "w", encoding="utf-8"), indent=1)
    print(f"[verify_sources] evaluated: {len(fresh)} new finding(s), {len(repeats)} known repeat(s), "
          f"{len(WARNINGS)} warning(s); state has {len(new_state)} active fingerprint(s).")
    return 0


def replay():
    """FINAL gate step: replay the pre-commit decision and exit with it.
    If the verdict file is missing (the evaluate step never ran), fall back to a
    LIVE run that alarms on ANY failure — erring toward noise, never silence."""
    try:
        v = json.load(open(GATE_VERDICT, encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        print("::warning::gate verdict missing — the evaluate step did not run; "
              "checking live and alarming on ANY failure (damping deliberately bypassed).")
        now = now_vilnius()
        run_all_checks(now)
        for w in WARNINGS:
            print(f"::warning::{w}")
        for f in FAILURES:
            print(f"::error::{f}")
        return 1 if FAILURES else 0
    for w in v.get("warnings") or []:
        print(f"::warning::{w}")
    for f in v.get("errors") or []:
        print(f"::error::{f}")
    if v.get("rc"):
        print(f"[verify_sources] {len(v.get('errors') or [])} NEW stale/corrupt finding(s) — "
              f"failing the run so this cannot pass as success.")
        return 1
    print("[verify_sources] no NEW findings; any known repeats are listed above as warnings. "
          "Truth stays visible in the annotations, /health, and the app's date line.")
    return 0


if __name__ == "__main__":
    if "--evaluate" in sys.argv or "--record-state" in sys.argv:   # old flag kept working
        sys.exit(evaluate(now_vilnius()))
    if "--replay" in sys.argv:
        sys.exit(replay())
    sys.exit(main())
