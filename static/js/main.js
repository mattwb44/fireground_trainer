(() => {
  if (!("serviceWorker" in navigator)) {
    return;
  }

  const scriptEl = document.currentScript || document.querySelector("script[data-sw-url]");
  if (!scriptEl) {
    return;
  }

  const swSrc = scriptEl.dataset.swUrl;
  if (!swSrc) {
    return;
  }

  const swUrl = new URL(swSrc, window.location.origin);
  const search = new URLSearchParams();

  const offlineUrl = scriptEl.dataset.offlineUrl;
  const manifestUrl = scriptEl.dataset.manifestUrl;
  const icon192Url = scriptEl.dataset.icon192Url;
  const icon512Url = scriptEl.dataset.icon512Url;

  if (offlineUrl) search.set("offline", offlineUrl);
  if (manifestUrl) search.set("manifest", manifestUrl);
  if (icon192Url) search.set("icon192", icon192Url);
  if (icon512Url) search.set("icon512", icon512Url);

  const query = search.toString();
  if (query) {
    swUrl.search = query;
  }

  window.addEventListener("load", () => {
    navigator.serviceWorker.register(swUrl.toString()).catch((err) => {
      console.warn("Service worker registration failed:", err);
    });
  });
})();
