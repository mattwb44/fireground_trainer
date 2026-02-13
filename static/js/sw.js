const CACHE_PREFIX = "fg-trainer";
const STATIC_CACHE = `${CACHE_PREFIX}-static-v2`;
const PAGE_CACHE = `${CACHE_PREFIX}-pages-v2`;

const params = new URL(self.location.href).searchParams;
const OFFLINE_URL = params.get("offline") || "/offline";
const manifestUrl = params.get("manifest");
const icon192Url = params.get("icon192");
const icon512Url = params.get("icon512");

const PRECACHE_URLS = [OFFLINE_URL, manifestUrl, icon192Url, icon512Url].filter(Boolean);
const ACTIVE_CACHES = new Set([STATIC_CACHE, PAGE_CACHE]);

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => cache.addAll(PRECACHE_URLS))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key.startsWith(CACHE_PREFIX) && !ACTIVE_CACHES.has(key))
          .map((key) => caches.delete(key))
      )
    ).then(() => self.clients.claim())
  );
});

async function networkFirstNavigation(request) {
  const pageCache = await caches.open(PAGE_CACHE);
  try {
    const response = await fetch(request);
    if (response && response.ok) {
      pageCache.put(request, response.clone());
    }
    return response;
  } catch (_err) {
    const cached = await pageCache.match(request);
    if (cached) return cached;

    const offlineFallback = await caches.match(OFFLINE_URL);
    if (offlineFallback) return offlineFallback;

    return new Response("Offline", {
      status: 503,
      headers: { "Content-Type": "text/plain; charset=utf-8" },
    });
  }
}

async function cacheFirstStatic(request) {
  const staticCache = await caches.open(STATIC_CACHE);
  const cached = await staticCache.match(request);
  if (cached) return cached;

  const response = await fetch(request);
  if (response && response.ok) {
    staticCache.put(request, response.clone());
  }
  return response;
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") {
    return;
  }

  const url = new URL(request.url);

  if (request.mode === "navigate") {
    event.respondWith(networkFirstNavigation(request));
    return;
  }

  if (url.origin === self.location.origin && url.pathname.startsWith("/static/")) {
    event.respondWith(cacheFirstStatic(request));
  }
});
