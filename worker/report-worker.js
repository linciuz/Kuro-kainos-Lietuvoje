// Kuro Kainos — "report a price" backend (Cloudflare Worker + KV).
// Free tier is plenty (100k reads + 1k writes/day).
//
// Endpoints (CORS open so the GitHub Pages app can call it):
//   GET  /reports         -> { "<stationKey>": { "<fuel>": { price, ts } }, ... }
//   POST /report  {station, fuel, price}  -> stores the user-reported price
//
// stationKey is "network|address|municipality" (built by the app).
// Reports are advisory: the app shows them with a caveat until the next
// official LEA update supersedes them. KV TTL bounds storage to 48h.

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};
const FUELS = ["petrol95", "diesel", "lpg"];
const KEY = "reports";
const MAX_STATIONS = 1000;      // bound the blob size
const TTL = 60 * 60 * 72;       // seconds; 72h so a Friday-evening report survives
                                // the no-LEA weekend until Monday's ~10:00 list
const VISITS_TOTAL = "visits:total";     // all-time visitor counter

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status, headers: { ...CORS, "Content-Type": "application/json" },
  });
}

// Live EV occupancy proxy: the Lithuania NAP OCPI endpoint is open but blocks
// browser CORS, so we fetch it here and return a compact {ocpi_id: {a,t,s}} map
// (a=available, t=total connectors, s=overall status). Edge-cached ~45s.
const OCPI_LOCATIONS = "https://ev.vialietuva.lt/ocpi/2.3.0/locations";

// The feed's last_updated stamps are NAIVE Europe/Vilnius local time. Build a
// comparable "now" the same way so age math is correct regardless of runtime tz.
function vilniusNowMs() {
  const s = new Date().toLocaleString("sv-SE", { timeZone: "Europe/Vilnius" });
  return Date.parse(s.replace(" ", "T"));
}

async function evStatus() {
  // The OCPI feed paginates (X-Total-Count ~2943, ~94 rows/page). Fetch the
  // first page to learn the total + real page size, then pull the rest with
  // bounded concurrency (was sequential = ~40s; now ~4-6s). Ids repeat across
  // pages — union the EVSEs.
  const sites = {};
  const H = { headers: { "Accept": "application/json" } };
  const collect = (batch) => {
    for (const loc of batch || []) {
      const id = String(loc.id);
      const slot = (sites[id] = sites[id] || { evses: [], ts: 0 });
      for (const e of loc.evses || []) {
        if (e.status === "REMOVED") continue;   // decommissioned — not a real point
        slot.evses.push(e);
        const t = Date.parse(e.last_updated || "") || 0;
        if (t > slot.ts) slot.ts = t;
      }
      const lt = Date.parse(loc.last_updated || "") || 0;
      if (lt > slot.ts) slot.ts = lt;
    }
  };
  const page = (offset) =>
    fetch(`${OCPI_LOCATIONS}?offset=${offset}&limit=1000`, H)
      .then((r) => (r.ok ? r.json() : { data: [] }))
      .then((j) => j.data || [])
      .catch(() => []);

  const firstR = await fetch(`${OCPI_LOCATIONS}?offset=0&limit=1000`, H);
  if (!firstR.ok) return {};
  const total = parseInt(firstR.headers.get("X-Total-Count") || "0", 10) || 0;
  const first = (await firstR.json()).data || [];
  collect(first);
  const step = first.length || 100;

  const offsets = [];
  for (let o = step; o < total; o += step) offsets.push(o);
  const CONC = 10;
  for (let i = 0; i < offsets.length; i += CONC) {
    const batches = await Promise.all(offsets.slice(i, i + CONC).map(page));
    batches.forEach(collect);
  }

  const out = {};
  const nowV = vilniusNowMs();
  for (const [id, slot] of Object.entries(sites)) {
    const evses = slot.evses;
    if (!evses.length) continue;               // all points removed — no status to show
    let avail = 0;
    for (const e of evses) if (e.status === "AVAILABLE") avail++;
    let s = "unknown";
    if (avail > 0) s = "available";
    else if (evses.some(e => e.status === "CHARGING" || e.status === "BLOCKED")) s = "busy";
    else if (evses.some(e => e.status === "OUTOFORDER" || e.status === "INOPERATIVE")) s = "down";
    const o = { a: avail, t: evses.length, s };
    // m = minutes since the operator last updated this site (data freshness).
    if (slot.ts) o.m = Math.max(0, Math.round((nowV - slot.ts) / 60000));
    out[id] = o;
  }
  return out;
}

export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    if (req.method === "OPTIONS") return new Response(null, { headers: CORS });

    if (url.pathname === "/ev-status" && req.method === "GET") {
      const cache = caches.default;
      const cacheKey = new Request(url.toString(), req);
      let hit = await cache.match(cacheKey);
      if (hit) return hit;
      const data = await evStatus();
      const resp = new Response(JSON.stringify(data), {
        headers: { ...CORS, "Content-Type": "application/json", "Cache-Control": "public, max-age=180" },
      });
      ctx.waitUntil(cache.put(cacheKey, resp.clone()));
      return resp;
    }

    if (url.pathname === "/reports" && req.method === "GET") {
      const raw = await env.REPORTS.get(KEY);
      return json(raw ? JSON.parse(raw) : {});
    }

    // User reports about EV chargers ("neveikia" / "veikia" counter-report).
    // Latest report per charger wins; whole blob expires after 48h so stale
    // complaints clear themselves once people stop renewing them.
    if (url.pathname === "/ev-reports" && req.method === "GET") {
      const raw = await env.REPORTS.get("evreports");
      return json(raw ? JSON.parse(raw) : {});
    }

    if (url.pathname === "/ev-report" && req.method === "POST") {
      let body;
      try { body = await req.json(); } catch { return json({ error: "bad json" }, 400); }
      const charger = (body && body.charger || "").toString();
      const status = body && body.status;
      const price = (body && body.price != null) ? Number(body.price) : null;
      if (!charger || charger.length > 200) return json({ error: "bad charger" }, 400);
      if (status == null && price == null) return json({ error: "empty report" }, 400);
      if (status != null && status !== "broken" && status !== "ok") return json({ error: "bad status" }, 400);
      if (price != null && !(price >= 0.05 && price <= 2)) return json({ error: "price out of range" }, 400);

      const raw = await env.REPORTS.get("evreports");
      const all = raw ? JSON.parse(raw) : {};
      const cur = all[charger] || {};
      if (status != null) { cur.s = status; cur.ts = Date.now(); }
      if (price != null) { cur.p = Math.round(price * 1000) / 1000; cur.pts = Date.now(); }
      all[charger] = cur;

      const keys = Object.keys(all);
      if (keys.length > 500) {                   // bound the blob size
        const newest = (o) => Math.max(o.ts || 0, o.pts || 0);
        keys.sort((a, b) => newest(all[a]) - newest(all[b]));
        for (const k of keys.slice(0, keys.length - 500)) delete all[k];
      }
      await env.REPORTS.put("evreports", JSON.stringify(all), { expirationTtl: TTL });
      return json({ ok: true });
    }

    // Visitor counter at one endpoint: POST /count (or /hit) logs a visit and
    // returns {total,today}; GET /count reads without incrementing (owner check).
    // The app POSTs at most once per device per day, so writes stay well within
    // the free tier. KV is eventually consistent → the total is approximate under
    // heavy concurrency, which is exactly right for a "visitors" number.
    if (url.pathname === "/count" || url.pathname === "/hit") {
      const today = new Date().toISOString().slice(0, 10);
      const dayKey = "visits:" + today;
      const [tot, day] = await Promise.all([
        env.REPORTS.get(VISITS_TOTAL),
        env.REPORTS.get(dayKey),
      ]);
      let total = parseInt(tot || "0", 10);
      let dayCount = parseInt(day || "0", 10);
      if (req.method === "POST") {
        total += 1; dayCount += 1;
        await Promise.all([
          env.REPORTS.put(VISITS_TOTAL, String(total)),
          env.REPORTS.put(dayKey, String(dayCount), { expirationTtl: 60 * 60 * 24 * 45 }),
        ]);
      }
      return json({ total, today: dayCount });
    }

    if (url.pathname === "/report" && req.method === "POST") {
      let body;
      try { body = await req.json(); } catch { return json({ error: "bad json" }, 400); }
      const station = (body && body.station || "").toString();
      const fuel = body && body.fuel;
      const price = Number(body && body.price);
      if (!station || station.length > 200) return json({ error: "bad station" }, 400);
      if (!FUELS.includes(fuel)) return json({ error: "bad fuel" }, 400);
      if (!(price >= 0.3 && price <= 3.5)) return json({ error: "price out of range" }, 400);

      const raw = await env.REPORTS.get(KEY);
      const all = raw ? JSON.parse(raw) : {};
      (all[station] = all[station] || {})[fuel] = {
        price: Math.round(price * 1000) / 1000,
        ts: Date.now(),
      };

      // Prune oldest stations if we exceed the cap.
      const keys = Object.keys(all);
      if (keys.length > MAX_STATIONS) {
        const newest = (o) => Math.max(...Object.values(o).map((v) => v.ts));
        keys.sort((a, b) => newest(all[a]) - newest(all[b]));
        for (const k of keys.slice(0, keys.length - MAX_STATIONS)) delete all[k];
      }

      await env.REPORTS.put(KEY, JSON.stringify(all), { expirationTtl: TTL });
      return json({ ok: true, station, fuel, price: all[station][fuel].price });
    }

    return json({ error: "not found" }, 404);
  },

  // Cron trigger (see wrangler.toml): GitHub's own scheduler has been dropping
  // most of its slots (2026-07-08/09: hour-plus gaps straight across LEA's 10:00
  // publication), so Cloudflare — whose crons fire reliably — pokes the price
  // workflow via workflow_dispatch instead. The workflow's concurrency group +
  // no-change commit skip make redundant pokes free.
  async scheduled(event, env, ctx) {
    if (!env.GH_TOKEN) return;
    ctx.waitUntil(fetch(
      "https://api.github.com/repos/linciuz/Kuro-kainos-Lietuvoje/actions/workflows/update-prices.yml/dispatches",
      {
        method: "POST",
        headers: {
          "Authorization": "Bearer " + env.GH_TOKEN,
          "Accept": "application/vnd.github+json",
          "Content-Type": "application/json",
          "User-Agent": "fuelis-cron-worker",
        },
        body: JSON.stringify({ ref: "main" }),
      },
    ));
  },
};
