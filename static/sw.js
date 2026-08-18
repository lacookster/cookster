/* Cookster service worker — Phase 6 offline support */

const CACHE_VERSION = 2
const STATIC_CACHE = `cookster-static-v${CACHE_VERSION}`
const PAGES_CACHE = `cookster-pages-v${CACHE_VERSION}`

const APP_SHELL = [
  '/',
  '/offline',
  '/static/app.js',
  '/static/lists.js',
  '/static/recipe.js',
  '/static/style.css',
  '/static/cookster-logo.jpg',
  '/static/manifest.json'
]

const STATIC_ORIGINS = [self.location.origin]
const isStaticAsset = (url) => {
  const { pathname, origin } = new URL(url)
  if (!STATIC_ORIGINS.includes(origin)) return false
  return pathname.startsWith('/static/') || pathname === '/favicon.ico'
}

const isApiCall = (url) => {
  const { pathname } = new URL(url)
  return pathname.startsWith('/api/') || pathname === '/search'
}

const isHtmlPage = (url) => {
  const { pathname } = new URL(url)
  return (
    pathname === '/' ||
    pathname.startsWith('/book') ||
    pathname.startsWith('/books') ||
    pathname.startsWith('/recipe/') ||
    pathname === '/offline'
  )
}

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => cache.addAll(APP_SHELL))
  )
  self.skipWaiting()
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key !== STATIC_CACHE && key !== PAGES_CACHE)
            .map((key) => caches.delete(key))
        )
      )
      .then(() => self.clients.claim())
  )
})

self.addEventListener('fetch', (event) => {
  const { request } = event
  const url = request.url

  // Only handle GET requests.
  if (request.method !== 'GET') return

  // Static assets: cache-first, then network, then nothing.
  if (isStaticAsset(url)) {
    event.respondWith(
      caches.match(request).then((cached) => {
        if (cached) return cached
        return fetch(request).then((response) => {
          if (!response || response.status !== 200 || response.type !== 'basic') {
            return response
          }
          const clone = response.clone()
          caches.open(STATIC_CACHE).then((cache) => cache.put(request, clone))
          return response
        })
      })
    )
    return
  }

  // API calls and HTML pages: network-first, fallback to cache, then offline page.
  if (isApiCall(url) || isHtmlPage(url) || request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (response && response.ok) {
            const clone = response.clone()
            caches.open(PAGES_CACHE).then((cache) => cache.put(request, clone))
          }
          return response
        })
        .catch(async () => {
          const cached = await caches.match(request)
          if (cached) return cached
          if (request.mode === 'navigate' || isHtmlPage(url)) {
            const offline = await caches.match('/offline')
            if (offline) return offline
          }
          // Re-throw so the browser shows its default error for non-HTML requests.
          throw new Error('Network and cache miss')
        })
    )
    return
  }
})
