const CACHE_VERSION = 'markup-v1';
const SHARE_CACHE = 'markup-share-v1';
const SHARE_KEY = '/__oqb_markup_shared_image__';

self.addEventListener('install', event => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names
      .filter(name => name.startsWith('markup-') && ![CACHE_VERSION, SHARE_CACHE].includes(name))
      .map(name => caches.delete(name)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  const isShareTarget = event.request.method === 'POST'
    && url.pathname === '/admin/toolbox/markup/share-target';

  if (!isShareTarget) return;

  event.respondWith((async () => {
    try {
      const formData = await event.request.formData();
      const file = formData.get('image');
      if (file && typeof file.arrayBuffer === 'function') {
        const cache = await caches.open(SHARE_CACHE);
        await cache.put(SHARE_KEY, new Response(file, {
          headers: {
            'Content-Type': file.type || 'application/octet-stream',
            'X-OQB-Filename': encodeURIComponent(file.name || 'shared-image')
          }
        }));
      }
    } catch (err) {
      // Continue to the app even if the shared file could not be cached.
    }
    return Response.redirect('/admin/toolbox/markup?shared=1', 303);
  })());
});
