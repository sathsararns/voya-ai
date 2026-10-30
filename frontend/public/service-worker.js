// Retirement worker for browsers that still have the old caching worker.
// No fetch handler: POST, authentication and chat requests go to the network.
self.addEventListener('install', (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    await self.registration.unregister();
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    await Promise.allSettled(windows
      .filter((client) => client.url.startsWith(self.registration.scope))
      .map((client) => client.navigate(client.url)));
  })());
});
