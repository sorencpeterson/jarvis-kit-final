/* Second Brain service worker (#91/#93): offline shell + last-good-state cache. */
const CACHE = 'brain-v7';
const SHELL = ['/', '/icons.svg', '/manifest.webmanifest', '/icon-192.png'];
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
const put = (req, r) => { if (r && r.ok) { const cp = r.clone(); caches.open(CACHE).then(c => c.put(req, cp)); } return r; };
self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  if (e.request.method !== 'GET') return;
  // signed public pages are living documents: never cache, never intercept
  if (/^\/(prop|mock|agree|case|delivered)\//.test(u.pathname)) return;
  if (u.pathname.startsWith('/api/') || u.pathname === '/') {
    // network-first, cache only GOOD responses, fall back to last good copy offline
    e.respondWith(fetch(e.request).then(r => put(e.request, r)).catch(() => caches.match(e.request)));
  } else {
    e.respondWith(caches.match(e.request).then(hit => hit || fetch(e.request).then(r => put(e.request, r))));
  }
});
