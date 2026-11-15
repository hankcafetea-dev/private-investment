/* Service worker：讓網頁可以「加入主畫面」當 App 用，沒網路時也打得開
 *   網頁本體（HTML／JS／CSS／圖示）：先用快取、背景更新
 *   data/ 資料：一律先連網拿最新的，連不到才用上次的（私人資料本來就是加密檔）
 * VERSION 由 tools/build_site.py 依網頁本體內容自動帶入，內容一改就換新快取。 */
const VERSION = "__BUILD_VERSION__";
const SHELL = `shell-${VERSION}`;
const DATA = "data-v1";
const SHELL_FILES = [
  "./", "index.html", "app.js", "style.css", "manifest.webmanifest",
  "icons/apple-touch-icon.png", "icons/icon-192.png", "icons/icon-512.png", "icons/favicon-32.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith("shell-") && k !== SHELL).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;

  if (url.pathname.includes("/data/")) {
    // 資料：網路優先（不帶 ?t= 的網址存一份，離線時拿來用）
    const key = url.origin + url.pathname;
    e.respondWith(
      fetch(req).then((res) => {
        if (res.ok) { const copy = res.clone(); caches.open(DATA).then((c) => c.put(key, copy)); }
        return res;
      }).catch(() => caches.match(key).then((hit) => hit || new Response("{}", { status: 503, headers: { "Content-Type": "application/json" } }))),
    );
    return;
  }

  // 網頁本體：快取優先，同時背景抓新版
  e.respondWith(
    caches.open(SHELL).then(async (c) => {
      const hit = await c.match(req, { ignoreSearch: req.mode === "navigate" });
      const net = fetch(req).then((res) => { if (res.ok && url.search === "") c.put(req, res.clone()); return res; }).catch(() => null);
      return hit || (await net) || (req.mode === "navigate" ? c.match("index.html") : Response.error());
    }),
  );
});
