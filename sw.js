// 오프라인용 서비스 워커: 처음 열 때 앱 전체(파이썬 엔진 포함)를 기기에 저장하고, 이후에는 저장본으로 동작
const CACHE = "pyj-ea3d1c6097";
const ASSETS = ["./", "index.html", "worker.js", "runner.py", "manifest.webmanifest", "icon-180.png", "icon-192.png", "icon-512.png", "pyodide.mjs", "pyodide.asm.mjs", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json"];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE)
      .then((c) => c.addAll(ASSETS.map((a) => new Request(a, { cache: "reload" }))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((ks) => Promise.all(ks.filter((k) => k.startsWith("pyj-") && k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  e.respondWith((async () => {
    const c = await caches.open(CACHE);
    const hit = await c.match(req, { ignoreSearch: true });
    if (hit) return hit;
    if (req.mode === "navigate") {
      const idx = (await c.match(new URL("index.html", self.registration.scope).href)) || (await c.match(self.registration.scope));
      if (idx) return idx;
    }
    try {
      return await fetch(req);
    } catch (err) {
      return new Response("오프라인 상태이고 저장된 파일이 없어요.", { status: 503, headers: { "Content-Type": "text/plain; charset=utf-8" } });
    }
  })());
});
