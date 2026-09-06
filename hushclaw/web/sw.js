const CACHE = "hushclaw-v31";
const STATIC = [
  "/",
  "/index.html",
  "/ui-lab.html",
  "/app.js",
  "/react-dist/react-islands.js",
  "/react-dist/react-islands.css",
  "/style.css",
  "/styles/theme-modes.css",
  "/styles/chat-theme.css",
  "/styles/markdown-tight.css",
  "/styles/markdown-system.css",
  "/styles/panels-app-connectors.css",
  "/styles/share-card.css",
  "/styles/startup.css",
  "/styles/panels.css",
  "/styles/panels-insights.css",
  "/styles/panels-files.css",
  "/styles/settings.css",
  "/styles/modal.css",
  "/styles/calendar.css",
  "/styles/responsive.css",
  "/styles/product-pages.css",
  "/styles/harness-shell.css",
  "/styles/ui-foundations.css",
  "/styles/chat-product.css",
  "/styles/ai-primitives.css",
  "/styles/ui-lab.css",
  "/modules/ui-lab.js",
  "/modules/ui/ai-primitives.js",
  "/modules/ui/composer-menu.js",
  "/modules/ui/selection-actions.js",
  "/modules/shell.js",
  "/manifest.json",
  "/icon.svg",
  "/favicon-32.png",
  "/favicon-192.png",
  "/favicon-512.png",
];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(STATIC)));
  self.skipWaiting();
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
  ));
  self.clients.claim();
});

self.addEventListener("message", e => {
  if (e.data && e.data.type === "SKIP_WAITING") {
    self.skipWaiting();
  }
});

async function networkFirst(req) {
  try {
    const res = await fetch(req, { cache: "no-store" });
    if (res && res.ok) {
      const c = await caches.open(CACHE);
      c.put(req, res.clone());
    }
    return res;
  } catch (_e) {
    const cached = await caches.match(req);
    if (cached) return cached;
    throw new Error("network and cache missed");
  }
}

self.addEventListener("fetch", e => {
  // Only intercept same-origin GET requests; skip WebSocket and API calls
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.host !== location.host) return;
  if (url.pathname.startsWith("/files/") || url.pathname.startsWith("/upload")) return;
  e.respondWith(
    (async () => {
      try {
        return await networkFirst(e.request);
      } catch (_e) {
        if (e.request.mode === "navigate") {
          return (await caches.match("/index.html")) || (await caches.match("/"));
        }
        return new Response("Offline", { status: 503, statusText: "Offline" });
      }
    })()
  );
});
