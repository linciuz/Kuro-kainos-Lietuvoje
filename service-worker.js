// Kuro Kainos Lietuvoje - service worker
// Network-first for code + data so updates always reach users (with offline
// cache fallback); cache-first only for images. Bump CACHE on shell changes.
const CACHE = "kk-v12";
const SHELL = [
  "./", "./index.html", "./app.js", "./i18n.js", "./manifest.webmanifest",
  "./icon-192.png", "./icon-512.png"
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

  // Cache-first for images (they rarely change).
  if (/\.(png|jpe?g|svg|webp|ico|gif)$/i.test(url.pathname)) {
    e.respondWith(
      caches.match(e.request).then((r) => r || fetch(e.request).then((resp) => {
        const cp = resp.clone();
        caches.open(CACHE).then((c) => c.put(e.request, cp));
        return resp;
      }))
    );
    return;
  }

  // Network-first for app code (app.js/index.html) and data (stations.json,
  // sources, discrepancies): always fetch fresh, fall back to cache offline.
  // Only cache OK (200) responses so a 404/error can't poison the offline copy.
  e.respondWith(
    fetch(e.request)
      .then((r) => {
        if (r && r.ok) { const cp = r.clone(); caches.open(CACHE).then((c) => c.put(e.request, cp)); }
        return r;
      })
      .catch(() => caches.match(e.request))
  );
});
