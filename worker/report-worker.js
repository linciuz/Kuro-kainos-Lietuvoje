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
const TTL = 60 * 60 * 48;       // seconds

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status, headers: { ...CORS, "Content-Type": "application/json" },
  });
}

// Live EV occupancy proxy: the Lithuania NAP OCPI endpoint is open but blocks
// browser CORS, so we fetch it here and return a compact {ocpi_id: {a,t,s}} map
// (a=available, t=total connectors, s=overall status). Edge-cached ~45s.
const OCPI_LOCATIONS = "https://ev.vialietuva.lt/ocpi/2.3.0/locations";

async function evStatus() {
  const r = await fetch(OCPI_LOCATIONS, { headers: { "Accept": "application/json" } });
  if (!r.ok) return {};
  const body = await r.json();
  const out = {};
  for (const loc of (body.data || [])) {
    const evses = loc.evses || [];
    let avail = 0;
    for (const e of evses) if (e.status === "AVAILABLE") avail++;
    const total = evses.length;
    let s = "unknown";
    if (avail > 0) s = "available";
    else if (evses.some(e => e.status === "CHARGING" || e.status === "BLOCKED")) s = "busy";
    else if (evses.some(e => e.status === "OUTOFORDER" || e.status === "INOPERATIVE")) s = "down";
    out[String(loc.id)] = { a: avail, t: total, s };
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
        headers: { ...CORS, "Content-Type": "application/json", "Cache-Control": "public, max-age=45" },
      });
      ctx.waitUntil(cache.put(cacheKey, resp.clone()));
      return resp;
    }

    if (url.pathname === "/reports" && req.method === "GET") {
      const raw = await env.REPORTS.get(KEY);
      return json(raw ? JSON.parse(raw) : {});
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
};
