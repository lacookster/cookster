// Cookster local lists/favourites/shopping/meal-plan module.
// User data is persisted server-side in SQLite and localStorage is kept as a
// fast local cache. Changes are debounced and synced to the server.

(function (root) {
  const STORAGE_KEY = 'cookster_lists_v3'
  const PREV_KEYS = ['cookster_lists_v2', 'cookster_lists']
  const SERVER_DEBOUNCE_MS = 2000

  let saveTimer = null
  let lastServerPush = 0

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

  function emptyData() {
    return {
      favorites: [],
      lists: [],
      shopping: { items: [] },
      mealPlan: {},
      notes: {},
      ratings: {},
      cooked: {},
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
        lists: Array.isArray(parsed.lists) ? parsed.lists : [],
        shopping: parsed.shopping && typeof parsed.shopping === 'object' ? parsed.shopping : { items: [] },
        mealPlan: parsed.mealPlan && typeof parsed.mealPlan === 'object' ? parsed.mealPlan : {},
        notes: parsed.notes && typeof parsed.notes === 'object' ? parsed.notes : {},
        ratings: parsed.ratings && typeof parsed.ratings === 'object' ? parsed.ratings : {},
        cooked: parsed.cooked && typeof parsed.cooked === 'object' ? parsed.cooked : {},
        updatedAt: typeof parsed.updatedAt === 'number' ? parsed.updatedAt : 0
      }
    } catch (e) {
      return null
    }
  }

  function load() {
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
      return parsed || emptyData()
    } catch (e) {
      console.error('[cookster] failed to load lists', e)
      return emptyData()
    }
  }

  function save(data) {
    try {
      data.updatedAt = Date.now()
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
    saveTimer = setTimeout(pushToServer, SERVER_DEBOUNCE_MS)
  }

  function pushToServer() {
    const data = load()
    // Avoid pushing more often than the debounce interval in very active sessions.
    const now = Date.now()
    if (now - lastServerPush < 500) return
    lastServerPush = now
    fetch('/api/user-data', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ data })
    })
      .then(r => r.ok ? r.json() : Promise.reject(new Error('server error ' + r.status)))
      .then(() => {
        window.dispatchEvent(new CustomEvent('cookster-sync-status', { detail: { status: 'saved', when: now } }))
      })
      .catch(err => {
        console.error('[cookster] failed to sync to server', err)
        window.dispatchEvent(new CustomEvent('cookster-sync-status', { detail: { status: 'error', when: now } }))
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
      data.lists.length > 0 ||
      (data.shopping && data.shopping.items && data.shopping.items.length > 0) ||
      Object.keys(data.mealPlan || {}).length > 0 ||
      Object.keys(data.notes || {}).length > 0 ||
      Object.keys(data.ratings || {}).length > 0 ||
      Object.keys(data.cooked || {}).length > 0
  }

  function resolveAndStore(serverPayload) {
    if (!serverPayload) return
    const serverData = serverPayload.data || {}
    const serverTime = serverPayload.updated_at || serverData.updatedAt || 0
    const localData = load()
    const localTime = localData.updatedAt || 0

    if (hasContent(serverData) && !hasContent(localData)) {
      serverData.updatedAt = serverTime
      localStorage.setItem(STORAGE_KEY, JSON.stringify(serverData))
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
        notify()
      } else if (localTime > serverTime) {
        pushToServer()
      }
      return
    }

    // Both empty: nothing to do.
  }

  // Sync once on load so server data is adopted and local changes are uploaded.
  pullFromServer().then(resolveAndStore)

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
        lists: Array.isArray(data.lists) ? data.lists : [],
        shopping: data.shopping && typeof data.shopping === 'object' ? data.shopping : { items: [] },
        mealPlan: data.mealPlan && typeof data.mealPlan === 'object' ? data.mealPlan : {},
        notes: data.notes && typeof data.notes === 'object' ? data.notes : {},
        ratings: data.ratings && typeof data.ratings === 'object' ? data.ratings : {},
        cooked: data.cooked && typeof data.cooked === 'object' ? data.cooked : {},
        updatedAt: Date.now()
      }
      save(merged)
      return { ok: true }
    },

    // Server sync helpers --------------------------------------------------
    syncNow() {
      if (saveTimer) clearTimeout(saveTimer)
      pushToServer()
    },

    pullFromServer,
    pushToServer
  }

  root.CooksterLists = api
})(window)
