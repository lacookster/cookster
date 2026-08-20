(() => {
  const Lists = window.CooksterLists
  const qInput = document.getElementById('q')
  const sourceSelect = document.getElementById('source')
  const sortSelect = document.getElementById('sort')
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
  const copyShoppingBtn = document.getElementById('copy-shopping')
  const groupShoppingBtn = document.getElementById('group-shopping')
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
  const pantryInput = document.getElementById('pantry-input')
  const addPantryBtn = document.getElementById('add-pantry')
  const pantryListEl = document.getElementById('pantry-list')
  const pantryBoost = document.getElementById('pantry-boost')
  const excludeInput = document.getElementById('exclude')
  const haveInput = document.getElementById('have')
  const haveBox = document.getElementById('have-box')
  const whatIHaveToggle = document.getElementById('what-i-have-toggle')

  const installBtn = document.getElementById('install-btn')
  const onboardingOverlay = document.getElementById('onboarding-overlay')
  const onboardingDots = document.getElementById('onboarding-dots')
  const onboardingPrev = document.getElementById('onboarding-prev')
  const onboardingNext = document.getElementById('onboarding-next')
  const onboardingFinish = document.getElementById('onboarding-finish')

  const suggestionsEl = document.getElementById('suggestions')
  const recentSearchesEl = document.getElementById('recent-searches')
  const randomBtn = document.getElementById('random')
  const bookCountEl = document.getElementById('book-count')
  const bookCountInlineEl = document.getElementById('book-count-inline')
  const voiceBtn = document.getElementById('voice-search')
  const statsFavCountEl = document.getElementById('stats-fav-count')
  const statsWantCountEl = document.getElementById('stats-want-count')
  const statsCookedCountEl = document.getElementById('stats-cooked-count')
  const statsMostCookedEl = document.getElementById('stats-most-cooked')

  const params = new URLSearchParams(location.search)
  let page = Math.max(1, parseInt(params.get('page'), 10) || 1)
  let timer = null
  let suggestionTimer = null
  let lastQuery = (params.get('q') || '').trim()
  let lastSource = (params.get('source') || '').trim()
  let currentSort = (params.get('sort') || 'relevance').trim()
  let activeFilters = new Set((params.get('filters') || '').split(',').filter(Boolean))
  let boostPantry = (params.get('pantry') || '').trim() !== ''
  let whatIHaveMode = (params.get('have') || '').trim() !== ''
  let excludeValue = (params.get('exclude') || '').trim()
  let haveValue = (params.get('have') || '').trim()
  let totalResults = 0
  let currentView = 'search' // 'search' | 'list'
  let activeListId = null
  let activeSuggestion = -1
  const limit = 50
  const SHOPPING_GROUPED_KEY = 'cookster_shopping_grouped'
  let shoppingGrouped = false
  let searchAbortController = null
  let suggestAbortController = null
  let newBooksAbortController = null
  try {
    shoppingGrouped = localStorage.getItem(SHOPPING_GROUPED_KEY) === 'true'
  } catch (e) {}

  // Simple keyword-based aisle categorisation for the shopping list.
  const AISLE_KEYWORDS = {
    Produce: ['apple', 'tomato', 'lettuce', 'onion', 'garlic', 'carrot', 'potato', 'lemon', 'lime', 'banana', 'orange', 'strawberry', 'blueberry', 'spinach', 'kale', 'broccoli', 'cauliflower', 'pepper', 'cucumber', 'avocado', 'mushroom', 'ginger', 'basil', 'parsley', 'cilantro', 'mint', 'herb', 'coriander', 'scallion', 'spring onion', 'green onion', 'zucchini', 'courgette', 'aubergine', 'eggplant', 'squash', 'pumpkin', 'beetroot', 'radish', 'celery', 'asparagus', 'peas', 'beans', 'sweetcorn', 'corn', 'pear', 'peach', 'plum', 'grape', 'melon', 'watermelon', 'mango', 'pineapple', 'kiwi', 'passion fruit', 'rhubarb', 'apricot', 'cherry', 'date', 'fig', 'pomegranate'],
    Meat: ['chicken', 'beef', 'pork', 'lamb', 'turkey', 'sausage', 'bacon', 'ham', 'mince', 'steak', 'veal', 'duck', 'goose', 'rabbit', 'venison', 'meatball', 'burger', 'kebab', 'salami', 'chorizo', 'prosciutto', 'parma ham'],
    Seafood: ['fish', 'salmon', 'tuna', 'prawn', 'shrimp', 'cod', 'haddock', 'halibut', 'mackerel', 'sardine', 'anchovy', 'sea bass', 'bass', 'scallop', 'mussel', 'clam', 'oyster', 'crab', 'lobster', 'squid', 'octopus', 'crayfish', 'langoustine'],
    Dairy: ['egg', 'eggs', 'milk', 'cheese', 'butter', 'cream', 'yogurt', 'yoghurt', 'cheddar', 'mozzarella', 'parmesan', 'feta', 'ricotta', 'gouda', 'brie', 'camembert', 'goat cheese', 'halloumi', 'mascarpone', 'creme fraiche', 'sour cream', 'double cream', 'single cream', 'custard', 'fromage frais', 'quark'],
    Pantry: ['flour', 'sugar', 'rice', 'pasta', 'noodle', 'bread', 'oil', 'olive oil', 'vinegar', 'soy sauce', 'salt', 'pepper', 'spice', 'honey', 'maple syrup', 'baking powder', 'baking soda', 'yeast', 'stock', 'broth', 'canned', 'tin', 'tomato paste', 'ketchup', 'mustard', 'mayo', 'mayonnaise', 'jam', 'peanut butter', 'cereal', 'oats', 'lentil', 'bean', 'chickpea', 'couscous', 'quinoa', 'polenta', 'semolina', 'breadcrumb', 'crouton', 'nori', 'sesame seed', 'nut', 'almond', 'walnut', 'cashew', 'pecan', 'hazelnut', 'pistachio', 'raisin', 'sultana', 'currant', 'coconut', 'cocoa', 'chocolate', 'vanilla', 'cinnamon', 'nutmeg', 'ginger', 'clove', 'turmeric', 'cumin', 'coriander', 'paprika', 'chilli', 'oregano', 'thyme', 'rosemary', 'sage', 'bay leaf', 'saffron', 'caper', 'olive', 'pickle', 'anchovy', 'sardine', 'tuna', 'soup', 'sauce', 'gravy', 'marinade', 'dressing', 'worcestershire', 'harissa', 'gochujang', 'miso', 'tahini', 'hummus', 'salsa', 'relish', 'chutney', 'bbq sauce', 'soy', 'fish sauce', 'oyster sauce', 'hoisin', 'sriracha', 'tabasco', 'vanilla extract', 'almond extract', 'rose water', 'orange blossom', 'coconut milk', 'coconut cream', 'evaporated milk', 'condensed milk', 'polenta', 'cornmeal', 'cornflour', 'arrowroot', 'custard powder', 'gelatine', 'gelatin', 'agar', 'yeast', 'dried', 'sun-dried'],
    Frozen: ['frozen', 'ice cream', 'pastry', 'puff pastry', 'shortcrust', 'phyllo', 'filo', 'peas', 'chips', 'fries', 'frozen veg', 'frozen vegetable', 'frozen fruit', 'frozen berry', 'pizza', 'garlic bread'],
    Drinks: ['water', 'juice', 'coffee', 'tea', 'wine', 'beer', 'soda', 'soft drink', 'sparkling water', 'tonic', 'cordial', 'milkshake', 'smoothie', 'cocktail', 'spirits', 'whisky', 'whiskey', 'vodka', 'rum', 'gin', 'brandy', 'cider', 'prosecco', 'champagne', 'cognac']
  }

  const filterChipsEl = document.getElementById('filter-chips')
  const activeFiltersEl = document.getElementById('active-filters')
  const savedSearchesEl = document.getElementById('saved-searches')
  const saveSearchBtn = document.getElementById('save-search')

  if (lastQuery) qInput.value = lastQuery
  if (excludeValue && excludeInput) excludeInput.value = excludeValue
  if (haveValue && haveInput) haveInput.value = haveValue

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

  function parseListInput(value) {
    return value.split(',').map(s => s.trim()).filter(Boolean)
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
      const filterTags = Array.from(activeFilters).map(f =>
        `<span class="active-filter-tag" data-filter="${escapeHtml(f)}">${escapeHtml(f.replace(/-/g, ' '))} <button aria-label="Remove ${escapeHtml(f.replace(/-/g, ' '))} filter">×</button></span>`
      )
      const excludeTags = parseListInput(excludeValue).map(e =>
        `<span class="active-filter-tag exclude-tag" data-exclude="${escapeHtml(e)}">${escapeHtml(e)} <button aria-label="Remove ${escapeHtml(e)} exclusion">×</button></span>`
      )
      const tags = filterTags.concat(excludeTags)
      if (tags.length === 0) {
        activeFiltersEl.innerHTML = ''
      } else {
        activeFiltersEl.innerHTML = tags.join('')
      }
      activeFiltersEl.querySelectorAll('.active-filter-tag').forEach(tag => {
        tag.querySelector('button').addEventListener('click', () => {
          if (tag.dataset.filter) {
            activeFilters.delete(tag.dataset.filter)
            updateFilterChips()
            page = 1
            doSearch({ pushHistory: true })
          } else if (tag.dataset.exclude) {
            const remaining = parseListInput(excludeValue).filter(x => x.toLowerCase() !== tag.dataset.exclude.toLowerCase())
            excludeValue = remaining.join(', ')
            if (excludeInput) excludeInput.value = excludeValue
            updateFilterChips()
            page = 1
            doSearch({ pushHistory: true })
          }
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

  function saveRecentSearch(q, filters, sort) {
    if (!q) return
    const recents = loadRecentSearches()
    const updated = recents.filter(r => r.q !== q)
    updated.unshift({ q, filters: filters || '', sort: sort || 'relevance', source: sourceSelect ? sourceSelect.value : '', timestamp: Date.now() })
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
        <div class="recent-search-item" role="option" data-index="${i}" tabindex="0">
          <span class="recent-search-query">${escapeHtml(r.q)}</span>
          ${r.filters || r.sort && r.sort !== 'relevance' ? `<span class="recent-search-meta">${escapeHtml([r.filters, r.sort !== 'relevance' ? r.sort : ''].filter(Boolean).join(' · '))}</span>` : ''}
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
    if (sortSelect) sortSelect.value = r.sort || 'relevance'
    if (sourceSelect) sourceSelect.value = r.source || ''
    closeRecentSearches()
    doSearch({ pushHistory: true })
  }

  function renderSavedSearches() {
    if (!savedSearchesEl) return
    const saved = Lists.getSavedSearches()
    if (!saved.length) {
      savedSearchesEl.innerHTML = ''
      return
    }
    savedSearchesEl.innerHTML = `
      <span class="saved-searches-label">Saved</span>
      ${saved.map(s => `
        <span class="saved-search-chip" data-id="${escapeHtml(s.id)}" role="button" tabindex="0">
          <span class="saved-search-text">${escapeHtml(s.label || s.q || s.have)}${s.have ? ' 🥫' : ''}${s.sort && s.sort !== 'relevance' ? ` · ${escapeHtml(s.sort)}` : ''}</span>
          <button class="saved-search-delete" aria-label="Remove saved search">×</button>
        </span>
      `).join('')}
    `
    savedSearchesEl.querySelectorAll('.saved-search-chip').forEach(chip => {
      chip.addEventListener('click', (e) => {
        if (e.target.closest('.saved-search-delete')) return
        const s = Lists.getSavedSearches().find(x => x.id === chip.dataset.id)
        if (!s) return
        qInput.value = s.q
        activeFilters = new Set((s.filters || '').split(',').filter(Boolean))
        updateFilterChips()
        if (sortSelect) sortSelect.value = s.sort || 'relevance'
        if (sourceSelect) sourceSelect.value = s.source || ''
        if (haveInput) {
          haveInput.value = s.have || ''
          haveValue = s.have || ''
          updateWhatIHaveMode(!!s.have)
        }
        doSearch({ pushHistory: true })
      })
    })
    savedSearchesEl.querySelectorAll('.saved-search-delete').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation()
        const id = btn.closest('.saved-search-chip').dataset.id
        Lists.deleteSavedSearch(id)
      })
    })
    updateSaveSearchButton()
  }

  function updateSaveSearchButton() {
    if (!saveSearchBtn) return
    const q = qInput.value.trim()
    const have = haveInput ? haveInput.value.trim() : ''
    const enabled = (q || have) && currentView === 'search'
    saveSearchBtn.disabled = !enabled
  }

  function heartIcon(filled) {
    return '<span class="icon" data-icon="heart"></span>'
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
      : `<div class="card-media"><div class="placeholder"></div></div>`
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

  function renderRecentCard(r) {
    const imageHtml = r.image_url
      ? `<div class="card-media"><img src="${r.image_url}" alt="" loading="lazy"></div>`
      : `<div class="card-media"><div class="placeholder">No image</div></div>`
    return `
      <a class="card recent-card" href="/recipe/${r.stable_id || String(r.id)}">
        ${imageHtml}
        <div class="card-body">
          <div class="card-title-row">
            <h3>${escapeHtml(r.title)}</h3>
          </div>
          <div class="card-meta">
            <span>${escapeHtml(r.source)}</span>
            ${r.serves ? `<span class="card-serves">🍽 ${escapeHtml(r.serves)}</span>` : ''}
          </div>
        </div>
      </a>
    `
  }

  async function renderRecentlyViewed() {
    const container = document.getElementById('recently-viewed-home')
    if (!container) return
    const ids = Lists.getRecentViews()
    if (!ids.length) {
      container.innerHTML = ''
      container.style.display = 'none'
      return
    }
    try {
      const res = await fetch(`/api/recipes?ids=${ids.join(',')}`)
      if (!res.ok) throw new Error('Failed to load recent views')
      const data = await res.json()
      const byId = new Map(data.map(r => [String(r.stable_id || r.id), r]))
      const ordered = ids.map(id => byId.get(id)).filter(Boolean)
      if (!ordered.length) {
        container.innerHTML = ''
        container.style.display = 'none'
        return
      }
      container.style.display = ''
      container.innerHTML = `
        <h2>👁 Recently viewed</h2>
        <div class="recently-viewed-grid">${ordered.map(renderRecentCard).join('')}</div>
      `
    } catch (err) {
      console.error('[cookster] recent views error:', err)
      container.innerHTML = ''
      container.style.display = 'none'
    }
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
    if (newBooksAbortController) newBooksAbortController.abort()
    newBooksAbortController = new AbortController()
    let homeHtml = ''
    try {
      const res = await fetch('/api/new-books?limit=5', { signal: newBooksAbortController.signal })
      if (!res.ok) throw new Error('Failed to load new books')
      const data = await res.json()
      const books = data.books || []
      if (books.length === 0) {
        homeHtml = `
          <div class="empty empty-home">
            <h2>👋 Welcome to your cookbooks</h2>
            <p>Your newest cookbooks will appear here. Start typing to search for recipes, ingredients, or methods. Try a popular ingredient like <strong>chicken</strong>, <strong>chocolate</strong>, or <strong>tofu</strong>, or use the filters above for dietary shortcuts.</p>
            <div class="empty-actions">
              <button class="empty-example" data-q="chicken">🍗 Chicken</button>
              <button class="empty-example" data-q="chocolate cake">🍰 Chocolate cake</button>
              <button class="empty-example" data-q="quick">⏱ Quick</button>
            </div>
          </div>`
      } else {
        homeHtml = `
          <div class="new-books-home">
            <h2>📚 New cookbooks</h2>
            <div class="books-grid new-books-grid">${books.map(renderNewBookCard).join('')}</div>
          </div>`
      }
    } catch (err) {
      if (err.name === 'AbortError') return
      console.error('[cookster] new books error:', err)
      homeHtml = `
        <div class="empty empty-home">
          <h2>👋 Welcome to your cookbooks</h2>
          <p>Start typing to search for recipes, ingredients, or methods. Try a popular ingredient like <strong>chicken</strong>, <strong>chocolate</strong>, or <strong>tofu</strong>.</p>
          <div class="empty-actions">
            <button class="empty-example" data-q="chicken">🍗 Chicken</button>
            <button class="empty-example" data-q="pasta">🍝 Pasta</button>
            <button class="empty-example" data-q="dessert">🍰 Dessert</button>
          </div>
        </div>`
    }
    resultsEl.innerHTML = homeHtml + '<div id="recently-viewed-home" class="recently-viewed-home" style="display:none"></div>'
    resultsEl.querySelectorAll('.empty-example').forEach(btn => {
      btn.addEventListener('click', () => {
        qInput.value = btn.dataset.q
        page = 1
        doSearch({ pushHistory: true })
      })
    })
    renderRecentlyViewed()
    setBusy(false)
  }

  function renderCard(r) {
    const sid = r.stable_id || String(r.id)
    const isFav = Lists.isFavorite(sid)
    const isWantToTry = Lists.isWantToTry(sid)
    const inLists = Lists.listsForRecipe(sid)
    const imageHtml = r.image_url
      ? `<div class="card-media"><img src="${r.image_url}" alt="" loading="lazy"></div>`
      : `<div class="card-media"><div class="placeholder">No image</div></div>`

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
                <span class="icon" data-icon="utensils"></span>
              </button>
              <button class="fav-btn ${isFav ? 'active' : ''}" data-id="${sid}" aria-label="${isFav ? 'Remove from favourites' : 'Add to favourites'}">
                ${heartIcon(isFav)}
              </button>
            </div>
          </div>
          <div class="card-meta">
            <a href="/book?source=${encodeURIComponent(r.source_raw || r.source)}">${r.source}</a>
            ${r.serves ? `<span class="card-serves">🍽 ${escapeHtml(r.serves)}</span>` : ''}
            ${r.have_total ? `<span class="have-match-badge" title="${r.have_match_count} of ${r.have_total} ingredients matched">Matched ${r.have_match_count} / ${r.have_total}</span>` : ''}
            <span class="score">${(r.score || 0).toFixed(2)}</span>
          </div>
          ${renderRatingStars(Lists.getRating(sid), '0.95rem')}
          ${listChips}
          <div class="card-snippet">
            ${r.ingredients_snippet ? `<div><strong>Ingredients:</strong> ${r.ingredients_snippet}</div>` : ''}
            ${r.steps_snippet ? `<div style="margin-top:6px"><strong>Method:</strong> ${r.steps_snippet}</div>` : ''}
          </div>
          <div class="card-actions-row">
            <button class="card-action-btn add-shopping" data-id="${sid}" title="Add ingredients to shopping list"><span class="icon" data-icon="shopping-cart"></span> Shopping</button>
            <div class="card-plan">
              <input type="date" class="card-plan-date" data-id="${sid}" aria-label="Plan meal date">
              <button class="card-action-btn plan-meal" data-id="${sid}" title="Add to meal plan"><span class="icon" data-icon="calendar"></span> Plan</button>
            </div>
          </div>
        </div>
      </article>
    `
  }

  function renderSkeletonCards(count = 6) {
    const card = `
      <article class="card skeleton-card" aria-hidden="true">
        <div class="card-media"></div>
        <div class="card-body">
          <div class="card-title-row"><h3>Loading recipe title…</h3></div>
          <div class="card-meta">Loading details…</div>
          <div class="card-snippet">Loading ingredients and method snippet…</div>
        </div>
      </article>
    `
    resultsEl.innerHTML = card.repeat(count)
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
      <div class="suggestion-item" role="option" aria-selected="false" data-index="${i}" data-title="${escapeHtml(t)}">${highlightMatch(t, q)}</div>
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
    if (suggestAbortController) suggestAbortController.abort()
    suggestAbortController = new AbortController()
    try {
      const res = await fetch(`/api/suggest?q=${encodeURIComponent(q)}`, { signal: suggestAbortController.signal })
      if (!res.ok) throw new Error('suggest failed')
      const data = await res.json()
      renderSuggestions(data.suggestions || [], q)
    } catch (err) {
      if (err.name === 'AbortError') return
      console.error('[cookster] suggest error:', err)
    }
  }

  function updateActiveSuggestion() {
    const items = suggestionsEl.querySelectorAll('.suggestion-item')
    items.forEach((el, i) => {
      const active = i === activeSuggestion
      el.classList.toggle('active', active)
      el.setAttribute('aria-selected', String(active))
    })
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
    const sort = sortSelect ? sortSelect.value : 'relevance'
    const pantryItems = Lists.getPantry()
    const exclude = excludeInput ? excludeInput.value.trim() : ''
    const have = haveInput ? haveInput.value.trim() : ''
    if ((q || have) && currentView === 'search') {
      if (q) url.searchParams.set('q', q)
      else url.searchParams.delete('q')
      url.searchParams.set('page', String(page))
      if (source) url.searchParams.set('source', source)
      else url.searchParams.delete('source')
      if (filters) url.searchParams.set('filters', filters)
      else url.searchParams.delete('filters')
      if (sort && sort !== 'relevance') url.searchParams.set('sort', sort)
      else url.searchParams.delete('sort')
      if (boostPantry && pantryItems.length) url.searchParams.set('pantry', pantryItems.join(','))
      else url.searchParams.delete('pantry')
      if (exclude) url.searchParams.set('exclude', exclude)
      else url.searchParams.delete('exclude')
      if (have) url.searchParams.set('have', have)
      else url.searchParams.delete('have')
    } else {
      url.searchParams.delete('q')
      url.searchParams.delete('page')
      url.searchParams.delete('source')
      url.searchParams.delete('filters')
      url.searchParams.delete('sort')
      url.searchParams.delete('pantry')
      url.searchParams.delete('exclude')
      url.searchParams.delete('have')
    }
    if (currentView === 'list' && activeListId) {
      url.searchParams.set('view', 'list')
      url.searchParams.set('list', activeListId)
    } else {
      url.searchParams.delete('view')
      url.searchParams.delete('list')
    }
    if (push) {
      history.pushState({ q, page, source, filters, sort, currentView, activeListId, pantry: boostPantry ? pantryItems.join(',') : '', exclude, have }, '', url.toString())
    } else {
      history.replaceState({ q, page, source, filters, sort, currentView, activeListId, pantry: boostPantry ? pantryItems.join(',') : '', exclude, have }, '', url.toString())
    }
  }

  async function doSearch(options = {}) {
    const { pushHistory = false, scroll = true } = options
    const q = qInput.value.trim()
    const source = sourceSelect ? sourceSelect.value : ''
    const have = haveInput ? haveInput.value.trim() : ''
    const exclude = excludeInput ? excludeInput.value.trim() : ''

    if (!q && !have) {
      loadNewBooks()
      return
    }

    currentView = 'search'
    activeListId = null
    const sort = sortSelect ? sortSelect.value : currentSort
    if (q !== lastQuery || source !== lastSource || sort !== currentSort || have !== haveValue || exclude !== excludeValue) {
      page = 1
      lastQuery = q
      lastSource = source
      currentSort = sort
      haveValue = have
      excludeValue = exclude
    }

    setBusy(true)
    renderSkeletonCards()
    if (searchAbortController) searchAbortController.abort()
    searchAbortController = new AbortController()
    try {
      const qParam = q ? `q=${encodeURIComponent(q)}` : 'q='
      const sourceParam = source ? `&source=${encodeURIComponent(source)}` : ''
      const filterParam = activeFilters.size ? `&filters=${encodeURIComponent(filtersQuery())}` : ''
      const sortParam = sort && sort !== 'relevance' ? `&sort=${encodeURIComponent(sort)}` : ''
      const pantryItems = boostPantry ? Lists.getPantry() : []
      const pantryParam = (boostPantry && pantryItems.length) ? `&pantry=${encodeURIComponent(pantryItems.join(','))}` : ''
      const excludeParam = exclude ? `&exclude=${encodeURIComponent(exclude)}` : ''
      const haveParam = have ? `&have=${encodeURIComponent(have)}` : ''
      const res = await fetch(`/search?${qParam}&page=${page}&limit=${limit}${sourceParam}${filterParam}${sortParam}${pantryParam}${excludeParam}${haveParam}`, { signal: searchAbortController.signal })
      if (!res.ok) throw new Error(`Search failed (${res.status})`)
      const data = await res.json()
      totalResults = data.total || 0
      countEl.textContent = totalResults ? `${totalResults} result${totalResults === 1 ? '' : 's'}` : ''
      updatePager()

      resultsEl.innerHTML = ''
      if (!data.results || data.results.length === 0) {
        const activeFilterText = activeFilters.size ? ` with filters ${Array.from(activeFilters).map(f => f.replace(/-/g, ' ')).join(', ')}` : ''
        const haveText = have ? ` using “${escapeHtml(have)}”` : ''
        let suggestionHtml = ''
        if (q) {
          try {
            const corrRes = await fetch(`/api/suggest-correction?q=${encodeURIComponent(q)}`)
            if (corrRes.ok) {
              const corr = await corrRes.json()
              if (corr.suggestion && corr.suggestion.toLowerCase() !== q.toLowerCase()) {
                suggestionHtml = `<p>Did you mean <button class="text-link" id="suggestion-link">“${escapeHtml(corr.suggestion)}”</button>?</p>`
              }
            }
          } catch (e) {}
        }
        resultsEl.innerHTML = `
          <div class="empty empty-search">
            <h2>😕 No recipes found${q ? ` for “${escapeHtml(q)}”` : ''}${escapeHtml(activeFilterText)}${escapeHtml(haveText)}</h2>
            <p>We couldn't find a match. Try one of these popular searches, remove a filter, or check your spelling.</p>
            ${suggestionHtml}
            <div class="empty-actions">
              <button class="empty-example" data-q="chocolate">🍫 Chocolate</button>
              <button class="empty-example" data-q="chicken">🍗 Chicken</button>
              <button class="empty-example" data-q="vegetarian">🥬 Vegetarian</button>
              <button class="empty-example" data-q="quick dinner">⏱ Quick dinner</button>
            </div>
            <button id="empty-random" class="btn secondary">🎲 Surprise me</button>
          </div>`
        const suggestionLink = resultsEl.querySelector('#suggestion-link')
        if (suggestionLink) {
          suggestionLink.addEventListener('click', () => {
            qInput.value = suggestionLink.textContent.replace(/^“/, '').replace(/”$/, '')
            page = 1
            doSearch({ pushHistory: true })
          })
        }
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
        syncUrl(pushHistory)
        return
      }

      resultsEl.innerHTML = data.results.map(renderCard).join('')
      if (window.CooksterIcons) window.CooksterIcons.initIcons(resultsEl)
      syncUrl(pushHistory)
      if (q) saveRecentSearch(q, filtersQuery(), sort)
      updateSaveSearchButton()
      if (scroll) window.scrollTo({ top: 0, behavior: 'smooth' })
    } catch (err) {
      if (err.name === 'AbortError') return
      console.error('[cookster] search error:', err)
      resultsEl.innerHTML = `
        <div class="empty">
          <h2>🙈 Something went wrong</h2>
          <p>${err.message}. Try refreshing the page or search again in a moment.</p>
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
          <h2>📂 ${escapeHtml(title)} is empty</h2>
          <p>Save recipes you love by tapping the heart ❤️ or “Want to try” 🍽 button on any recipe card.</p>
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
      if (window.CooksterIcons) window.CooksterIcons.initIcons(resultsEl)
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

  async function handleResultsClick(e) {
    const favBtn = e.target.closest('.fav-btn')
    if (favBtn) {
      e.preventDefault()
      e.stopPropagation()
      const id = favBtn.dataset.id
      const nowFav = Lists.toggleFavorite(id)
      favBtn.classList.toggle('active', nowFav)
      favBtn.setAttribute('aria-label', nowFav ? 'Remove from favourites' : 'Add to favourites')
      favBtn.innerHTML = heartIcon(nowFav)
      if (window.CooksterIcons) window.CooksterIcons.initIcons(favBtn)
      return
    }

    const wantBtn = e.target.closest('.want-btn')
    if (wantBtn) {
      e.preventDefault()
      e.stopPropagation()
      const id = wantBtn.dataset.id
      const nowWant = Lists.toggleWantToTry(id)
      wantBtn.classList.toggle('active', nowWant)
      wantBtn.setAttribute('aria-label', nowWant ? 'Remove from Want to try' : 'Add to Want to try')
      wantBtn.setAttribute('title', nowWant ? 'Remove from Want to try' : 'Add to Want to try')
      showToast(nowWant ? 'Added to Want to try' : 'Removed from Want to try')
      return
    }

    const addShoppingBtn = e.target.closest('.add-shopping')
    if (addShoppingBtn) {
      e.preventDefault()
      e.stopPropagation()
      const id = addShoppingBtn.dataset.id
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
      return
    }

    const planMealBtn = e.target.closest('.plan-meal')
    if (planMealBtn) {
      e.preventDefault()
      e.stopPropagation()
      const id = planMealBtn.dataset.id
      const input = planMealBtn.parentElement.querySelector('.card-plan-date')
      const date = input.value
      if (!date) {
        showToast('Pick a date first')
        return
      }
      Lists.addMeal(date, id)
      showToast('Added to meal plan')
      input.value = ''
    }
  }

  resultsEl.addEventListener('click', handleResultsClick)

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

  function renderPantry() {
    const items = Lists.getPantry()
    if (!pantryListEl) return
    if (!items.length) {
      pantryListEl.innerHTML = '<p class="empty-lists">No pantry items yet.</p>'
    } else {
      pantryListEl.innerHTML = items.map(item => `
        <div class="pantry-item">
          <span class="pantry-text">${escapeHtml(item)}</span>
          <button class="icon-btn pantry-delete" data-item="${escapeHtml(item)}" aria-label="Remove">✕</button>
        </div>
      `).join('')
      pantryListEl.querySelectorAll('.pantry-delete').forEach(btn => {
        btn.addEventListener('click', () => {
          Lists.removePantryItem(btn.dataset.item)
          renderPantry()
          if (boostPantry && qInput.value.trim()) doSearch({ pushHistory: true })
        })
      })
    }
    if (pantryBoost) pantryBoost.disabled = !items.length
  }

  function addPantryItem() {
    if (!pantryInput) return
    const value = pantryInput.value.trim().toLowerCase()
    if (!value) return
    Lists.addPantryItem(value)
    pantryInput.value = ''
    renderPantry()
    if (boostPantry && qInput.value.trim()) doSearch({ pushHistory: true })
  }

  async function renderStats() {
    const data = Lists.load()
    if (statsFavCountEl) statsFavCountEl.textContent = data.favorites.length
    if (statsWantCountEl) statsWantCountEl.textContent = data.wantToTry.length
    if (statsCookedCountEl) statsCookedCountEl.textContent = Object.keys(data.cooked || {}).length
    if (!statsMostCookedEl) return
    const ids = Lists.getMostCookedIds(3)
    if (!ids.length) {
      statsMostCookedEl.innerHTML = '<p class="empty-lists">No recipes marked as cooked yet.</p>'
      return
    }
    try {
      const res = await fetch(`/api/recipes?ids=${ids.join(',')}`)
      if (!res.ok) throw new Error('Failed to load most cooked')
      const recipes = await res.json()
      const byId = new Map(recipes.map(r => [String(r.stable_id || r.id), r]))
      const ordered = ids.map(id => byId.get(id)).filter(Boolean)
      statsMostCookedEl.innerHTML = `
        <ol class="most-cooked-list">
          ${ordered.map(r => `
            <li>
              <a href="/recipe/${r.stable_id || String(r.id)}">${escapeHtml(r.title)}</a>
              <span class="most-cooked-source">${escapeHtml(r.source)}</span>
            </li>
          `).join('')}
        </ol>
      `
    } catch (err) {
      console.error('[cookster] stats error:', err)
      statsMostCookedEl.innerHTML = '<p class="empty-lists">Unable to load most cooked recipes.</p>'
    }
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
    renderPantry()
    renderStats()
  }

  // Shopping list helpers --------------------------------------------------
  function categorizeAisle(text) {
    const lower = text.toLowerCase()
    for (const category of Object.keys(AISLE_KEYWORDS)) {
      if (AISLE_KEYWORDS[category].some(keyword => lower.includes(keyword))) {
        return category
      }
    }
    return 'Other'
  }

  function groupShoppingByAisle(items) {
    const groups = { Other: [] }
    for (const category of Object.keys(AISLE_KEYWORDS)) {
      groups[category] = []
    }
    items.forEach(item => {
      groups[categorizeAisle(item.text)].push(item)
    })
    const ordered = {}
    for (const category of Object.keys(AISLE_KEYWORDS)) {
      if (groups[category].length) ordered[category] = groups[category]
    }
    if (groups.Other.length) ordered.Other = groups.Other
    return ordered
  }

  function formatShoppingListPlainText(items) {
    const lines = items.map(item => `[${item.checked ? 'x' : ' '}] ${item.text}`)
    return ['Shopping list', ...lines].join('\n')
  }

  function downloadShoppingList(text) {
    const blob = new Blob([text], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'shopping-list.txt'
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  function copyShoppingList() {
    const items = Lists.getShoppingItems()
    const text = formatShoppingListPlainText(items)
    navigator.clipboard.writeText(text)
      .then(() => showToast('Shopping list copied'))
      .catch(() => {
        downloadShoppingList(text)
        showToast('Shopping list downloaded')
      })
  }

  function shoppingItemHtml(item) {
    return `
      <label class="shopping-item ${item.checked ? 'checked' : ''}">
        <input type="checkbox" data-id="${item.id}" ${item.checked ? 'checked' : ''}>
        <span class="shopping-text">${escapeHtml(item.text)}${item.source ? ` <span class="shopping-source">(${escapeHtml(item.source)})</span>` : ''}</span>
        <button class="shopping-delete" data-id="${item.id}" aria-label="Remove">✕</button>
      </label>
    `
  }

  function updateShoppingHeaderButtons() {
    if (copyShoppingBtn) {
      copyShoppingBtn.style.display = Lists.getShoppingItems().length ? '' : 'none'
    }
    if (groupShoppingBtn) {
      groupShoppingBtn.style.display = Lists.getShoppingItems().length ? '' : 'none'
      groupShoppingBtn.textContent = shoppingGrouped ? 'Flat' : 'Group'
      groupShoppingBtn.title = shoppingGrouped ? 'Show flat list' : 'Group by aisle'
    }
  }

  function bindShoppingItems() {
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

  function renderShoppingList() {
    const items = Lists.getShoppingItems()
    if (!shoppingListEl) return
    updateShoppingHeaderButtons()
    if (!items.length) {
      shoppingListEl.innerHTML = ''
      shoppingEmptyEl.style.display = ''
      clearBoughtBtn.style.display = 'none'
      return
    }
    shoppingEmptyEl.style.display = 'none'
    clearBoughtBtn.style.display = ''

    if (shoppingGrouped) {
      const groups = groupShoppingByAisle(items)
      shoppingListEl.innerHTML = Object.entries(groups).map(([category, catItems]) => `
        <div class="shopping-group">
          <h4 class="shopping-category">${escapeHtml(category)}</h4>
          ${catItems.map(shoppingItemHtml).join('')}
        </div>
      `).join('')
    } else {
      shoppingListEl.innerHTML = items.map(shoppingItemHtml).join('')
    }

    bindShoppingItems()
  }

  // Meal plan drag-and-drop --------------------------------------------------
  let draggedMeal = null

  function handleMealDragStart(e) {
    draggedMeal = this
    this.classList.add('dragging')
    e.dataTransfer.setData('application/json', JSON.stringify({
      sourceDate: this.dataset.date,
      id: this.dataset.id
    }))
    e.dataTransfer.effectAllowed = 'move'
  }

  function handleMealDragEnd() {
    this.classList.remove('dragging')
    draggedMeal = null
    mealPlanEl.querySelectorAll('.day-card').forEach(card => card.classList.remove('drag-over'))
  }

  function handleDayDragOver(e) {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
    this.classList.add('drag-over')
  }

  function handleDayDragLeave(e) {
    if (!this.contains(e.relatedTarget)) {
      this.classList.remove('drag-over')
    }
  }

  async function handleDayDrop(e) {
    e.preventDefault()
    this.classList.remove('drag-over')
    const raw = e.dataTransfer.getData('application/json')
    if (!raw) return
    let data
    try {
      data = JSON.parse(raw)
    } catch (err) {
      return
    }
    const { sourceDate, id } = data
    const targetDate = this.dataset.date
    if (!sourceDate || !id || !targetDate || sourceDate === targetDate) return
    Lists.removeMeal(sourceDate, id)
    Lists.addMeal(targetDate, id)
    showToast('Meal moved')
    await renderMealPlan()
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
            <div class="meal-item" draggable="true" data-date="${date}" data-id="${id}">
              <a class="meal-title" href="/recipe/${id}" draggable="false">${escapeHtml(titles.get(id) || 'Recipe')}</a>
              <button class="meal-remove" data-date="${date}" data-id="${id}" aria-label="Remove" draggable="false">✕</button>
            </div>
          `).join('')
          return `
            <div class="day-card" data-date="${date}">
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
    mealPlanEl.querySelectorAll('.meal-item').forEach(item => {
      item.addEventListener('dragstart', handleMealDragStart)
      item.addEventListener('dragend', handleMealDragEnd)
    })
    mealPlanEl.querySelectorAll('.day-card').forEach(card => {
      card.addEventListener('dragover', handleDayDragOver)
      card.addEventListener('dragleave', handleDayDragLeave)
      card.addEventListener('drop', handleDayDrop)
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
  if (copyShoppingBtn) {
    copyShoppingBtn.addEventListener('click', copyShoppingList)
  }
  if (groupShoppingBtn) {
    groupShoppingBtn.addEventListener('click', () => {
      shoppingGrouped = !shoppingGrouped
      try {
        localStorage.setItem(SHOPPING_GROUPED_KEY, String(shoppingGrouped))
      } catch (e) {}
      updateShoppingHeaderButtons()
      renderShoppingList()
    })
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
    updateSaveSearchButton()
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
  if (saveSearchBtn) {
    saveSearchBtn.addEventListener('click', () => {
      const q = qInput.value.trim()
      const have = haveInput ? haveInput.value.trim() : ''
      if (!q && !have) return
      const name = prompt('Name this saved search:', q || have)
      if (!name) return
      Lists.saveSearch(name, q, filtersQuery(), sourceSelect ? sourceSelect.value : '', sortSelect ? sortSelect.value : 'relevance', have)
      renderSavedSearches()
      showToast('Search saved')
    })
  }
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
  if (sortSelect) sortSelect.addEventListener('change', () => { page = 1; doSearch({ pushHistory: true }) })
  if (addPantryBtn) addPantryBtn.addEventListener('click', addPantryItem)
  if (pantryInput) pantryInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); addPantryItem() } })
  if (pantryBoost) {
    pantryBoost.checked = boostPantry
    pantryBoost.addEventListener('change', () => {
      boostPantry = pantryBoost.checked
      if (qInput.value.trim() || (haveInput && haveInput.value.trim())) doSearch({ pushHistory: true })
      else syncUrl(false)
    })
  }

  function updateWhatIHaveMode(active) {
    whatIHaveMode = active
    if (whatIHaveToggle) {
      whatIHaveToggle.setAttribute('aria-pressed', String(active))
      whatIHaveToggle.classList.toggle('active', active)
    }
    if (haveBox) haveBox.style.display = active ? '' : 'none'
  }

  if (whatIHaveToggle) {
    whatIHaveToggle.addEventListener('click', () => {
      updateWhatIHaveMode(!whatIHaveMode)
      if (whatIHaveMode) {
        if (haveInput) haveInput.focus()
      } else if (haveInput) {
        haveInput.value = ''
        haveValue = ''
      }
      page = 1
      doSearch({ pushHistory: true })
    })
  }

  if (excludeInput) {
    excludeInput.addEventListener('input', () => {
      excludeValue = excludeInput.value.trim()
      debounceSearch()
    })
    excludeInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') { closeSuggestions(); doSearch({ pushHistory: true }) } })
  }

  if (haveInput) {
    haveInput.addEventListener('input', () => {
      haveValue = haveInput.value.trim()
      debounceSearch()
    })
    haveInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') { closeSuggestions(); doSearch({ pushHistory: true }) } })
  }

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
    renderSavedSearches()
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
    const newSort = (p.get('sort') || 'relevance').trim()
    boostPantry = (p.get('pantry') || '').trim() !== ''
    if (pantryBoost) pantryBoost.checked = boostPantry
    const newExclude = (p.get('exclude') || '').trim()
    const newHave = (p.get('have') || '').trim()
    if (excludeInput) excludeInput.value = newExclude
    excludeValue = newExclude
    if (haveInput) haveInput.value = newHave
    haveValue = newHave
    updateWhatIHaveMode(!!newHave)
    if (sourceSelect) sourceSelect.value = newSource
    if (sortSelect) sortSelect.value = newSort
    activeFilters = newFilters
    currentSort = newSort
    updateFilterChips()
    const view = p.get('view')
    const listId = p.get('list')
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
    themeBtn.innerHTML = isDark
      ? '<span class="icon" data-icon="sun"></span> Light'
      : '<span class="icon" data-icon="moon"></span> Dark'
    if (window.CooksterIcons) window.CooksterIcons.initIcons(themeBtn)
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

  function initVoiceSearch() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition
    if (!SpeechRecognition || !voiceBtn) {
      if (voiceBtn) voiceBtn.style.display = 'none'
      return
    }
    const recognition = new SpeechRecognition()
    recognition.continuous = false
    recognition.interimResults = false
    recognition.lang = document.documentElement.lang || 'en-US'

    voiceBtn.addEventListener('click', () => {
      try {
        recognition.start()
        voiceBtn.classList.add('listening')
        voiceBtn.setAttribute('aria-label', 'Listening…')
        showToast('🎤 Listening…')
      } catch (e) {
        console.error('[cookster] voice start error:', e)
        showToast('Could not start voice search')
      }
    })

    recognition.addEventListener('result', (e) => {
      const transcript = e.results[0][0].transcript
      qInput.value = transcript
      voiceBtn.classList.remove('listening')
      voiceBtn.setAttribute('aria-label', 'Search by voice')
      closeSuggestions()
      doSearch({ pushHistory: true })
    })

    recognition.addEventListener('error', (e) => {
      console.error('[cookster] voice error:', e.error)
      voiceBtn.classList.remove('listening')
      voiceBtn.setAttribute('aria-label', 'Search by voice')
      if (e.error === 'not-allowed') {
        showToast('Microphone access denied. Please allow it and try again.')
      } else if (e.error === 'no-speech') {
        showToast('No speech detected. Please try again.')
      } else {
        showToast('Voice search failed. Please try again.')
      }
    })

    recognition.addEventListener('end', () => {
      voiceBtn.classList.remove('listening')
      voiceBtn.setAttribute('aria-label', 'Search by voice')
    })
  }

  // Service worker registration and PWA install prompt ------------------------
  let deferredPrompt = null

  function registerServiceWorker() {
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('/static/sw.js')
        .then((reg) => console.log('[cookster] sw registered:', reg.scope))
        .catch((err) => console.error('[cookster] sw registration failed:', err))
    }
  }

  function showInstallButton() {
    if (!installBtn) return
    installBtn.hidden = false
  }

  function hideInstallButton() {
    if (!installBtn) return
    installBtn.hidden = true
    deferredPrompt = null
  }

  if (installBtn) {
    installBtn.addEventListener('click', async () => {
      if (!deferredPrompt) return
      deferredPrompt.prompt()
      try {
        const choice = await deferredPrompt.userChoice
        console.log('[cookster] install choice:', choice.outcome)
      } catch (err) {
        console.error('[cookster] install prompt error:', err)
      }
      hideInstallButton()
    })
  }

  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault()
    deferredPrompt = e
    showInstallButton()
  })

  window.addEventListener('appinstalled', () => {
    console.log('[cookster] app installed')
    hideInstallButton()
  })

  // Onboarding tour ----------------------------------------------------------
  const ONBOARDING_KEY = 'cookster_onboarding_done'
  let onboardingSlide = 0
  const onboardingSlides = onboardingOverlay ? Array.from(onboardingOverlay.querySelectorAll('.onboarding-slide')) : []
  const totalOnboardingSlides = onboardingSlides.length

  function renderOnboardingDots() {
    if (!onboardingDots) return
    onboardingDots.innerHTML = onboardingSlides.map((_, i) => `
      <button class="onboarding-dot ${i === onboardingSlide ? 'active' : ''}" data-slide="${i}" aria-label="Go to slide ${i + 1}"></button>
    `).join('')
    onboardingDots.querySelectorAll('.onboarding-dot').forEach(dot => {
      dot.addEventListener('click', () => goToOnboardingSlide(parseInt(dot.dataset.slide, 10)))
    })
  }

  function goToOnboardingSlide(index) {
    if (!onboardingOverlay || !onboardingSlides.length) return
    onboardingSlide = Math.max(0, Math.min(index, totalOnboardingSlides - 1))
    onboardingSlides.forEach((slide, i) => slide.classList.toggle('active', i === onboardingSlide))
    renderOnboardingDots()
    if (onboardingPrev) onboardingPrev.hidden = onboardingSlide === 0
    if (onboardingNext) onboardingNext.hidden = onboardingSlide === totalOnboardingSlides - 1
    if (onboardingFinish) onboardingFinish.hidden = onboardingSlide !== totalOnboardingSlides - 1
  }

  function openOnboarding() {
    if (!onboardingOverlay) return
    onboardingOverlay.hidden = false
    onboardingSlide = 0
    goToOnboardingSlide(0)
  }

  function closeOnboarding() {
    if (!onboardingOverlay) return
    onboardingOverlay.hidden = true
    try {
      localStorage.setItem(ONBOARDING_KEY, 'true')
    } catch (e) {}
  }

  if (onboardingPrev) {
    onboardingPrev.addEventListener('click', () => goToOnboardingSlide(onboardingSlide - 1))
  }
  if (onboardingNext) {
    onboardingNext.addEventListener('click', () => goToOnboardingSlide(onboardingSlide + 1))
  }
  if (onboardingFinish) {
    onboardingFinish.addEventListener('click', closeOnboarding)
  }
  if (onboardingOverlay) {
    onboardingOverlay.addEventListener('click', (e) => {
      if (e.target === onboardingOverlay) closeOnboarding()
    })
  }

  // Initialise
  registerServiceWorker()
  loadSources()
  if (sortSelect) sortSelect.value = currentSort
  loadStats()
  setInterval(loadStats, 60000)
  renderListsPanel()
  updateWhatIHaveMode(whatIHaveMode)
  updateFilterChips()
  renderSavedSearches()
  updateSaveSearchButton()
  initVoiceSearch()

  if (onboardingOverlay) {
    try {
      if (!localStorage.getItem(ONBOARDING_KEY)) {
        openOnboarding()
      }
    } catch (e) {}
  }

  const viewParam = params.get('view')
  const listParam = params.get('list')
  if (viewParam === 'list' && listParam) {
    showList(listParam, { pushHistory: false, scroll: false })
  } else if (lastQuery || haveValue) {
    doSearch({ pushHistory: false, scroll: false })
  } else {
    loadNewBooks()
  }
})()
