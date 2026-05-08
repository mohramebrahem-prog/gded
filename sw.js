// TG AI System - Service Worker v1.0
const CACHE = 'tg-ai-v1';
const ASSETS = [
  '/',
  '/static/index.html',
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // API calls — always network first
  if (url.pathname.startsWith('/api/') || url.pathname === '/ws') {
    e.respondWith(fetch(e.request).catch(() =>
      new Response(JSON.stringify({ detail: 'أنت غير متصل بالإنترنت' }), {
        headers: { 'Content-Type': 'application/json' }
      })
    ));
    return;
  }

  // Static assets — cache first, then network
  e.respondWith(
    caches.match(e.request).then(cached => {
      if (cached) return cached;
      return fetch(e.request).then(res => {
        if (res && res.status === 200 && res.type === 'basic') {
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
        }
        return res;
      }).catch(() => caches.match('/'));
    })
  );
});
