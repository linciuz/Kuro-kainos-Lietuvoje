// Fuelis - service worker
// Network-first for code + data so updates always reach users (with offline
// cache fallback); cache-first only for SAME-ORIGIN images. Bump CACHE on
// shell changes.
const CACHE = "kk-v63";
const SHELL = [
  "./", "./index.html", "./app.js", "./i18n.js", "./manifest.webmanifest",
  "./icon-192.png", "./icon-512.png",
  "./vendor/leaflet/leaflet.js", "./vendor/leaflet/leaflet.css",
  "./vendor/leaflet/images/marker-icon.png", "./vendor/leaflet/images/marker-shadow.png"
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET") return;
  const url = new URL(e.request.url);

  // Never intercept cross-origin requests (OSM tiles, external APIs): the
  // browser's HTTP cache handles them. We used to cache-first map tiles into
  // the app cache — opaque responses are quota-padded, and ONE page load
  // hoarded 90+ MB, risking eviction of the whole origin (incl. saved prices).
  if (url.origin !== self.location.origin) return;

  // Cache-first for images (they rarely change). Only cache real 200s.
  if (/\.(png|jpe?g|svg|webp|ico|gif)$/i.test(url.pathname)) {
    e.respondWith(
      caches.match(e.request).then((r) => r || fetch(e.request).then((resp) => {
        if (resp && resp.ok) { const cp = resp.clone(); caches.open(CACHE).then((c) => c.put(e.request, cp)); }
        return resp;
      }))
    );
    return;
  }

  // Network-first for app code (app.js/index.html) and data (stations.json,
  // sources, discrepancies): always fetch fresh, fall back to cache offline.
  // Only cache OK (200) responses so a 404/error can't poison the offline copy.
  // Offline navigations fall back to the cached shell REGARDLESS of query
  // string (shared links like /?fuel=diesel&muni=... must open offline too).
  e.respondWith(
    fetch(e.request)
      .then((r) => {
        if (r && r.ok) { const cp = r.clone(); caches.open(CACHE).then((c) => c.put(e.request, cp)); }
        return r;
      })
      // Offline navigation: prefer the ACTUAL page if we have it, and only fall
      // back to the app shell if we don't. The old order tried index.html first,
      // which meant an offline visit to /kainos/kaunas.html or
      // /atviri-duomenys.html silently rendered the map app instead of the page
      // the user asked for. ignoreSearch still lets shared links like
      // /?fuel=diesel&muni=... match the cached "./" entry.
      .catch(() => (e.request.mode === "navigate"
        ? caches.match(e.request, { ignoreSearch: true }).then((r) => r || caches.match("./index.html"))
        : caches.match(e.request)))
  );
});
