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

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    if (req.method === "OPTIONS") return new Response(null, { headers: CORS });

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
