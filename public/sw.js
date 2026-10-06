// Offline shell. The page opens straight from this cache and is refreshed in the background
// (stale-while-revalidate), so a repeat visit does not wait for the network.
// Live data is never served from here: /stations, /health, /api and /reports always go to the network.
// (The page keeps its own last-known station list and labels it with its age; see savedStations() in app.js.)
// Bump the version below together with the ?v= on app.css / app.js in index.html: a test checks they match.
const SHELL = "nkb-shell-v56";
const FILES = ["./", "index.html", "app.css?v=56", "app.js?v=56", "report.html", "help.html", "privacy.html",
  "manifest.webmanifest", "icon.svg"];
const LIVE = /^\/(stations|health|api|reports)/;

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(FILES)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== SHELL && k !== "nkb-data").map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || u.origin !== location.origin || LIVE.test(u.pathname)) return;
  e.respondWith(caches.open(SHELL).then(async (cache) => {
    const hit = await cache.match(e.request);
    const refresh = fetch(e.request).then((r) => { if (r.ok) cache.put(e.request, r.clone()); return r; });
    if (hit) { e.waitUntil(refresh.catch(() => {})); return hit; }        // answer now, update for next time
    return refresh.catch(() => cache.match("index.html"));                 // never seen and offline: the app shell
  }));
});
