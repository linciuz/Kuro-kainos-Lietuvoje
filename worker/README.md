# Report-a-price backend (Cloudflare Worker)

A tiny free serverless endpoint that stores user-reported fuel prices so all
app users see them. Free tier (100k reads / 1k writes per day) is far more than
enough.

## Deploy (one time, ~5 min)

```bash
npm i -g wrangler          # or use npx wrangler
cd worker
wrangler login             # opens browser; needs a free Cloudflare account

# 1) create the KV store and copy the printed id into wrangler.toml
wrangler kv namespace create REPORTS

# 2) deploy
wrangler deploy
```

`wrangler deploy` prints your Worker URL, e.g.
`https://kk-reports.<your-subdomain>.workers.dev`.

## Wire it into the app

Put that URL in `app.js`:

```js
const REPORT_API = "https://kk-reports.<your-subdomain>.workers.dev";
```

Commit & push — GitHub Pages redeploys and the "Pranešti kainą" (report price)
button activates. If `REPORT_API` is left empty, the button is hidden and the
app works exactly as before.

## API

- `GET  /reports` → `{ "network|address|municipality": { "petrol95": {price, ts}, ... } }`
- `POST /report`  `{ station, fuel, price }` → stores it (validates fuel + 0.3–3.5 €/L range)
- `GET  /ev-status` → live EV occupancy `{ "<ocpi_id>": {a, t, s} }` (a=available,
  t=total, s=available|busy|down). Proxies Lithuania's official OCPI feed
  (`ev.vialietuva.lt`), which is open but blocks browser CORS. Edge-cached ~45s.

Reports are advisory: the app shows a reported price with a caveat and lets the
next official LEA update supersede it. Stored values expire after 48h. Once the
Worker URL is in `REPORT_API`, the EV tab also shows 🟢/🔴 live availability.
