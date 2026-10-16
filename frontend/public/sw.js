// Service worker mínimo: permite instalar o painel como app. Dados do bot NUNCA são guardados em cache
// (sempre ao vivo); só a casca do app é reaproveitada se a rede cair.
const SHELL = "quantbot-shell-v1";
self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(["/icon-192.png", "/manifest.webmanifest"])));
  self.skipWaiting();
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== SHELL).map((k) => caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.pathname.startsWith("/api/")) return; // API: sempre rede
  if (e.request.mode === "navigate") {
    e.respondWith(fetch(e.request).catch(() => new Response(
      "<meta name=viewport content='width=device-width'><body style='background:#0b0e14;color:#e6e9ef;font-family:sans-serif;padding:24px'>" +
      "<h3>Sem conexão</h3><p>Não foi possível falar com o painel. Verifique a internet do celular e se o PC do bot está ligado.</p>",
      { headers: { "Content-Type": "text/html; charset=utf-8" } })));
  }
});
