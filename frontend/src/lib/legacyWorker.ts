// This app does not use offline service workers. Retire old root workers before
// mounting React, so cached session checks cannot override live authentication.
export async function retireLegacyWorker(reload = () => window.location.reload()): Promise<boolean> {
  if (!('serviceWorker' in navigator)) return false
  const base = new URL(import.meta.env.BASE_URL, window.location.origin)
  const knownPaths = ['service-worker.js', 'sw.js'].map((name) => new URL(name, base).pathname)
  const isLegacy = (scriptURL?: string) => {
    if (!scriptURL) return false
    const url = new URL(scriptURL, base)
    return url.origin === base.origin && knownPaths.includes(url.pathname)
  }
  try {
    const container = navigator.serviceWorker
    const registrations = await container.getRegistrations()
    const legacy = registrations.filter((registration) => registration.scope === base.href
      && [registration.active, registration.waiting, registration.installing].some((worker) => isLegacy(worker?.scriptURL)))
    const removed = await Promise.all(legacy.map((registration) => registration.unregister()))
    if (removed.some(Boolean) && isLegacy(container.controller?.scriptURL)) {
      // unregister() does not detach the worker from an already open document.
      reload()
      return true
    }
  } catch (error) {
    console.error('[Voya AI] Could not retire the legacy service worker:', error)
  }
  return false
}
