// Cache the app shell so the page opens offline. API data is never served from
// this cache: stale water levels are worse than an honest "can't connect" error.
const SHELL = "nkb-shell-v20";
const FILES = ["./", "index.html", "report.html", "help.html", "privacy.html", "manifest.webmanifest", "icon.svg"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(FILES)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || u.origin !== location.origin) return;
  if (/^\/(stations|health)/.test(u.pathname)) return;  // live data: network only
  e.respondWith(fetch(e.request).then((r) => {  // network first so UI updates ship immediately
    const copy = r.clone(); caches.open(SHELL).then((c) => c.put(e.request, copy)); return r;
  }).catch(() => caches.match(e.request).then((m) => m || caches.match("index.html"))));
});
