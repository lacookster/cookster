(() => {
  const Lists = window.CooksterLists
  const qInput = document.getElementById('q')
  const sourceSelect = document.getElementById('source')
  const busy = document.getElementById('busy')
  const resultsEl = document.getElementById('results')
  const countEl = document.getElementById('count')
  const prevBtn = document.getElementById('prev')
  const nextBtn = document.getElementById('next')
  const pageEl = document.getElementById('page')
  const pagesEl = document.getElementById('pages')
  const themeBtn = document.getElementById('theme')
  const listsToggle = document.getElementById('lists-toggle')
  const listsPanel = document.getElementById('lists-panel')
  const listsBackdrop = document.getElementById('lists-backdrop')
  const listsClose = document.getElementById('lists-close')
  const customListsEl = document.getElementById('custom-lists')
  const favCountEl = document.getElementById('fav-count')
  const wantCountEl = document.getElementById('want-count')
  const newListForm = document.getElementById('new-list-form')
  const newListName = document.getElementById('new-list-name')
  const shoppingListEl = document.getElementById('shopping-list')
  const shoppingEmptyEl = document.querySelector('.shopping-empty')
  const clearBoughtBtn = document.getElementById('clear-bought')
  const dedupeShoppingBtn = document.getElementById('dedupe-shopping')
  const uncheckAllShoppingBtn = document.getElementById('uncheck-all-shopping')
  const clearAllShoppingBtn = document.getElementById('clear-all-shopping')
  const addMealPlanShopping3Btn = document.getElementById('add-meal-plan-shopping-3')
  const addMealPlanShopping7Btn = document.getElementById('add-meal-plan-shopping-7')
  const mealPlanEl = document.getElementById('meal-plan')
  const backupExportBtn = document.getElementById('backup-export')
  const backupImportBtn = document.getElementById('backup-import')
  const backupArea = document.getElementById('backup-area')
  const backupStatus = document.getElementById('backup-status')
  const recoveryCodeInput = document.getElementById('recovery-code')
  const copyRecoveryCodeBtn = document.getElementById('copy-recovery-code')
  const importRecoveryInput = document.getElementById('import-recovery-code')
  const importRecoveryBtn = document.getElementById('import-recovery-btn')
  const syncStatusEl = document.getElementById('sync-status')
  const resetServerDataBtn = document.getElementById('reset-server-data')

  const suggestionsEl = document.getElementById('suggestions')
  const recentSearchesEl = document.getElementById('recent-searches')
  const randomBtn = document.getElementById('random')
  const bookCountEl = document.getElementById('book-count')
  const bookCountInlineEl = document.getElementById('book-count-inline')

  const params = new URLSearchParams(location.search)
  let page = Math.max(1, parseInt(params.get('page'), 10) || 1)
  let timer = null
  let suggestionTimer = null
  let lastQuery = (params.get('q') || '').trim()
  let lastSource = (params.get('source') || '').trim()
  let activeFilters = new Set((params.get('filters') || '').split(',').filter(Boolean))
  let totalResults = 0
  let currentView = 'search' // 'search' | 'list'
  let activeListId = null
  let activeSuggestion = -1
  const limit = 50
  const filterChipsEl = document.getElementById('filter-chips')
  const activeFiltersEl = document.getElementById('active-filters')

  if (lastQuery) qInput.value = lastQuery

  function setBusy(v) { busy.style.display = v ? 'block' : 'none' }

  function totalPages() {
    return totalResults > 0 ? Math.max(1, Math.ceil(totalResults / limit)) : 1
  }

  function updatePager() {
    const tp = totalPages()
    pageEl.textContent = page
    if (pagesEl) pagesEl.textContent = `of ${tp}`
    prevBtn.disabled = page <= 1
    nextBtn.disabled = page >= tp || totalResults === 0
    prevBtn.style.display = currentView === 'search' ? '' : 'none'
    nextBtn.style.display = currentView === 'search' ? '' : 'none'
    pageEl.parentElement.style.display = currentView === 'search' ? '' : 'none'
  }

  function updateFilterChips() {
    if (!filterChipsEl) return
    filterChipsEl.querySelectorAll('.filter-chip').forEach(btn => {
      const f = btn.dataset.filter
      const active = activeFilters.has(f)
      btn.classList.toggle('active', active)
      btn.setAttribute('aria-pressed', String(active))
    })
    if (activeFiltersEl) {
      if (activeFilters.size === 0) {
        activeFiltersEl.innerHTML = ''
      } else {
        activeFiltersEl.innerHTML = Array.from(activeFilters).map(f =>
          `<span class="active-filter-tag" data-filter="${escapeHtml(f)}">${escapeHtml(f.replace(/-/g, ' '))} <button aria-label="Remove ${escapeHtml(f.replace(/-/g, ' '))} filter">×</button></span>`
        ).join('')
      }
      activeFiltersEl.querySelectorAll('.active-filter-tag').forEach(tag => {
        tag.querySelector('button').addEventListener('click', () => {
          activeFilters.delete(tag.dataset.filter)
          updateFilterChips()
          page = 1
          doSearch({ pushHistory: true })
        })
      })
    }
  }

  function filtersQuery() {
    return Array.from(activeFilters).join(',')
  }

  const RECENT_SEARCHES_KEY = 'cookster_recent_searches'
  const MAX_RECENT = 10

  function loadRecentSearches() {
    try {
      return JSON.parse(localStorage.getItem(RECENT_SEARCHES_KEY) || '[]')
    } catch (e) {
      return []
    }
  }

  function saveRecentSearch(q, filters) {
    if (!q) return
    const recents = loadRecentSearches()
    const updated = recents.filter(r => r.q !== q)
    updated.unshift({ q, filters: filters || '', source: sourceSelect ? sourceSelect.value : '', timestamp: Date.now() })
    while (updated.length > MAX_RECENT) updated.pop()
    try {
      localStorage.setItem(RECENT_SEARCHES_KEY, JSON.stringify(updated))
    } catch (e) {}
  }

  function renderRecentSearches() {
    if (!recentSearchesEl) return
    const recents = loadRecentSearches()
    if (!recents.length || qInput.value.trim()) {
      closeRecentSearches()
      return
    }
    recentSearchesEl.innerHTML = `
      <div class="recent-searches-header">
        <span>Recent searches</span>
        <button id="clear-recent" class="text-btn">Clear</button>
      </div>
      ${recents.map((r, i) => `
        <div class="recent-search-item" data-index="${i}" role="button" tabindex="0">
          <span class="recent-search-query">${escapeHtml(r.q)}</span>
          ${r.filters ? `<span class="recent-search-filters">${escapeHtml(r.filters.replace(/,/g, ' · '))}</span>` : ''}
        </div>
      `).join('')}
    `
    recentSearchesEl.classList.add('open')
    recentSearchesEl.setAttribute('aria-hidden', 'false')
    recentSearchesEl.querySelectorAll('.recent-search-item').forEach(el => {
      el.addEventListener('click', () => selectRecentSearch(parseInt(el.dataset.index, 10)))
    })
    const clearBtn = recentSearchesEl.querySelector('#clear-recent')
    if (clearBtn) {
      clearBtn.addEventListener('click', (e) => {
        e.stopPropagation()
        localStorage.removeItem(RECENT_SEARCHES_KEY)
        closeRecentSearches()
      })
    }
  }

  function closeRecentSearches() {
    if (!recentSearchesEl) return
    recentSearchesEl.innerHTML = ''
    recentSearchesEl.classList.remove('open')
    recentSearchesEl.setAttribute('aria-hidden', 'true')
  }

  function selectRecentSearch(index) {
    const recents = loadRecentSearches()
    const r = recents[index]
    if (!r) return
    qInput.value = r.q
    activeFilters = new Set((r.filters || '').split(',').filter(Boolean))
    updateFilterChips()
    if (sourceSelect) sourceSelect.value = r.source || ''
    closeRecentSearches()
    doSearch({ pushHistory: true })
  }

  function heartIcon(filled) {
    return filled ? '♥' : '♡'
  }

  function renderRatingStars(rating, size = '1rem') {
    if (!rating) return ''
    const stars = [1, 2, 3, 4, 5].map(i => {
      const active = i <= rating ? 'active' : ''
      return `<span class="rating-star-display ${active}" style="font-size:${size}">★</span>`
    }).join('')
    return `<span class="rating-display" title="${rating} star${rating === 1 ? '' : 's'}">${stars}</span>`
  }

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
  }

  function formatDateLabel(iso) {
    const d = new Date(iso + 'T00:00:00')
    const today = new Date()
    const isToday = d.toDateString() === today.toDateString()
    const tomorrow = new Date(today); tomorrow.setDate(today.getDate() + 1)
    const isTomorrow = d.toDateString() === tomorrow.toDateString()
    const weekday = d.toLocaleDateString(undefined, { weekday: 'short' })
    const date = d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
    if (isToday) return `Today · ${weekday} ${date}`
    if (isTomorrow) return `Tomorrow · ${weekday} ${date}`
    return `${weekday} ${date}`
  }

  function parseIngredients(text) {
    if (!text) return []
    return text.split('\n').map(l => l.trim()).filter(Boolean)
  }

  function renderNewBookCard(book) {
    const imageHtml = book.image_url
      ? `<div class="card-media"><img src="${book.image_url}" alt="" loading="lazy"></div>`
      : `<div class="card-media"><div class="placeholder">📚</div></div>`
    return `
      <a class="card book-card" href="/book?source=${encodeURIComponent(book.source)}">
        ${imageHtml}
        <div class="card-body">
          <div class="card-title-row">
            <h3>${escapeHtml(book.title)}</h3>
          </div>
          <div class="card-meta">
            <span>${book.count} recipe${book.count === 1 ? '' : 's'}</span>
          </div>
        </div>
      </a>
    `
  }

  async function loadNewBooks() {
    currentView = 'search'
    activeListId = null
    page = 1
    totalResults = 0
    lastQuery = ''
    lastSource = ''
    updatePager()
    syncUrl(false)
    countEl.textContent = ''
    setBusy(true)
    try {
      const res = await fetch('/api/new-books?limit=5')
      if (!res.ok) throw new Error('Failed to load new books')
      const data = await res.json()
      const books = data.books || []
      if (books.length === 0) {
        resultsEl.innerHTML = `
          <div class="empty empty-home">
            <h2>Start typing to search</h2>
            <p>Try an ingredient like <strong>chicken</strong>, <strong>chocolate</strong>, or <strong>tofu</strong>. Or use the filters above for dietary shortcuts.</p>
            <div class="empty-actions">
              <button class="empty-example" data-q="chicken">🍗 Chicken</button>
              <button class="empty-example" data-q="chocolate cake">🍰 Chocolate cake</button>
              <button class="empty-example" data-q="quick">⏱ Quick</button>
            </div>
          </div>`
        resultsEl.querySelectorAll('.empty-example').forEach(btn => {
          btn.addEventListener('click', () => {
            qInput.value = btn.dataset.q
            page = 1
            doSearch({ pushHistory: true })
          })
        })
        return
      }
      resultsEl.innerHTML = `
        <div class="new-books-home">
          <h2>📚 New cookbooks</h2>
          <div class="books-grid new-books-grid">${books.map(renderNewBookCard).join('')}</div>
        </div>`
    } catch (err) {
      console.error('[cookster] new books error:', err)
      resultsEl.innerHTML = `
        <div class="empty empty-home">
          <h2>Start typing to search</h2>
          <p>Try an ingredient like <strong>chicken</strong>, <strong>chocolate</strong>, or <strong>tofu</strong>.</p>
          <div class="empty-actions">
            <button class="empty-example" data-q="chicken">🍗 Chicken</button>
            <button class="empty-example" data-q="pasta">🍝 Pasta</button>
            <button class="empty-example" data-q="dessert">🍰 Dessert</button>
          </div>
        </div>`
      resultsEl.querySelectorAll('.empty-example').forEach(btn => {
        btn.addEventListener('click', () => {
          qInput.value = btn.dataset.q
          page = 1
          doSearch({ pushHistory: true })
        })
      })
    } finally {
      setBusy(false)
    }
  }

  function renderCard(r) {
    const sid = r.stable_id || String(r.id)
    const isFav = Lists.isFavorite(sid)
    const isWantToTry = Lists.isWantToTry(sid)
    const inLists = Lists.listsForRecipe(sid)
    const imageHtml = r.image_url
      ? `<div class="card-media"><img src="${r.image_url}" alt="" loading="lazy"></div>`
      : `<div class="card-media"><div class="placeholder">📷 No image</div></div>`

    const listChips = inLists.length
      ? `<div class="card-lists">${inLists.map(l => `<span class="list-chip">${escapeHtml(l.name)}</span>`).join('')}</div>`
      : ''

    return `
      <article class="card" data-recipe-id="${sid}">
        ${imageHtml}
        <div class="card-body">
          <div class="card-title-row">
            <h3><a href="/recipe/${sid}">${r.title}</a></h3>
            <div class="card-title-actions">
              <button class="want-btn ${isWantToTry ? 'active' : ''}" data-id="${sid}" title="${isWantToTry ? 'Remove from Want to try' : 'Add to Want to try'}" aria-label="${isWantToTry ? 'Remove from Want to try' : 'Add to Want to try'}">
                ${isWantToTry ? '🍽' : '🍽'}
              </button>
              <button class="fav-btn ${isFav ? 'active' : ''}" data-id="${sid}" aria-label="${isFav ? 'Remove from favourites' : 'Add to favourites'}">
                ${heartIcon(isFav)}
              </button>
            </div>
          </div>
          <div class="card-meta">
            <a href="/book?source=${encodeURIComponent(r.source_raw || r.source)}">${r.source}</a>
            ${r.serves ? `<span class="card-serves">🍽 ${escapeHtml(r.serves)}</span>` : ''}
            <span class="score">${(r.score || 0).toFixed(2)}</span>
          </div>
          ${renderRatingStars(Lists.getRating(sid), '0.95rem')}
          ${listChips}
          <div class="card-snippet">
            ${r.ingredients_snippet ? `<div><strong>Ingredients:</strong> ${r.ingredients_snippet}</div>` : ''}
            ${r.steps_snippet ? `<div style="margin-top:6px"><strong>Method:</strong> ${r.steps_snippet}</div>` : ''}
          </div>
          <div class="card-actions-row">
            <button class="card-action-btn add-shopping" data-id="${sid}" title="Add ingredients to shopping list">🛒 Shopping</button>
            <div class="card-plan">
              <input type="date" class="card-plan-date" data-id="${sid}" aria-label="Plan meal date">
              <button class="card-action-btn plan-meal" data-id="${sid}" title="Add to meal plan">📅 Plan</button>
            </div>
          </div>
        </div>
      </article>
    `
  }

  function closeSuggestions() {
    if (!suggestionsEl) return
    suggestionsEl.innerHTML = ''
    suggestionsEl.classList.remove('open')
    suggestionsEl.setAttribute('aria-hidden', 'true')
    activeSuggestion = -1
  }

  function highlightMatch(title, q) {
    const safe = escapeHtml(title)
    const low = q.toLowerCase()
    const idx = safe.toLowerCase().indexOf(low)
    if (idx === -1) return safe
    return safe.slice(0, idx) + '<mark>' + safe.slice(idx, idx + q.length) + '</mark>' + safe.slice(idx + q.length)
  }

  function renderSuggestions(items, q) {
    if (!suggestionsEl) return
    if (!items.length) {
      closeSuggestions()
      return
    }
    suggestionsEl.innerHTML = items.map((t, i) => `
      <div class="suggestion-item" data-index="${i}" data-title="${escapeHtml(t)}">${highlightMatch(t, q)}</div>
    `).join('')
    suggestionsEl.classList.add('open')
    suggestionsEl.setAttribute('aria-hidden', 'false')
    activeSuggestion = -1
    suggestionsEl.querySelectorAll('.suggestion-item').forEach(el => {
      el.addEventListener('click', () => {
        qInput.value = el.dataset.title
        closeSuggestions()
        doSearch({ pushHistory: true })
      })
    })
  }

  async function fetchSuggestions() {
    const q = qInput.value.trim()
    if (!q) {
      closeSuggestions()
      return
    }
    try {
      const res = await fetch(`/api/suggest?q=${encodeURIComponent(q)}`)
      if (!res.ok) throw new Error('suggest failed')
      const data = await res.json()
      renderSuggestions(data.suggestions || [], q)
    } catch (err) {
      console.error('[cookster] suggest error:', err)
    }
  }

  function updateActiveSuggestion() {
    const items = suggestionsEl.querySelectorAll('.suggestion-item')
    items.forEach((el, i) => el.classList.toggle('active', i === activeSuggestion))
    if (activeSuggestion >= 0 && items[activeSuggestion]) {
      items[activeSuggestion].scrollIntoView({ block: 'nearest' })
    }
  }

  function selectSuggestion() {
    const items = suggestionsEl.querySelectorAll('.suggestion-item')
    if (activeSuggestion >= 0 && items[activeSuggestion]) {
      qInput.value = items[activeSuggestion].dataset.title
      closeSuggestions()
      doSearch({ pushHistory: true })
    }
  }

  function syncUrl(push = false) {
    const url = new URL(location.href)
    const q = qInput.value.trim()
    const source = sourceSelect ? sourceSelect.value : ''
    const filters = filtersQuery()
    if (q && currentView === 'search') {
      url.searchParams.set('q', q)
      url.searchParams.set('page', String(page))
      if (source) url.searchParams.set('source', source)
      else url.searchParams.delete('source')
      if (filters) url.searchParams.set('filters', filters)
      else url.searchParams.delete('filters')
    } else {
      url.searchParams.delete('q')
      url.searchParams.delete('page')
      url.searchParams.delete('source')
      url.searchParams.delete('filters')
    }
    if (currentView === 'list' && activeListId) {
      url.searchParams.set('view', 'list')
      url.searchParams.set('list', activeListId)
    } else {
      url.searchParams.delete('view')
      url.searchParams.delete('list')
    }
    if (push) {
      history.pushState({ q, page, source, filters, currentView, activeListId }, '', url.toString())
    } else {
      history.replaceState({ q, page, source, filters, currentView, activeListId }, '', url.toString())
    }
  }

  async function doSearch(options = {}) {
    const { pushHistory = false, scroll = true } = options
    const q = qInput.value.trim()
    const source = sourceSelect ? sourceSelect.value : ''

    if (!q) {
      loadNewBooks()
      return
    }

    currentView = 'search'
    activeListId = null
    if (q !== lastQuery || source !== lastSource) {
      page = 1
      lastQuery = q
      lastSource = source
    }

    setBusy(true)
    try {
      const sourceParam = source ? `&source=${encodeURIComponent(source)}` : ''
      const filterParam = activeFilters.size ? `&filters=${encodeURIComponent(filtersQuery())}` : ''
      const res = await fetch(`/search?q=${encodeURIComponent(q)}&page=${page}&limit=${limit}${sourceParam}${filterParam}`)
      if (!res.ok) throw new Error(`Search failed (${res.status})`)
      const data = await res.json()
      totalResults = data.total || 0
      countEl.textContent = totalResults ? `${totalResults} result${totalResults === 1 ? '' : 's'}` : ''
      updatePager()

      resultsEl.innerHTML = ''
      if (!data.results || data.results.length === 0) {
        const activeFilterText = activeFilters.size ? ` with filters ${Array.from(activeFilters).map(f => f.replace(/-/g, ' ')).join(', ')}` : ''
        resultsEl.innerHTML = `
          <div class="empty empty-search">
            <h2>No recipes found for “${escapeHtml(q)}”${escapeHtml(activeFilterText)}</h2>
            <p>Try one of these popular searches, or remove some filters.</p>
            <div class="empty-actions">
              <button class="empty-example" data-q="chocolate">🍫 Chocolate</button>
              <button class="empty-example" data-q="chicken">🍗 Chicken</button>
              <button class="empty-example" data-q="vegetarian">🥬 Vegetarian</button>
              <button class="empty-example" data-q="quick dinner">⏱ Quick dinner</button>
            </div>
            <button id="empty-random" class="btn secondary">🎲 Surprise me</button>
          </div>`
        resultsEl.querySelectorAll('.empty-example').forEach(btn => {
          btn.addEventListener('click', () => {
            qInput.value = btn.dataset.q
            page = 1
            doSearch({ pushHistory: true })
          })
        })
        const emptyRandom = resultsEl.querySelector('#empty-random')
        if (emptyRandom && randomBtn) {
          emptyRandom.addEventListener('click', () => randomBtn.click())
        }
        bindCardActions()
        syncUrl(pushHistory)
        return
      }

      resultsEl.innerHTML = data.results.map(renderCard).join('')
      bindCardActions()
      syncUrl(pushHistory)
      saveRecentSearch(q, filtersQuery())
      if (scroll) window.scrollTo({ top: 0, behavior: 'smooth' })
    } catch (err) {
      console.error('[cookster] search error:', err)
      resultsEl.innerHTML = `
        <div class="empty">
          <h2>Something went wrong</h2>
          <p>${err.message}</p>
        </div>`
      updatePager()
    } finally {
      setBusy(false)
    }
  }

  async function showList(listId, { pushHistory = false, scroll = true } = {}) {
    activeListId = listId
    currentView = 'list'
    let ids = []
    let title = ''
    if (listId === '__favorites__') {
      ids = Lists.getFavorites()
      title = 'Favourites'
    } else if (listId === '__want_to_try__') {
      ids = Lists.getWantToTry()
      title = 'Want to try'
    } else {
      const list = Lists.getList(listId)
      if (!list) return
      ids = list.recipes
      title = list.name
    }

    page = 1
    totalResults = ids.length
    updatePager()
    countEl.textContent = ids.length ? `${ids.length} recipe${ids.length === 1 ? '' : 's'}` : ''

    resultsEl.innerHTML = ''
    if (ids.length === 0) {
      resultsEl.innerHTML = `
        <div class="empty empty-list">
          <h2>${escapeHtml(title)} is empty</h2>
          <p>Tap the heart or “Want to try” button on any recipe to add it here.</p>
          <button id="empty-browse" class="btn secondary">🔎 Browse recipes</button>
        </div>`
      const emptyBrowse = resultsEl.querySelector('#empty-browse')
      if (emptyBrowse) {
        emptyBrowse.addEventListener('click', () => {
          currentView = 'search'
          activeListId = null
          qInput.value = ''
          lastQuery = ''
          page = 1
          doSearch({ pushHistory: true })
        })
      }
      syncUrl(pushHistory)
      if (scroll) window.scrollTo({ top: 0, behavior: 'smooth' })
      return
    }

    setBusy(true)
    try {
      const res = await fetch(`/api/recipes?ids=${ids.join(',')}`)
      if (!res.ok) throw new Error('Failed to load list')
      const data = await res.json()
      const byId = new Map(data.map(r => [String(r.stable_id || r.id), r]))
      const ordered = ids.map(id => byId.get(id)).filter(Boolean)

      resultsEl.innerHTML = `
        <div class="list-view-header">
          <button id="back-to-search" class="btn secondary">← Back to search</button>
          <h2>${escapeHtml(title)}</h2>
        </div>
        <div class="list-view-results">${ordered.map(renderCard).join('')}</div>
      `
      bindCardActions()
      document.getElementById('back-to-search').addEventListener('click', () => {
        currentView = 'search'
        activeListId = null
        doSearch({ pushHistory: true })
      })
      syncUrl(pushHistory)
      if (scroll) window.scrollTo({ top: 0, behavior: 'smooth' })
    } catch (err) {
      console.error('[cookster] list error:', err)
      resultsEl.innerHTML = `<div class="empty"><h2>Error loading list</h2><p>${err.message}</p></div>`
    } finally {
      setBusy(false)
    }
    closeListsPanel()
  }

  function bindFavButtons() {
    resultsEl.querySelectorAll('.fav-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.preventDefault()
        e.stopPropagation()
        const id = btn.dataset.id
        const nowFav = Lists.toggleFavorite(id)
        btn.classList.toggle('active', nowFav)
        btn.setAttribute('aria-label', nowFav ? 'Remove from favourites' : 'Add to favourites')
        btn.textContent = heartIcon(nowFav)
      })
    })
    resultsEl.querySelectorAll('.want-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.preventDefault()
        e.stopPropagation()
        const id = btn.dataset.id
        const nowWant = Lists.toggleWantToTry(id)
        btn.classList.toggle('active', nowWant)
        btn.setAttribute('aria-label', nowWant ? 'Remove from Want to try' : 'Add to Want to try')
        btn.setAttribute('title', nowWant ? 'Remove from Want to try' : 'Add to Want to try')
        showToast(nowWant ? 'Added to Want to try' : 'Removed from Want to try')
      })
    })
  }

  async function bindCardActions() {
    bindFavButtons()
    resultsEl.querySelectorAll('.add-shopping').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.preventDefault()
        e.stopPropagation()
        const id = btn.dataset.id
        try {
          const res = await fetch(`/api/recipes?ids=${id}`)
          if (!res.ok) throw new Error('fetch failed')
          const data = await res.json()
          const r = data[0]
          if (!r) return
          const ingredients = parseIngredients(r.ingredients)
          if (ingredients.length) {
            Lists.addShoppingItems(ingredients, r.stable_id || String(r.id), r.source)
            showToast(`Added ${ingredients.length} ingredient${ingredients.length === 1 ? '' : 's'} to shopping list`)
          } else {
            showToast('No ingredients found for this recipe')
          }
        } catch (err) {
          console.error('[cookster] shopping error:', err)
          showToast('Could not add ingredients')
        }
      })
    })

    resultsEl.querySelectorAll('.plan-meal').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.preventDefault()
        e.stopPropagation()
        const id = btn.dataset.id
        const input = btn.parentElement.querySelector('.card-plan-date')
        const date = input.value
        if (!date) {
          showToast('Pick a date first')
          return
        }
        Lists.addMeal(date, id)
        showToast('Added to meal plan')
        input.value = ''
      })
    })
  }

  function showToast(message) {
    let toast = document.getElementById('cookster-toast')
    if (!toast) {
      toast = document.createElement('div')
      toast.id = 'cookster-toast'
      toast.className = 'cookster-toast'
      document.body.appendChild(toast)
    }
    toast.textContent = message
    toast.classList.add('show')
    setTimeout(() => toast.classList.remove('show'), 2200)
  }

  async function loadSources() {
    if (!sourceSelect) return
    try {
      const res = await fetch('/api/sources')
      if (!res.ok) throw new Error('Failed to load sources')
      const data = await res.json()
      const currentRaw = sourceSelect ? sourceSelect.value : ''
      sourceSelect.innerHTML = '<option value="">All books</option>' +
        data.sources.map(s => `<option value="${escapeHtml(s.raw)}">${escapeHtml(s.clean)}</option>`).join('')
      sourceSelect.value = currentRaw || (params.get('source') || '')
    } catch (err) {
      console.error('[cookster] sources error:', err)
    }
  }

  async function loadStats() {
    if (!bookCountEl && !bookCountInlineEl) return
    try {
      const res = await fetch('/api/stats')
      if (!res.ok) throw new Error('Failed to load stats')
      const data = await res.json()
      const count = data.total_books || 0
      if (bookCountEl) bookCountEl.textContent = count
      if (bookCountInlineEl) bookCountInlineEl.textContent = count
    } catch (err) {
      console.error('[cookster] stats error:', err)
    }
  }

  // Lists panel tabs --------------------------------------------------------
  function switchTab(tabName) {
    if (!listsPanel) return
    listsPanel.querySelectorAll('.lists-tab').forEach(tab => {
      const active = tab.dataset.tab === tabName
      tab.classList.toggle('active', active)
      tab.setAttribute('aria-selected', active ? 'true' : 'false')
    })
    listsPanel.querySelectorAll('.tab-panel').forEach(panel => {
      panel.classList.toggle('active', panel.dataset.tab === tabName)
    })
  }

  listsPanel.querySelectorAll('.lists-tab').forEach(tab => {
    tab.addEventListener('click', () => switchTab(tab.dataset.tab))
  })

  function openListsPanel() {
    listsPanel.classList.add('open')
    listsBackdrop.classList.add('open')
    listsPanel.setAttribute('aria-hidden', 'false')
    renderListsPanel()
  }

  function closeListsPanel() {
    listsPanel.classList.remove('open')
    listsBackdrop.classList.remove('open')
    listsPanel.setAttribute('aria-hidden', 'true')
  }

  function renderListsPanel() {
    const data = Lists.load()
    if (favCountEl) favCountEl.textContent = data.favorites.length
    if (wantCountEl) wantCountEl.textContent = data.wantToTry.length
    customListsEl.innerHTML = data.lists.map(list => `
      <div class="list-row" data-list-id="${list.id}">
        <div class="list-row-main">
          <span class="list-name" title="${escapeHtml(list.name)}">${escapeHtml(list.name)}</span>
          <span class="list-count">${list.recipes.length}</span>
        </div>
        <div class="list-row-actions">
          <button class="icon-btn rename-list" title="Rename">✎</button>
          <button class="icon-btn delete-list" title="Delete">🗑</button>
        </div>
      </div>
    `).join('') || '<p class="empty-lists">No custom lists yet.</p>'

    customListsEl.querySelectorAll('.list-row').forEach(row => {
      const id = row.dataset.listId
      row.querySelector('.list-row-main').addEventListener('click', () => showList(id, { pushHistory: true }))
      row.querySelector('.rename-list').addEventListener('click', (e) => {
        e.stopPropagation()
        const nameSpan = row.querySelector('.list-name')
        const current = nameSpan.textContent
        const input = document.createElement('input')
        input.type = 'text'
        input.value = current
        input.className = 'rename-input'
        nameSpan.replaceWith(input)
        input.focus()
        function finish() {
          if (input.dataset.done === 'true') return
          input.dataset.done = 'true'
          Lists.renameList(id, input.value)
        }
        input.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') finish() })
        input.addEventListener('blur', finish)
      })
      row.querySelector('.delete-list').addEventListener('click', (e) => {
        e.stopPropagation()
        if (confirm('Delete this list?')) Lists.deleteList(id)
      })
    })

    renderShoppingList()
    renderMealPlan()
  }

  function renderShoppingList() {
    const items = Lists.getShoppingItems()
    if (!shoppingListEl) return
    if (!items.length) {
      shoppingListEl.innerHTML = ''
      shoppingEmptyEl.style.display = ''
      clearBoughtBtn.style.display = 'none'
      return
    }
    shoppingEmptyEl.style.display = 'none'
    clearBoughtBtn.style.display = ''
    shoppingListEl.innerHTML = items.map(item => `
      <label class="shopping-item ${item.checked ? 'checked' : ''}">
        <input type="checkbox" data-id="${item.id}" ${item.checked ? 'checked' : ''}>
        <span class="shopping-text">${escapeHtml(item.text)}${item.source ? ` <span class="shopping-source">(${escapeHtml(item.source)})</span>` : ''}</span>
        <button class="shopping-delete" data-id="${item.id}" aria-label="Remove">✕</button>
      </label>
    `).join('')

    shoppingListEl.querySelectorAll('input[type="checkbox"]').forEach(cb => {
      cb.addEventListener('change', () => Lists.toggleShoppingItem(cb.dataset.id))
    })
    shoppingListEl.querySelectorAll('.shopping-delete').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.preventDefault()
        Lists.removeShoppingItem(btn.dataset.id)
      })
    })
  }

  async function renderMealPlan() {
    if (!mealPlanEl) return
    const plan = Lists.getMealPlan()
    const dates = []
    const today = new Date()
    for (let i = 0; i < 7; i++) {
      const d = new Date(today); d.setDate(today.getDate() + i)
      dates.push(d.toISOString().split('T')[0])
    }

    const allIds = [...new Set(Object.values(plan).flat())]
    let titles = new Map()
    if (allIds.length) {
      try {
        const res = await fetch(`/api/recipes?ids=${allIds.join(',')}`)
        if (res.ok) {
          const data = await res.json()
          titles = new Map(data.map(r => [r.stable_id || String(r.id), r.title]))
        }
      } catch (err) {
        console.error('[cookster] meal plan fetch error:', err)
      }
    }

    mealPlanEl.innerHTML = `
      <div class="weekly-calendar">
        ${dates.map(date => {
          const ids = plan[date] || []
          const items = ids.map(id => `
            <div class="meal-item">
              <a class="meal-title" href="/recipe/${id}">${escapeHtml(titles.get(id) || 'Recipe')}</a>
              <button class="meal-remove" data-date="${date}" data-id="${id}" aria-label="Remove">✕</button>
            </div>
          `).join('')
          return `
            <div class="day-card">
              <div class="day-card-header">
                <span class="day-name">${formatDateLabel(date)}</span>
              </div>
              <div class="day-card-body">
                ${items || '<span class="meal-empty">No meals planned</span>'}
              </div>
            </div>
          `
        }).join('')}
      </div>
    `

    mealPlanEl.querySelectorAll('.meal-remove').forEach(btn => {
      btn.addEventListener('click', () => Lists.removeMeal(btn.dataset.date, btn.dataset.id))
    })
  }

  // Backup / server sync UI -----------------------------------------------
  function setSyncStatus(text, type = 'info') {
    if (!syncStatusEl) return
    syncStatusEl.textContent = text
    syncStatusEl.className = 'sync-status ' + type
  }

  function loadRecoveryCode() {
    fetch('/api/user-data/export', { credentials: 'same-origin' })
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(payload => {
        if (recoveryCodeInput) recoveryCodeInput.value = payload.token || ''
        setSyncStatus('Saved to server.')
      })
      .catch(() => {
        setSyncStatus('Unable to reach server.', 'error')
      })
  }

  window.addEventListener('cookster-sync-status', (e) => {
    if (e.detail && e.detail.status === 'saved') {
      setSyncStatus('Saved to server.')
    } else if (e.detail && e.detail.status === 'error') {
      setSyncStatus('Sync failed.', 'error')
    }
  })

  if (backupExportBtn) {
    backupExportBtn.addEventListener('click', () => {
      const data = Lists.exportAll()
      backupArea.value = JSON.stringify(data, null, 2)
      backupArea.select()
      navigator.clipboard.writeText(backupArea.value).catch(() => {})
      backupStatus.textContent = 'Exported to clipboard/textarea.'
      backupStatus.className = 'backup-status success'
    })
  }

  if (backupImportBtn) {
    backupImportBtn.addEventListener('click', () => {
      const result = Lists.importAll(backupArea.value)
      if (result.ok) {
        backupStatus.textContent = 'Backup restored.'
        backupStatus.className = 'backup-status success'
      } else {
        backupStatus.textContent = 'Error: ' + result.error
        backupStatus.className = 'backup-status error'
      }
    })
  }

  if (copyRecoveryCodeBtn) {
    copyRecoveryCodeBtn.addEventListener('click', () => {
      recoveryCodeInput.select()
      navigator.clipboard.writeText(recoveryCodeInput.value).catch(() => {})
      backupStatus.textContent = 'Recovery code copied.'
      backupStatus.className = 'backup-status success'
    })
  }

  if (importRecoveryBtn) {
    importRecoveryBtn.addEventListener('click', () => {
      const token = (importRecoveryInput.value || '').trim()
      if (!token) {
        backupStatus.textContent = 'Please paste a recovery code.'
        backupStatus.className = 'backup-status error'
        return
      }
      backupStatus.textContent = 'Importing…'
      backupStatus.className = 'backup-status'
      fetch('/api/user-data/import', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token })
      })
        .then(r => r.ok ? r.json() : r.json().then(err => Promise.reject(err)))
        .then(() => {
          backupStatus.textContent = 'Device linked. Reloading…'
          backupStatus.className = 'backup-status success'
          setTimeout(() => location.reload(), 800)
        })
        .catch(err => {
          backupStatus.textContent = 'Import failed: ' + (err.detail || err.error || 'unknown')
          backupStatus.className = 'backup-status error'
        })
    })
  }

  if (resetServerDataBtn) {
    resetServerDataBtn.addEventListener('click', () => {
      if (!confirm('Permanently delete all your server-side favourites, lists, shopping, meal plan, notes and ratings? This cannot be undone.')) return
      fetch('/api/user-data/reset', {
        method: 'POST',
        credentials: 'same-origin'
      })
        .then(r => r.ok ? r.json() : Promise.reject())
        .then(() => {
          backupStatus.textContent = 'Server data deleted. Reloading…'
          backupStatus.className = 'backup-status success'
          setTimeout(() => location.reload(), 800)
        })
        .catch(() => {
          backupStatus.textContent = 'Reset failed.'
          backupStatus.className = 'backup-status error'
        })
    })
  }

  loadRecoveryCode()

  // Bind static favourites and want-to-try rows once.
  document.querySelector('.favorite-row')?.addEventListener('click', () => {
    showList('__favorites__', { pushHistory: true })
  })
  document.querySelector('.want-row')?.addEventListener('click', () => {
    showList('__want_to_try__', { pushHistory: true })
  })

  if (clearBoughtBtn) {
    clearBoughtBtn.addEventListener('click', () => Lists.clearBought())
  }
  if (dedupeShoppingBtn) {
    dedupeShoppingBtn.addEventListener('click', () => {
      Lists.dedupeShopping()
      showToast('Duplicate items merged where possible')
    })
  }
  if (uncheckAllShoppingBtn) {
    uncheckAllShoppingBtn.addEventListener('click', () => Lists.uncheckAllShopping())
  }
  if (clearAllShoppingBtn) {
    clearAllShoppingBtn.addEventListener('click', () => {
      if (confirm('Clear the entire shopping list?')) Lists.clearShopping()
    })
  }
  if (addMealPlanShopping3Btn) {
    addMealPlanShopping3Btn.addEventListener('click', async () => {
      setBusy(true)
      try {
        const added = await Lists.addMealPlanToShopping(3)
        showToast(`Added ${added} ingredients from meal plan`)
      } catch (err) {
        showToast('Could not add meal plan ingredients')
      } finally {
        setBusy(false)
      }
    })
  }
  if (addMealPlanShopping7Btn) {
    addMealPlanShopping7Btn.addEventListener('click', async () => {
      setBusy(true)
      try {
        const added = await Lists.addMealPlanToShopping(7)
        showToast(`Added ${added} ingredients from meal plan`)
      } catch (err) {
        showToast('Could not add meal plan ingredients')
      } finally {
        setBusy(false)
      }
    })
  }

  document.querySelectorAll('.mobile-nav-item').forEach(item => {
    item.addEventListener('click', (e) => {
      const action = item.dataset.action
      if (!action) return
      if (action === 'shopping' || action === 'mealplan' || action === 'lists') {
        e.preventDefault()
        openListsPanel()
        switchTab(action === 'shopping' ? 'shopping' : action === 'mealplan' ? 'mealplan' : 'lists')
      }
      // search and books are normal links.
    })
  })

  function debounceSearch() {
    clearTimeout(timer)
    timer = setTimeout(() => doSearch({ pushHistory: false, scroll: false }), 250)
  }

  function debounceSuggestions() {
    clearTimeout(suggestionTimer)
    suggestionTimer = setTimeout(fetchSuggestions, 150)
  }

  // Search event listeners
  qInput.addEventListener('input', () => {
    debounceSearch()
    debounceSuggestions()
    if (qInput.value.trim()) closeRecentSearches()
    else renderRecentSearches()
  })
  qInput.addEventListener('focus', () => {
    closeSuggestions()
    if (!qInput.value.trim()) renderRecentSearches()
  })
  qInput.addEventListener('blur', () => {
    // Delay so clicks on recent items register first.
    setTimeout(() => closeRecentSearches(), 180)
  })
  qInput.addEventListener('keydown', (e) => {
    if (suggestionsEl && suggestionsEl.classList.contains('open')) {
      const items = suggestionsEl.querySelectorAll('.suggestion-item')
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        activeSuggestion = Math.min(activeSuggestion + 1, items.length - 1)
        updateActiveSuggestion()
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        activeSuggestion = Math.max(activeSuggestion - 1, -1)
        updateActiveSuggestion()
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        closeSuggestions()
        return
      }
      if (e.key === 'Enter' && activeSuggestion >= 0) {
        e.preventDefault()
        selectSuggestion()
        return
      }
    }
    if (e.key === 'Enter') {
      closeSuggestions()
      doSearch({ pushHistory: true })
    }
  })
  document.getElementById('go').addEventListener('click', () => { closeSuggestions(); doSearch({ pushHistory: true }) })
  if (randomBtn) {
    randomBtn.addEventListener('click', async () => {
      try {
        const res = await fetch('/api/random')
        if (!res.ok) throw new Error('random failed')
        const data = await res.json()
        if (data.stable_id) location.href = `/recipe/${data.stable_id}`
      } catch (err) {
        console.error('[cookster] random error:', err)
      }
    })
  }
  if (sourceSelect) sourceSelect.addEventListener('change', () => { page = 1; doSearch({ pushHistory: true }) })
  nextBtn.addEventListener('click', () => { if (page < totalPages()) { page++; doSearch({ pushHistory: true }) } })
  prevBtn.addEventListener('click', () => { if (page > 1) { page--; doSearch({ pushHistory: true }) } })

  if (filterChipsEl) {
    filterChipsEl.querySelectorAll('.filter-chip').forEach(chip => {
      chip.addEventListener('click', () => {
        const f = chip.dataset.filter
        if (activeFilters.has(f)) activeFilters.delete(f)
        else activeFilters.add(f)
        updateFilterChips()
        page = 1
        doSearch({ pushHistory: true })
      })
    })
  }

  // Close suggestions when clicking outside
  document.addEventListener('click', (e) => {
    if (suggestionsEl && !suggestionsEl.contains(e.target) && e.target !== qInput) {
      closeSuggestions()
    }
    if (recentSearchesEl && !recentSearchesEl.contains(e.target) && e.target !== qInput) {
      closeRecentSearches()
    }
  })

  // Lists panel listeners
  listsToggle.addEventListener('click', openListsPanel)
  listsClose.addEventListener('click', closeListsPanel)
  listsBackdrop.addEventListener('click', closeListsPanel)
  newListForm.addEventListener('submit', (e) => {
    e.preventDefault()
    const name = newListName.value.trim()
    if (name) {
      Lists.createList(name)
      newListName.value = ''
      renderListsPanel()
    }
  })

  window.addEventListener('cookster-lists-changed', () => {
    renderListsPanel()
    if (currentView === 'list') {
      if (Lists.getList(activeListId)) {
        showList(activeListId, { pushHistory: false, scroll: false })
      } else {
        currentView = 'search'
        activeListId = null
        if (lastQuery) doSearch({ pushHistory: false, scroll: false })
      }
    }
    if (currentView === 'search' && lastQuery) doSearch({ pushHistory: false, scroll: false })
  })

  window.addEventListener('popstate', (e) => {
    const p = new URLSearchParams(location.search)
    const newQ = (p.get('q') || '').trim()
    const newSource = (p.get('source') || '').trim()
    const newFilters = new Set((p.get('filters') || '').split(',').filter(Boolean))
    const view = p.get('view')
    const listId = p.get('list')
    if (sourceSelect) sourceSelect.value = newSource
    activeFilters = newFilters
    updateFilterChips()
    if (view === 'list' && listId) {
      page = 1
      qInput.value = newQ
      lastQuery = newQ
      showList(listId, { pushHistory: false, scroll: false })
    } else {
      page = Math.max(1, parseInt(p.get('page'), 10) || 1)
      qInput.value = newQ
      lastQuery = newQ
      lastSource = newSource
      currentView = 'search'
      activeListId = null
      doSearch({ pushHistory: false, scroll: false })
    }
  })

  // Theme toggle
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (suggestionsEl && suggestionsEl.classList.contains('open')) {
        closeSuggestions()
        return
      }
      if (listsPanel.classList.contains('open')) {
        closeListsPanel()
        return
      }
    }
    if (e.key === '/' && document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'TEXTAREA') {
      e.preventDefault()
      qInput.focus()
    }
  })

  function syncTheme() {
    const isDark = document.documentElement.getAttribute('data-theme') === 'dark'
    themeBtn.textContent = isDark ? '☀️ Light' : '🌙 Dark'
  }
  themeBtn.addEventListener('click', () => {
    const isDark = document.documentElement.getAttribute('data-theme') === 'dark'
    const next = isDark ? 'light' : 'dark'
    document.documentElement.setAttribute('data-theme', next)
    localStorage.setItem('theme', next)
    syncTheme()
  })
  const saved = localStorage.getItem('theme')
  if (saved) document.documentElement.setAttribute('data-theme', saved)
  syncTheme()

  // Initialise
  loadSources()
  loadStats()
  setInterval(loadStats, 60000)
  renderListsPanel()
  updateFilterChips()
  const viewParam = params.get('view')
  const listParam = params.get('list')
  if (viewParam === 'list' && listParam) {
    showList(listParam, { pushHistory: false, scroll: false })
  } else if (lastQuery) {
    doSearch({ pushHistory: false, scroll: false })
  } else {
    loadNewBooks()
  }
})()
