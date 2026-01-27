const CACHE_NAME = "fg-trainer-v1";

// Minimal cache: home page + icons.
// You can add images later once you know their filenames.
const ASSETS = [
  "/",
  "/static/pwa/manifest.json",
  "/static/pwa/ff-icon-192.png",
  "/static/pwa/ff-icon-512.png"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS))
  );
});

self.addEventListener("fetch", (event) => {
  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});
