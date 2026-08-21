// Cookster local lists/favourites/shopping/meal-plan module.
// User data is persisted server-side in SQLite and localStorage is kept as a
// fast local cache. Changes are debounced and synced to the server.

(function (root) {
  const STORAGE_KEY = 'cookster_lists_v3'
  const PREV_KEYS = ['cookster_lists_v2', 'cookster_lists']
  const SERVER_DEBOUNCE_MS = 2000

  let saveTimer = null
  let lastServerPush = 0
  let pushRetryCount = 0
  let _cached = null

  window.addEventListener('storage', (e) => {
    if (e.key === STORAGE_KEY) {
      _cached = null
    }
  })

  function today() {
    const d = new Date()
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  }

  function uuid() {
    return 'list_' + Math.random().toString(36).slice(2) + Date.now().toString(36)
  }

  function itemUuid() {
    return 'item_' + Math.random().toString(36).slice(2) + Date.now().toString(36)
  }

  function normalizeId(id) {
    return String(id)
  }

  // Shopping deduplication helpers -------------------------------------------
  const UNITS = ['kg', 'g', 'mg', 'ml', 'l', 'litre', 'liter', 'litres', 'liters',
    'cup', 'cups', 'tbsp', 'tablespoon', 'tablespoons', 'tsp', 'teaspoon', 'teaspoons',
    'oz', 'ounce', 'ounces', 'lb', 'lbs', 'pound', 'pounds',
    'bunch', 'bunches', 'clove', 'cloves', 'pinch', 'pinches',
    'slice', 'slices', 'piece', 'pieces', 'leaf', 'leaves', 'sprig', 'sprigs']

  const FRACTIONS = {
    '\u00bc': 0.25, '\u00bd': 0.5, '\u00be': 0.75,
    '\u2153': 1 / 3, '\u2154': 2 / 3, '\u215b': 1 / 8,
    '\u215c': 3 / 8, '\u215d': 5 / 8, '\u215e': 7 / 8
  }

  function parseNumberPrefix(text) {
    // Fractions like ½, ¼, ¾, optionally preceded by a whole number.
    const fracChar = Object.keys(FRACTIONS).find(c => text.includes(c))
    if (fracChar) {
      const idx = text.indexOf(fracChar)
      const before = text.slice(0, idx).trim()
      const after = text.slice(idx + 1).trim()
      const wholeMatch = before.match(/(\d+)\s*$/)
      const whole = wholeMatch ? parseInt(wholeMatch[1], 10) : 0
      const value = whole + FRACTIONS[fracChar]
      const prefixEnd = idx + 1
      return { value, prefixEnd, rest: after }
    }
    // Plain integers, decimals, or vulgar fractions like 1/2.
    const m = text.match(/^\s*(\d+(?:\.\d+)?)\s*(?:\/\s*(\d+))?\s*/)
    if (!m) return null
    let value = parseFloat(m[1])
    if (m[2]) value /= parseInt(m[2], 10)
    return { value, prefixEnd: m[0].length, rest: text.slice(m[0].length) }
  }

  function parseShoppingItem(text) {
    const t = text.trim().toLowerCase()
    const parsed = parseNumberPrefix(t)
    if (!parsed) return null
    let rest = parsed.rest.replace(/^,\s*/, '').trim()
    // Capture optional unit word.
    const unitMatch = rest.match(new RegExp('^(' + UNITS.map(u => u.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') + ')\\b\\s*'))
    let unit = ''
    if (unitMatch) {
      unit = unitMatch[1]
      rest = rest.slice(unitMatch[0].length).replace(/^,\s*/, '').trim()
    }
    // Strip common prep descriptors to get a normalised base name.
    const descriptors = ['fresh', 'freshly', 'ground', 'chopped', 'sliced', 'diced', 'peeled', 'grated', 'crushed', 'minced', 'beaten', 'melted', 'softened', 'large', 'small', 'medium']
    let name = rest.replace(/\s+/g, ' ')
    descriptors.forEach(d => {
      name = name.replace(new RegExp('\\b' + d + '\\b\\s*', 'g'), '')
    })
    name = name.replace(/\s+/g, ' ').trim()
    if (!name) return null
    // Simple singularisation.
    if (name.endsWith('s') && !name.endsWith('ss')) {
      name = name.slice(0, -1)
    }
    return { qty: parsed.value, unit, name, original: text }
  }

  function formatQty(value) {
    const rounded = Math.round(value * 100) / 100
    if (Math.abs(rounded - Math.round(rounded)) < 0.001) return String(Math.round(rounded))
    return String(rounded)
  }

  function normaliseUnit(unit) {
    const map = { 'litres': 'l', 'liters': 'l', 'liter': 'l', 'litre': 'l',
      'tablespoons': 'tbsp', 'tablespoon': 'tbsp',
      'teaspoons': 'tsp', 'teaspoon': 'tsp',
      'ounces': 'oz', 'ounce': 'oz',
      'pounds': 'lb', 'lbs': 'lb',
      'bunches': 'bunch', 'cloves': 'clove', 'pinches': 'pinch',
      'slices': 'slice', 'pieces': 'piece', 'leaves': 'leaf', 'sprigs': 'sprig' }
    return map[unit] || unit
  }

  function emptyData() {
    return {
      favorites: [],
      wantToTry: [],
      lists: [],
      tags: [],
      savedSearches: [],
      pantry: [],
      shopping: { items: [] },
      mealPlan: {},
      notes: {},
      ratings: {},
      cooked: {},
      substitutions: {},
      videoLinks: {},
      recentlyViewed: [],
      updatedAt: 0
    }
  }

  function migrate(raw) {
    if (!raw) return null
    try {
      const parsed = JSON.parse(raw)
      if (!parsed || typeof parsed !== 'object') return null
      return {
        favorites: Array.isArray(parsed.favorites) ? parsed.favorites : [],
        wantToTry: Array.isArray(parsed.wantToTry) ? parsed.wantToTry : [],
        lists: Array.isArray(parsed.lists) ? parsed.lists : [],
        tags: Array.isArray(parsed.tags) ? parsed.tags : [],
        savedSearches: Array.isArray(parsed.savedSearches) ? parsed.savedSearches : [],
        pantry: Array.isArray(parsed.pantry) ? parsed.pantry : [],
        shopping: parsed.shopping && typeof parsed.shopping === 'object' ? parsed.shopping : { items: [] },
        mealPlan: parsed.mealPlan && typeof parsed.mealPlan === 'object' ? parsed.mealPlan : {},
        notes: parsed.notes && typeof parsed.notes === 'object' ? parsed.notes : {},
        ratings: parsed.ratings && typeof parsed.ratings === 'object' ? parsed.ratings : {},
        cooked: parsed.cooked && typeof parsed.cooked === 'object' ? parsed.cooked : {},
        substitutions: parsed.substitutions && typeof parsed.substitutions === 'object' ? parsed.substitutions : {},
        videoLinks: parsed.videoLinks && typeof parsed.videoLinks === 'object' ? parsed.videoLinks : {},
        recentlyViewed: Array.isArray(parsed.recentlyViewed) ? parsed.recentlyViewed : [],
        updatedAt: typeof parsed.updatedAt === 'number' ? parsed.updatedAt : 0
      }
    } catch (e) {
      return null
    }
  }

  function load() {
    if (_cached) return _cached
    try {
      let raw = localStorage.getItem(STORAGE_KEY)
      if (!raw) {
        for (const key of PREV_KEYS) {
          raw = localStorage.getItem(key)
          if (raw) {
            const migrated = migrate(raw)
            if (migrated) {
              save(migrated)
              localStorage.removeItem(key)
              return migrated
            }
          }
        }
      }
      const parsed = migrate(raw || '{}')
      _cached = parsed || emptyData()
      return _cached
    } catch (e) {
      console.error('[cookster] failed to load lists', e)
      _cached = emptyData()
      return _cached
    }
  }

  function save(data) {
    try {
      data.updatedAt = Date.now()
      _cached = data
      localStorage.setItem(STORAGE_KEY, JSON.stringify(data))
      notify()
      schedulePush()
    } catch (e) {
      console.error('[cookster] failed to save lists', e)
    }
  }

  function notify() {
    window.dispatchEvent(new CustomEvent('cookster-lists-changed', { detail: load() }))
  }

  function schedulePush() {
    if (saveTimer) clearTimeout(saveTimer)
    pushRetryCount = 0
    saveTimer = setTimeout(pushToServer, SERVER_DEBOUNCE_MS)
  }

  function adoptServerData(serverData) {
    // Store a server blob locally without bumping updatedAt or re-pushing.
    _cached = null
    localStorage.setItem(STORAGE_KEY, JSON.stringify(serverData))
    notify()
  }

  function pushToServer() {
    const data = load()
    // Avoid pushing more often than the debounce interval in very active sessions.
    const now = Date.now()
    if (now - lastServerPush < 500) {
      setTimeout(pushToServer, 500)
      return
    }
    lastServerPush = now
    const sentUpdatedAt = data.updatedAt
    fetch('/api/user-data', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ data })
    })
      .then(r => {
        if (!r.ok) {
          const err = new Error('server error ' + r.status)
          err.status = r.status
          throw err
        }
        return r.json()
      })
      .then(resp => {
        pushRetryCount = 0
        // The server merged our blob with its stored one. Adopt the merged
        // result, unless the user made more local edits while we were pushing
        // (those will be sent by the next scheduled push).
        if (resp && resp.data && load().updatedAt === sentUpdatedAt) {
          const merged = resp.data
          if (typeof merged.updatedAt !== 'number' || merged.updatedAt < sentUpdatedAt) {
            merged.updatedAt = sentUpdatedAt
          }
          adoptServerData(merged)
        }
        window.dispatchEvent(new CustomEvent('cookster-sync-status', { detail: { status: 'saved', when: now } }))
      })
      .catch(err => {
        console.error('[cookster] failed to sync to server', err)
        window.dispatchEvent(new CustomEvent('cookster-sync-status', { detail: { status: 'error', when: now } }))
        // 403 means this device was revoked: retrying would never succeed.
        if (err.status !== 403 && pushRetryCount < 3) {
          const delay = 1000 * Math.pow(2, pushRetryCount)
          pushRetryCount++
          setTimeout(pushToServer, delay)
        }
      })
  }

  function pullFromServer() {
    return fetch('/api/user-data', { credentials: 'same-origin' })
      .then(r => r.ok ? r.json() : Promise.reject(new Error('server error ' + r.status)))
      .catch(err => {
        console.error('[cookster] failed to load from server', err)
        return null
      })
  }

  function hasContent(data) {
    if (!data) return false
    return data.favorites.length > 0 ||
      data.wantToTry.length > 0 ||
      data.lists.length > 0 ||
      data.tags.length > 0 ||
      data.savedSearches.length > 0 ||
      data.pantry.length > 0 ||
      (data.shopping && data.shopping.items && data.shopping.items.length > 0) ||
      Object.keys(data.mealPlan || {}).length > 0 ||
      Object.keys(data.notes || {}).length > 0 ||
      Object.keys(data.ratings || {}).length > 0 ||
      Object.keys(data.cooked || {}).length > 0 ||
      Object.keys(data.substitutions || {}).length > 0 ||
      Object.keys(data.videoLinks || {}).length > 0 ||
      (data.recentlyViewed && data.recentlyViewed.length > 0)
  }

  function resolveAndStore(serverPayload) {
    if (!serverPayload) return
    const serverData = serverPayload.data || {}
    // data.updatedAt is in ms (client convention); the updated_at column is seconds.
    const serverTime = serverData.updatedAt || (serverPayload.updated_at || 0) * 1000
    const localData = load()
    const localTime = localData.updatedAt || 0

    if (hasContent(serverData) && !hasContent(localData)) {
      serverData.updatedAt = serverTime
      localStorage.setItem(STORAGE_KEY, JSON.stringify(serverData))
      _cached = null
      notify()
      return
    }

    if (!hasContent(serverData) && hasContent(localData)) {
      pushToServer()
      return
    }

    if (hasContent(serverData) && hasContent(localData)) {
      if (serverTime > localTime) {
        serverData.updatedAt = serverTime
        localStorage.setItem(STORAGE_KEY, JSON.stringify(serverData))
        _cached = null
        notify()
      } else if (localTime > serverTime) {
        pushToServer()
      }
      return
    }

    // Both empty: nothing to do.
  }

  // If the URL carries a pairing code (?pair=CODE), claim it to adopt the
  // paired device's token, then reload without the query parameter.
  function handlePairParam() {
    let params
    try {
      params = new URLSearchParams(window.location.search)
    } catch (e) {
      return false
    }
    const code = params.get('pair')
    if (!code) return false
    fetch('/api/pairing-code/claim', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code })
    })
      .then(r => {
        if (!r.ok) {
          const err = new Error('pairing failed ' + r.status)
          err.status = r.status
          throw err
        }
        return r.json()
      })
      .then(payload => {
        if (payload && payload.data) {
          const data = payload.data
          if (typeof data.updatedAt !== 'number') data.updatedAt = Date.now()
          _cached = null
          localStorage.setItem(STORAGE_KEY, JSON.stringify(data))
        }
      })
      .catch(err => console.error('[cookster] pairing failed', err))
      .finally(() => {
        params.delete('pair')
        const qs = params.toString()
        const url = window.location.pathname + (qs ? '?' + qs : '') + window.location.hash
        window.location.replace(url)
      })
    return true
  }

  // Sync once on load so server data is adopted and local changes are uploaded.
  // (Skipped while a pairing claim is in flight; the reload re-triggers it.)
  if (!handlePairParam()) {
    pullFromServer().then(resolveAndStore)
  }

  const api = {
    load,

    toggleFavorite(id) {
      id = normalizeId(id)
      const data = load()
      const idx = data.favorites.indexOf(id)
      if (idx === -1) {
        data.favorites.push(id)
      } else {
        data.favorites.splice(idx, 1)
      }
      save(data)
      return idx === -1
    },

    isFavorite(id) {
      return load().favorites.includes(normalizeId(id))
    },

    getFavorites() {
      return load().favorites
    },

    toggleWantToTry(id) {
      id = normalizeId(id)
      const data = load()
      const idx = data.wantToTry.indexOf(id)
      if (idx === -1) {
        data.wantToTry.push(id)
      } else {
        data.wantToTry.splice(idx, 1)
      }
      save(data)
      return idx === -1
    },

    isWantToTry(id) {
      return load().wantToTry.includes(normalizeId(id))
    },

    getWantToTry() {
      return load().wantToTry
    },

    // Saved searches ---------------------------------------------------------
    saveSearch(name, q, filters, source, sort, have = '') {
      const trimmed = (name || '').trim()
      if (!trimmed) return null
      const data = load()
      const existing = data.savedSearches.findIndex(s => s.q === q && s.filters === filters && s.source === source && s.sort === sort && s.have === have)
      const search = {
        id: uuid(),
        label: trimmed,
        q: q || '',
        filters: filters || '',
        source: source || '',
        sort: sort || 'relevance',
        have: have || '',
        createdAt: Date.now()
      }
      if (existing !== -1) data.savedSearches[existing] = search
      else data.savedSearches.push(search)
      save(data)
      return search
    },

    getSavedSearches() {
      return load().savedSearches
    },

    deleteSavedSearch(id) {
      const data = load()
      data.savedSearches = data.savedSearches.filter(s => s.id !== id)
      save(data)
    },

    // Pantry -----------------------------------------------------------------
    getPantry() {
      return load().pantry
    },

    addPantryItem(text) {
      const trimmed = (text || '').trim().toLowerCase()
      if (!trimmed) return false
      const data = load()
      if (!data.pantry.includes(trimmed)) {
        data.pantry.push(trimmed)
        save(data)
      }
      return true
    },

    removePantryItem(text) {
      const target = (text || '').trim().toLowerCase()
      const data = load()
      data.pantry = data.pantry.filter(item => item !== target)
      save(data)
    },

    createList(name) {
      const trimmed = (name || '').trim()
      if (!trimmed) return null
      const data = load()
      const list = { id: uuid(), name: trimmed, recipes: [] }
      data.lists.push(list)
      save(data)
      return list
    },

    getLists() {
      return load().lists
    },

    getList(id) {
      return load().lists.find(l => l.id === id) || null
    },

    renameList(id, name) {
      const trimmed = (name || '').trim()
      if (!trimmed) return false
      const data = load()
      const list = data.lists.find(l => l.id === id)
      if (!list) return false
      list.name = trimmed
      save(data)
      return true
    },

    deleteList(id) {
      const data = load()
      const before = data.lists.length
      data.lists = data.lists.filter(l => l.id !== id)
      save(data)
      return data.lists.length < before
    },

    addToList(listId, recipeId) {
      recipeId = normalizeId(recipeId)
      const data = load()
      const list = data.lists.find(l => l.id === listId)
      if (!list) return false
      if (!list.recipes.includes(recipeId)) {
        list.recipes.push(recipeId)
        save(data)
      }
      return true
    },

    removeFromList(listId, recipeId) {
      recipeId = normalizeId(recipeId)
      const data = load()
      const list = data.lists.find(l => l.id === listId)
      if (!list) return false
      const before = list.recipes.length
      list.recipes = list.recipes.filter(rid => rid !== recipeId)
      if (list.recipes.length < before) save(data)
      return list.recipes.length < before
    },

    isInList(listId, recipeId) {
      const list = load().lists.find(l => l.id === listId)
      return list ? list.recipes.includes(normalizeId(recipeId)) : false
    },

    listsForRecipe(recipeId) {
      recipeId = normalizeId(recipeId)
      return load().lists.filter(l => l.recipes.includes(recipeId))
    },

    setRecipeListMembership(recipeId, listIds) {
      recipeId = normalizeId(recipeId)
      const ids = new Set(listIds)
      const data = load()
      let changed = false
      data.lists.forEach(list => {
        const inList = list.recipes.includes(recipeId)
        const want = ids.has(list.id)
        if (want && !inList) {
          list.recipes.push(recipeId)
          changed = true
        } else if (!want && inList) {
          list.recipes = list.recipes.filter(rid => rid !== recipeId)
          changed = true
        }
      })
      if (changed) save(data)
      return changed
    },

    // Shopping list --------------------------------------------------------
    getShoppingItems() {
      return load().shopping.items || []
    },

    addShoppingItems(texts, recipeId, recipeSource) {
      const data = load()
      const items = data.shopping.items || []
      const normalized = texts.map(t => t.trim()).filter(Boolean)
      for (const text of normalized) {
        if (items.some(i => i.text.toLowerCase() === text.toLowerCase() && i.recipeId === (recipeId || i.recipeId))) continue
        items.push({
          id: itemUuid(),
          text,
          recipeId: recipeId || null,
          source: recipeSource || null,
          checked: false,
          createdAt: Date.now()
        })
      }
      data.shopping.items = items
      save(data)
    },

    toggleShoppingItem(id) {
      const data = load()
      const item = data.shopping.items.find(i => i.id === id)
      if (!item) return false
      item.checked = !item.checked
      save(data)
      return item.checked
    },

    removeShoppingItem(id) {
      const data = load()
      const before = data.shopping.items.length
      data.shopping.items = data.shopping.items.filter(i => i.id !== id)
      save(data)
      return data.shopping.items.length < before
    },

    clearShopping() {
      const data = load()
      data.shopping.items = []
      save(data)
    },

    clearBought() {
      const data = load()
      data.shopping.items = data.shopping.items.filter(i => !i.checked)
      save(data)
    },

    uncheckAllShopping() {
      const data = load()
      data.shopping.items.forEach(i => { i.checked = false })
      save(data)
    },

    dedupeShopping() {
      const data = load()
      const items = data.shopping.items || []
      const exactSeen = new Set()
      const kept = []
      const groups = new Map()

      items.forEach(item => {
        const key = item.text.toLowerCase().trim()
        if (!exactSeen.has(key)) {
          exactSeen.add(key)
          kept.push(item)
        }
      })

      kept.forEach(item => {
        const parsed = parseShoppingItem(item.text)
        if (!parsed) return
        const unit = normaliseUnit(parsed.unit)
        const groupKey = parsed.name + '|' + unit
        if (!groups.has(groupKey)) {
          groups.set(groupKey, { unit, items: [] })
        }
        groups.get(groupKey).items.push({ item, parsed })
      })

      const mergedItems = []
      groups.forEach(group => {
        if (group.items.length < 2) {
          group.items.forEach(({ item }) => mergedItems.push(item))
          return
        }
        const totalQty = group.items.reduce((sum, { parsed }) => sum + parsed.qty, 0)
        const first = group.items[0].item
        const qtyText = formatQty(totalQty)
        const unitText = group.unit ? ' ' + group.unit : ''
        const name = group.items[0].parsed.name
        // Capitalise the name for a tidy combined line.
        const combinedText = qtyText + unitText + ' ' + name.replace(/\b\w/g, c => c.toUpperCase())
        mergedItems.push({
          ...first,
          text: combinedText
        })
      })

      // Keep ungrouped items as-is.
      const groupedKeys = new Set()
      groups.forEach((_, k) => groupedKeys.add(k))
      const ungrouped = kept.filter(item => {
        const parsed = parseShoppingItem(item.text)
        if (!parsed) return true
        return !groupedKeys.has(parsed.name + '|' + normaliseUnit(parsed.unit))
      })

      data.shopping.items = [...mergedItems, ...ungrouped]
      save(data)
    },

    addMealPlanToShopping(days = 7) {
      const data = load()
      const plan = data.mealPlan || {}
      const dates = []
      const todayObj = new Date()
      for (let i = 0; i < days; i++) {
        const d = new Date(todayObj)
        d.setDate(todayObj.getDate() + i)
        dates.push(d.toISOString().split('T')[0])
      }
      const ids = [...new Set(dates.flatMap(d => plan[d] || []))]
      if (!ids.length) return Promise.resolve(0)

      return fetch('/api/recipes?ids=' + encodeURIComponent(ids.join(',')))
        .then(r => r.ok ? r.json() : Promise.reject(new Error('fetch failed')))
        .then(recipes => {
          let added = 0
          recipes.forEach(r => {
            const ingredients = (r.ingredients || '').split('\n').map(l => l.trim()).filter(Boolean)
            if (ingredients.length) {
              this.addShoppingItems(ingredients, r.stable_id || String(r.id), r.source)
              added += ingredients.length
            }
          })
          return added
        })
    },

    // Meal plan ------------------------------------------------------------
    getMealPlan() {
      return load().mealPlan || {}
    },

    getMeals(date) {
      return (load().mealPlan[date] || []).slice()
    },

    addMeal(date, recipeId) {
      if (!date) return false
      recipeId = normalizeId(recipeId)
      const data = load()
      if (!data.mealPlan[date]) data.mealPlan[date] = []
      if (!data.mealPlan[date].includes(recipeId)) {
        data.mealPlan[date].push(recipeId)
        save(data)
      }
      return true
    },

    removeMeal(date, recipeId) {
      if (!date) return false
      recipeId = normalizeId(recipeId)
      const data = load()
      const before = (data.mealPlan[date] || []).length
      data.mealPlan[date] = (data.mealPlan[date] || []).filter(rid => rid !== recipeId)
      if (!data.mealPlan[date].length) delete data.mealPlan[date]
      save(data)
      return before > (data.mealPlan[date] || []).length
    },

    // Notes, ratings, cooked ---------------------------------------------
    setNote(recipeId, text) {
      recipeId = normalizeId(recipeId)
      const data = load()
      data.notes[recipeId] = String(text || '')
      save(data)
    },

    getNote(recipeId) {
      return load().notes[normalizeId(recipeId)] || ''
    },

    setRating(recipeId, stars) {
      recipeId = normalizeId(recipeId)
      const n = Math.max(0, Math.min(5, parseInt(stars, 10) || 0))
      const data = load()
      if (n <= 0) delete data.ratings[recipeId]
      else data.ratings[recipeId] = n
      save(data)
    },

    getRating(recipeId) {
      return load().ratings[normalizeId(recipeId)] || 0
    },

    markCooked(recipeId) {
      recipeId = normalizeId(recipeId)
      const data = load()
      data.cooked[recipeId] = new Date().toISOString()
      save(data)
    },

    getCookedDate(recipeId) {
      return load().cooked[normalizeId(recipeId)] || null
    },

    setSubstitution(recipeId, text) {
      recipeId = normalizeId(recipeId)
      const data = load()
      if (!text || !String(text).trim()) delete data.substitutions[recipeId]
      else data.substitutions[recipeId] = String(text).trim()
      save(data)
    },

    getSubstitution(recipeId) {
      return load().substitutions[normalizeId(recipeId)] || ''
    },

    setVideoLink(recipeId, url) {
      recipeId = normalizeId(recipeId)
      const data = load()
      if (!url || !String(url).trim()) delete data.videoLinks[recipeId]
      else data.videoLinks[recipeId] = String(url).trim()
      save(data)
    },

    getVideoLink(recipeId) {
      return load().videoLinks[normalizeId(recipeId)] || ''
    },

    // Cooking stats --------------------------------------------------------
    getCookedCount() {
      return Object.keys(load().cooked || {}).length
    },

    getMostCookedIds(limit = 3) {
      const data = load().cooked || {}
      return Object.entries(data)
        .sort((a, b) => new Date(b[1]) - new Date(a[1]))
        .slice(0, limit)
        .map(([id]) => id)
    },

    // Recently viewed ------------------------------------------------------
    addRecentView(recipeId) {
      recipeId = normalizeId(recipeId)
      const data = load()
      const recents = data.recentlyViewed || []
      const filtered = recents.filter(id => id !== recipeId)
      filtered.unshift(recipeId)
      while (filtered.length > 20) filtered.pop()
      data.recentlyViewed = filtered
      save(data)
    },

    getRecentViews() {
      return (load().recentlyViewed || []).slice()
    },

    // Backup / import ------------------------------------------------------
    exportAll() {
      return load()
    },

    importAll(input) {
      let data
      try {
        data = typeof input === 'string' ? JSON.parse(input) : input
      } catch (e) {
        return { ok: false, error: 'Invalid JSON' }
      }
      if (!data || typeof data !== 'object') return { ok: false, error: 'Not an object' }
      const merged = {
        favorites: Array.isArray(data.favorites) ? data.favorites : [],
        wantToTry: Array.isArray(data.wantToTry) ? data.wantToTry : [],
        lists: Array.isArray(data.lists) ? data.lists : [],
        tags: Array.isArray(data.tags) ? data.tags : [],
        savedSearches: Array.isArray(data.savedSearches) ? data.savedSearches : [],
        pantry: Array.isArray(data.pantry) ? data.pantry : [],
        shopping: data.shopping && typeof data.shopping === 'object' ? data.shopping : { items: [] },
        mealPlan: data.mealPlan && typeof data.mealPlan === 'object' ? data.mealPlan : {},
        notes: data.notes && typeof data.notes === 'object' ? data.notes : {},
        ratings: data.ratings && typeof data.ratings === 'object' ? data.ratings : {},
        cooked: data.cooked && typeof data.cooked === 'object' ? data.cooked : {},
        substitutions: data.substitutions && typeof data.substitutions === 'object' ? data.substitutions : {},
        videoLinks: data.videoLinks && typeof data.videoLinks === 'object' ? data.videoLinks : {},
        recentlyViewed: Array.isArray(data.recentlyViewed) ? data.recentlyViewed : [],
        updatedAt: Date.now()
      }
      save(merged)
      return { ok: true }
    },

    // Server sync helpers --------------------------------------------------
    syncNow() {
      if (saveTimer) clearTimeout(saveTimer)
      pushRetryCount = 0
      pushToServer()
    },

    pullFromServer,
    pushToServer
  }

  root.CooksterLists = api
})(window)
