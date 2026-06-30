// Kuro Kainos Lietuvoje - service worker
const CACHE = "kk-v1";
const SHELL = [
  "./", "./index.html", "./app.js", "./manifest.webmanifest",
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

  // Always try the network first for prices so data stays current.
  if (url.pathname.endsWith("stations.json")) {
    e.respondWith(
      fetch(e.request)
        .then((r) => { const cp = r.clone(); caches.open(CACHE).then((c) => c.put(e.request, cp)); return r; })
        .catch(() => caches.match(e.request))
    );
    return;
  }

  // Cache-first for the app shell (works offline).
  e.respondWith(caches.match(e.request).then((r) => r || fetch(e.request)));
});
