/* Cookster service worker — Phase 7 robust offline support */

const CACHE_VERSION = 3
const STATIC_CACHE = `cookster-static-v${CACHE_VERSION}`
const PAGES_CACHE = `cookster-pages-v${CACHE_VERSION}`
const IMAGES_CACHE = `cookster-images-v${CACHE_VERSION}`

const APP_SHELL = [
  '/',
  '/offline',
  '/static/app.js',
  '/static/lists.js',
  '/static/recipe.js',
  '/static/ui.js',
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

const isEpubImage = (url) => {
  const { pathname, origin } = new URL(url)
  if (!STATIC_ORIGINS.includes(origin)) return false
  return pathname.startsWith('/static/epub_images/')
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
    pathname === '/offline' ||
    pathname === '/collections'
  )
}

async function trimImageCache(cache, maxEntries = 200) {
  const keys = await cache.keys()
  if (keys.length <= maxEntries) return
  // Remove oldest requests first (Cache API returns insertion order).
  const toDelete = keys.slice(0, keys.length - maxEntries)
  await Promise.all(toDelete.map((req) => cache.delete(req)))
}

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(STATIC_CACHE)
      .then((cache) => cache.addAll(APP_SHELL))
      .catch((err) => {
        console.error('[cookster-sw] install addAll failed:', err)
      })
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
            .filter((key) => key !== STATIC_CACHE && key !== PAGES_CACHE && key !== IMAGES_CACHE)
            .map((key) => caches.delete(key))
        )
      )
      .then(() => self.clients.claim())
  )
})

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting()
  }
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

  // EPUB images: cache-first with network fallback and LRU eviction.
  if (isEpubImage(url)) {
    event.respondWith(
      caches.open(IMAGES_CACHE).then(async (cache) => {
        const cached = await cache.match(request)
        if (cached) return cached
        try {
          const response = await fetch(request)
          if (response && response.status === 200 && response.type === 'basic') {
            const clone = response.clone()
            await cache.put(request, clone)
            await trimImageCache(cache, 200)
          }
          return response
        } catch (err) {
          // No fallback for missing images; let the browser show its broken image.
          throw err
        }
      })
    )
    return
  }

  // HTML pages: network-first; cache only successful HTML GETs.
  if (isHtmlPage(url) || request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (response && response.ok && response.status === 200) {
            const contentType = response.headers.get('content-type') || ''
            if (contentType.includes('text/html')) {
              const clone = response.clone()
              caches.open(PAGES_CACHE).then((cache) => cache.put(request, clone))
            }
          }
          return response
        })
        .catch(async () => {
          const cached = await caches.match(request)
          if (cached) return cached
          const offline = await caches.match('/offline')
          if (offline) return offline
          throw new Error('Network and cache miss')
        })
    )
    return
  }

  // API calls: network-first, no caching.
  if (isApiCall(url)) {
    event.respondWith(
      fetch(request).catch(async () => {
        const cached = await caches.match(request)
        if (cached) return cached
        throw new Error('Network and cache miss')
      })
    )
    return
  }
})
