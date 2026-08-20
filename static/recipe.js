(() => {
  const Lists = window.CooksterLists
  const favEl = document.getElementById('recipe-fav')
  const recipeId = String(favEl ? favEl.dataset.id : (document.querySelector('[data-recipe-id]')?.dataset.recipeId || ''))
  const favBtn = favEl
  const wantBtn = document.getElementById('recipe-want')
  const listToggle = document.getElementById('list-toggle')
  const listMenu = document.getElementById('list-menu')
  const listMenuItems = document.getElementById('list-menu-items')
  const newListFromRecipe = document.getElementById('new-list-from-recipe')
  const backLink = document.getElementById('back-link')
  const addIngredientsBtn = document.getElementById('add-ingredients-shopping')
  const planDateInput = document.getElementById('recipe-plan-date')
  const addToMealPlanBtn = document.getElementById('add-to-meal-plan')
  const ratingEl = document.getElementById('recipe-rating')
  const ratingDisplayEl = document.getElementById('recipe-rating-display')
  const markCookedBtn = document.getElementById('mark-cooked')
  const cookedDateEl = document.getElementById('cooked-date')
  const notesInput = document.getElementById('recipe-notes')
  const substitutionInput = document.getElementById('recipe-substitution')
  const saveSubstitutionBtn = document.getElementById('save-substitution')
  const removeSubstitutionBtn = document.getElementById('remove-substitution')
  const savedSubstitutionEl = document.getElementById('saved-substitution')
  const videoInput = document.getElementById('recipe-video')
  const saveVideoBtn = document.getElementById('save-video')
  const watchVideoLink = document.getElementById('watch-video')
  const removeVideoBtn = document.getElementById('remove-video')
  const videoActions = document.getElementById('video-actions')
  const shareBtn = document.getElementById('share-recipe')
  const lightbox = document.getElementById('lightbox')
  const lightboxImg = document.getElementById('lightbox-img')
  const lightboxClose = document.getElementById('lightbox-close')
  const heroImg = document.querySelector('.recipe-hero-media img')
  const INGREDIENT_CHECKS_KEY = 'cookster_ingredient_checks'

  if (backLink && document.referrer && new URL(document.referrer).origin === location.origin) {
    backLink.href = document.referrer
  }

  function updateFav() {
    const isFav = Lists.isFavorite(recipeId)
    favBtn.classList.toggle('active', isFav)
    favBtn.innerHTML = '<span class="icon" data-icon="heart"></span>'
    if (window.CooksterIcons) window.CooksterIcons.initIcons(favBtn)
    favBtn.setAttribute('aria-label', isFav ? 'Remove from favourites' : 'Add to favourites')
  }

  function updateWant() {
    if (!wantBtn) return
    const isWant = Lists.isWantToTry(recipeId)
    wantBtn.classList.toggle('active', isWant)
    wantBtn.setAttribute('aria-label', isWant ? 'Remove from Want to try' : 'Add to Want to try')
    wantBtn.setAttribute('title', isWant ? 'Remove from Want to try' : 'Add to Want to try')
  }

  function renderMenu() {
    const lists = Lists.getLists()
    if (lists.length === 0) {
      listMenuItems.innerHTML = '<div class="list-menu-empty">No lists yet.</div>'
      return
    }
    listMenuItems.innerHTML = lists.map(list => {
      const checked = Lists.isInList(list.id, recipeId) ? 'checked' : ''
      return `
        <label class="list-menu-item">
          <input type="checkbox" data-list-id="${list.id}" ${checked}>
          <span>${escapeHtml(list.name)}</span>
        </label>
      `
    }).join('')

    listMenuItems.querySelectorAll('input[type="checkbox"]').forEach(cb => {
      cb.addEventListener('change', () => {
        if (cb.checked) {
          Lists.addToList(cb.dataset.listId, recipeId)
        } else {
          Lists.removeFromList(cb.dataset.listId, recipeId)
        }
      })
    })
  }

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
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

  function getIngredients() {
    const items = document.querySelectorAll('.ingredient-list li')
    return Array.from(items).map(li => li.textContent.trim()).filter(Boolean)
  }

  function getRecipeSource() {
    const meta = document.querySelector('.recipe-meta strong')
    return meta ? meta.textContent.trim() : ''
  }

  favBtn.addEventListener('click', () => {
    Lists.toggleFavorite(recipeId)
    updateFav()
  })

  if (wantBtn) {
    wantBtn.addEventListener('click', () => {
      Lists.toggleWantToTry(recipeId)
      updateWant()
      showToast(Lists.isWantToTry(recipeId) ? 'Added to Want to try' : 'Removed from Want to try')
    })
  }

  listToggle.addEventListener('click', (e) => {
    e.stopPropagation()
    listMenu.classList.toggle('open')
    renderMenu()
  })

  document.addEventListener('click', (e) => {
    if (!listMenu.contains(e.target) && e.target !== listToggle) {
      listMenu.classList.remove('open')
    }
  })

  newListFromRecipe.addEventListener('click', () => {
    const name = prompt('Name your new list:')
    if (!name) return
    const list = Lists.createList(name)
    if (list) Lists.addToList(list.id, recipeId)
    renderMenu()
  })

  addIngredientsBtn.addEventListener('click', () => {
    const ingredients = getIngredients()
    if (!ingredients.length) {
      showToast('No ingredients found')
      return
    }
    Lists.addShoppingItems(ingredients, recipeId, getRecipeSource())
    showToast(`Added ${ingredients.length} ingredient${ingredients.length === 1 ? '' : 's'} to shopping list`)
  })

  addToMealPlanBtn.addEventListener('click', () => {
    const date = planDateInput.value
    if (!date) {
      showToast('Pick a date first')
      return
    }
    Lists.addMeal(date, recipeId)
    showToast('Added to meal plan')
    planDateInput.value = ''
  })

  function renderHeaderRating() {
    if (!ratingDisplayEl) return
    const rating = Lists.getRating(recipeId)
    if (!rating) {
      ratingDisplayEl.innerHTML = ''
      ratingDisplayEl.style.display = 'none'
      return
    }
    ratingDisplayEl.style.display = ''
    const stars = [1, 2, 3, 4, 5].map(i => `<span class="rating-star-display ${i <= rating ? 'active' : ''}">★</span>`).join('')
    ratingDisplayEl.innerHTML = `<span class="rating-display" title="${rating} star${rating === 1 ? '' : 's'}">${stars}</span>`
  }

  function renderRating() {
    const rating = Lists.getRating(recipeId)
    ratingEl.innerHTML = [1, 2, 3, 4, 5].map(star => {
      const active = star <= rating ? 'active' : ''
      return `<button class="star ${active}" data-star="${star}" aria-label="Rate ${star} stars">★</button>`
    }).join('')
    ratingEl.querySelectorAll('.star').forEach(btn => {
      btn.addEventListener('click', () => {
        const star = parseInt(btn.dataset.star, 10)
        const current = Lists.getRating(recipeId)
        Lists.setRating(recipeId, current === star ? 0 : star)
        renderRating()
      })
    })
    renderHeaderRating()
  }

  function renderCooked() {
    const date = Lists.getCookedDate(recipeId)
    if (date) {
      const formatted = new Date(date).toLocaleDateString()
      cookedDateEl.textContent = `You cooked this on ${formatted}`
    } else {
      cookedDateEl.textContent = ''
    }
  }

  markCookedBtn.addEventListener('click', () => {
    Lists.markCooked(recipeId)
    renderCooked()
    showToast('Marked as cooked today')
  })

  if (notesInput) {
    notesInput.value = Lists.getNote(recipeId)
    let saveTimer = null
    notesInput.addEventListener('input', () => {
      clearTimeout(saveTimer)
      saveTimer = setTimeout(() => Lists.setNote(recipeId, notesInput.value), 400)
    })
    notesInput.addEventListener('blur', () => Lists.setNote(recipeId, notesInput.value))
  }

  function renderRelatedCard(r) {
    const imageHtml = r.image_url
      ? `<div class="related-media"><img src="${r.image_url}" alt="" loading="lazy"></div>`
      : `<div class="related-media placeholder"></div>`
    return `
      <a class="related-card" href="/recipe/${r.stable_id}">
        ${imageHtml}
        <div class="related-body">
          <div class="related-title">${escapeHtml(r.title)}</div>
          <div class="related-source">${escapeHtml(r.source)}</div>
        </div>
      </a>
    `
  }

  let relatedAbortController = null
  let nutritionAbortController = null

  async function loadRelated() {
    if (relatedAbortController) relatedAbortController.abort()
    relatedAbortController = new AbortController()
    try {
      const res = await fetch(`/api/related/${encodeURIComponent(recipeId)}`, { signal: relatedAbortController.signal })
      if (!res.ok) throw new Error('related failed')
      const data = await res.json()

      const sameBookSection = document.getElementById('related-same-book')
      const sameBookResults = document.getElementById('same-book-results')
      if (sameBookResults && data.same_book && data.same_book.length) {
        sameBookResults.innerHTML = data.same_book.map(renderRelatedCard).join('')
        sameBookSection.style.display = ''
      }

      const similarSection = document.getElementById('related-similar')
      const similarResults = document.getElementById('similar-results')
      if (similarResults && data.similar && data.similar.length) {
        similarResults.innerHTML = data.similar.map(renderRelatedCard).join('')
        similarSection.style.display = ''
      }
    } catch (err) {
      if (err.name === 'AbortError') return
      console.error('[cookster] related error:', err)
    }
  }

  // Servings scaler + unit converter ----------------------------------------
  const scaleInput = document.getElementById('scale-input')
  const ingredientListEl = document.getElementById('ingredient-list')
  const unitMetricBtn = document.getElementById('unit-metric')
  const unitImperialBtn = document.getElementById('unit-imperial')
  const unitResetBtn = document.getElementById('unit-reset')
  let currentUnitMode = 'metric'

  const FRACTIONS = {
    '\u00bc': 0.25, '\u00bd': 0.5, '\u00be': 0.75,
    '\u2153': 1/3, '\u2154': 2/3, '\u215b': 1/8,
    '\u215c': 3/8, '\u215d': 5/8, '\u215e': 7/8
  }

  function parseLeadingNumber(text) {
    const fraction = text.split('').find(c => FRACTIONS[c])
    if (fraction) {
      const idx = text.indexOf(fraction)
      const before = text.slice(0, idx).trim()
      const after = text.slice(idx + 1).trim()
      const wholeMatch = before.match(/(\d+)\s*$/)
      const whole = wholeMatch ? parseInt(wholeMatch[1], 10) : 0
      const val = whole + FRACTIONS[fraction]
      const prefix = wholeMatch ? before.slice(0, wholeMatch.index) : before
      return { val, prefix: prefix.trim(), suffix: after, raw: text.slice(0, idx + 1).trim() }
    }
    const m = text.match(/^(\d+(?:\.\d+)?)(?:\s*\/\s*(\d+))?\s*/)
    if (!m) return null
    let val = parseFloat(m[1])
    if (m[2]) val /= parseInt(m[2], 10)
    return { val, prefix: '', suffix: text.slice(m[0].length), raw: m[0].trim() }
  }

  function formatNumber(num) {
    if (Math.abs(num - Math.round(num)) < 0.001) return String(Math.round(num))
    const rounded = Math.round(num * 100) / 100
    return String(rounded)
  }

  function scaleLine(line, factor) {
    const parsed = parseLeadingNumber(line)
    if (!parsed) return line
    const scaled = parsed.val * factor
    const newVal = formatNumber(scaled)
    return (parsed.prefix + ' ' + newVal + ' ' + parsed.suffix).replace(/\s+/g, ' ').trim()
  }

  const IMPERIAL_CONVERSIONS = {
    g: { factor: 0.03527, unit: 'oz' },
    kg: { factor: 2.2046, unit: 'lb' },
    ml: { factor: 0.0338, unit: 'fl oz' },
    l: { factor: 1.057, unit: 'qt' },
  }

  function convertToImperial(line) {
    return line.replace(/\b(\d+(?:\.\d+)?)\s*(kg|ml|g|l)\b/gi, (match, qty, unit) => {
      const info = IMPERIAL_CONVERSIONS[unit.toLowerCase()]
      if (!info) return match
      const val = parseFloat(qty) * info.factor
      const rounded = Math.round(val * 10) / 10
      return `${rounded} ${info.unit}`
    })
  }

  function displayLine(metricLine) {
    if (currentUnitMode === 'imperial') {
      return convertToImperial(metricLine)
    }
    return metricLine
  }

  function setUnitMode(mode) {
    currentUnitMode = mode
    const metricActive = mode === 'metric'
    if (unitMetricBtn) {
      unitMetricBtn.classList.toggle('active', metricActive)
      unitMetricBtn.setAttribute('aria-pressed', String(metricActive))
    }
    if (unitImperialBtn) {
      unitImperialBtn.classList.toggle('active', !metricActive)
      unitImperialBtn.setAttribute('aria-pressed', String(!metricActive))
    }
    ingredientListEl.querySelectorAll('li').forEach(li => {
      const span = li.querySelector('.ingredient-check span')
      const metricLine = li.dataset.metric || li.dataset.original || (span ? span.textContent : li.textContent)
      const display = displayLine(metricLine)
      if (span) span.textContent = display
      else li.textContent = display
    })
  }

  function applyScale() {
    if (!ingredientListEl || !scaleInput) return
    const factor = parseFloat(scaleInput.value) || 1
    ingredientListEl.querySelectorAll('li').forEach(li => {
      const original = li.dataset.original || (li.querySelector('.ingredient-check span') ? li.querySelector('.ingredient-check span').textContent : li.textContent)
      if (!li.dataset.original) li.dataset.original = original
      const metric = scaleLine(original, factor)
      li.dataset.metric = metric
      const span = li.querySelector('.ingredient-check span')
      const display = displayLine(metric)
      if (span) span.textContent = display
      else li.textContent = display
    })
  }

  function initIngredientMetrics() {
    if (!ingredientListEl) return
    ingredientListEl.querySelectorAll('li').forEach(li => {
      const span = li.querySelector('.ingredient-check span')
      const text = li.dataset.original || (span ? span.textContent : li.textContent)
      if (!li.dataset.original) li.dataset.original = text
      if (!li.dataset.metric) li.dataset.metric = text
    })
  }

  initIngredientMetrics()

  if (unitMetricBtn) unitMetricBtn.addEventListener('click', () => setUnitMode('metric'))
  if (unitImperialBtn) unitImperialBtn.addEventListener('click', () => setUnitMode('imperial'))
  if (unitResetBtn) {
    unitResetBtn.addEventListener('click', () => {
      currentUnitMode = 'metric'
      if (scaleInput) scaleInput.value = 1
      setUnitMode('metric')
      applyScale()
    })
  }

  if (scaleInput) {
    scaleInput.addEventListener('input', () => {
      if (parseFloat(scaleInput.value) < 0.25) scaleInput.value = 0.25
      applyScale()
    })
  }
  document.querySelectorAll('.scale-preset').forEach(btn => {
    btn.addEventListener('click', () => {
      if (!scaleInput) return
      scaleInput.value = btn.dataset.scale
      applyScale()
    })
  })

  // Cooking mode -----------------------------------------------------------
  const startCookingBtn = document.getElementById('start-cooking')
  const cookingOverlay = document.getElementById('cooking-overlay')
  const cookingClose = document.getElementById('cooking-close')
  const cookingPrev = document.getElementById('cooking-prev')
  const cookingNext = document.getElementById('cooking-next')
  const cookingStepText = document.getElementById('cooking-step-text')
  const cookingStepNum = document.getElementById('cooking-step-num')
  const cookingStepTotal = document.getElementById('cooking-step-total')
  const cookingTimersEl = document.getElementById('cooking-timers')
  const cookingReadBtn = document.getElementById('cooking-read')
  const cookingVoice = document.getElementById('cooking-voice')
  const cookingFullscreen = document.getElementById('cooking-fullscreen')
  const voiceStatus = document.getElementById('voice-status')
  const cookingRepeat = document.getElementById('cooking-repeat')
  const cookingResetTimers = document.getElementById('cooking-reset-timers')

  let cookingSteps = []
  let cookingIndex = 0
  const activeTimers = new Map()
  let wakeLock = null
  let recognition = null
  let isListening = false

  function initCooking() {
    if (!cookingOverlay) return
    cookingSteps = Array.from(document.querySelectorAll('.step')).map(s => s.textContent.trim()).filter(Boolean)
    cookingIndex = 0
    if (!cookingSteps.length) return
    cookingStepTotal.textContent = cookingSteps.length
    renderCookingStep()
    cookingOverlay.classList.add('open')
    cookingOverlay.setAttribute('aria-hidden', 'false')
    document.body.style.overflow = 'hidden'
    requestWakeLock()
  }

  function closeCooking() {
    if (!cookingOverlay) return
    stopSpeech()
    stopVoiceListening()
    releaseWakeLock()
    cookingOverlay.classList.remove('open')
    cookingOverlay.setAttribute('aria-hidden', 'true')
    document.body.style.overflow = ''
  }

  function requestWakeLock() {
    if (!('wakeLock' in navigator)) return
    try {
      navigator.wakeLock.request('screen').then(lock => {
        wakeLock = lock
        lock.addEventListener('release', () => {
          wakeLock = null
          if (cookingOverlay && cookingOverlay.classList.contains('open')) {
            requestWakeLock()
          }
        })
      }).catch(err => {
        console.warn('[cookster] wake lock request failed:', err)
      })
    } catch (e) {
      console.warn('[cookster] wake lock error:', e)
    }
  }

  function releaseWakeLock() {
    if (wakeLock) {
      try {
        wakeLock.release()
      } catch (e) {}
      wakeLock = null
    }
  }

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && cookingOverlay && cookingOverlay.classList.contains('open') && !wakeLock) {
      requestWakeLock()
    }
  })

  function stopSpeech() {
    try {
      if (window.speechSynthesis) window.speechSynthesis.cancel()
    } catch (e) {}
  }

  function readCurrentStep() {
    if (!window.speechSynthesis || !cookingSteps.length) return
    stopSpeech()
    const text = cookingSteps[cookingIndex]
    if (!text) return
    const utterance = new SpeechSynthesisUtterance(text)
    window.speechSynthesis.speak(utterance)
  }

  function toggleSpeech() {
    if (!window.speechSynthesis || !cookingSteps.length) return
    if (window.speechSynthesis.speaking || window.speechSynthesis.pending) {
      window.speechSynthesis.cancel()
      return
    }
    readCurrentStep()
  }

  function setVoiceStatus(message) {
    if (!voiceStatus) return
    voiceStatus.textContent = message || ''
    voiceStatus.classList.toggle('has-content', !!message)
  }

  function initVoiceRecognition() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition
    if (!SpeechRecognition) return null
    const r = new SpeechRecognition()
    r.continuous = true
    r.interimResults = true
    r.lang = document.documentElement.lang || 'en-US'
    r.onresult = (event) => {
      const last = event.results[event.results.length - 1]
      const transcript = last[0].transcript.trim().toLowerCase()
      setVoiceStatus(transcript)
      if (last.isFinal) {
        handleVoiceCommand(transcript)
      }
    }
    r.onerror = (event) => {
      if (event.error === 'no-speech') {
        setVoiceStatus('No speech detected')
      } else if (event.error === 'aborted' || event.error === 'not-allowed') {
        setVoiceStatus('Voice error: ' + event.error)
        stopVoiceListening()
      } else {
        setVoiceStatus('Voice error: ' + event.error)
      }
    }
    r.onnomatch = () => {
      setVoiceStatus('No command recognised')
    }
    r.onend = () => {
      if (isListening) {
        try { r.start() } catch (e) {}
      }
    }
    return r
  }

  function handleVoiceCommand(command) {
    if (command.includes('next') || command.includes('forward') || command.includes('continue')) {
      cookingNext.click()
    } else if (command.includes('previous') || command.includes('back')) {
      cookingPrev.click()
    } else if (command.includes('repeat') || command.includes('again') || command.includes('read')) {
      readCurrentStep()
    } else if (command.includes('close') || command.includes('stop') || command.includes('exit')) {
      closeCooking()
    } else if (command.includes('timer') || command.includes('start timer')) {
      startAllStepTimers()
    } else if (command.includes('fullscreen') || command.includes('full screen')) {
      toggleFullscreen()
    } else {
      setVoiceStatus('Unknown command: "' + command + '"')
    }
  }

  function startVoiceListening() {
    if (!recognition) recognition = initVoiceRecognition()
    if (!recognition) return
    try {
      isListening = true
      recognition.start()
      if (cookingVoice) {
        cookingVoice.classList.add('listening')
        cookingVoice.setAttribute('aria-label', 'Stop voice commands')
        cookingVoice.setAttribute('title', 'Stop voice commands')
      }
      setVoiceStatus('Listening…')
    } catch (e) {
      isListening = false
      setVoiceStatus('Could not start voice listening')
      console.error('[cookster] voice start error:', e)
    }
  }

  function stopVoiceListening() {
    isListening = false
    if (cookingVoice) {
      cookingVoice.classList.remove('listening')
      cookingVoice.setAttribute('aria-label', 'Voice commands')
      cookingVoice.setAttribute('title', 'Voice commands')
    }
    setVoiceStatus('')
    if (recognition) {
      try { recognition.stop() } catch (e) {}
    }
  }

  function toggleVoiceListening() {
    if (isListening) stopVoiceListening()
    else startVoiceListening()
  }

  function startAllStepTimers() {
    if (!cookingTimersEl) return
    cookingTimersEl.querySelectorAll('.cooking-timer').forEach(btn => {
      if (!btn.disabled && !btn.classList.contains('timer-running')) {
        btn.click()
      }
    })
  }

  function toggleFullscreen() {
    const docEl = document.documentElement
    try {
      if (!document.fullscreenElement) {
        docEl.requestFullscreen()
      } else {
        document.exitFullscreen()
      }
    } catch (e) {
      showToast('Fullscreen not available')
      console.error('[cookster] fullscreen error:', e)
    }
  }

  function updateFullscreenIcon() {
    if (!cookingFullscreen) return
    const isFullscreen = !!document.fullscreenElement
    cookingFullscreen.innerHTML = '<span class="icon" data-icon="expand"></span>'
    if (window.CooksterIcons) window.CooksterIcons.initIcons(cookingFullscreen)
    cookingFullscreen.setAttribute('aria-label', isFullscreen ? 'Exit fullscreen' : 'Enter fullscreen')
    cookingFullscreen.setAttribute('title', isFullscreen ? 'Exit fullscreen' : 'Enter fullscreen')
    cookingFullscreen.classList.toggle('fullscreen-active', isFullscreen)
  }

  function resetStepTimers() {
    activeTimers.forEach(t => clearInterval(t.interval))
    activeTimers.clear()
    renderCookingTimers()
  }

  function renderCookingTimers() {
    if (!cookingTimersEl) return
    const times = parseStepTimes(cookingSteps[cookingIndex])
    if (!times.length) {
      cookingTimersEl.innerHTML = ''
      return
    }
    const seen = new Set()
    const unique = times.filter(t => {
      const key = t.value + '-' + t.unit
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
    cookingTimersEl.innerHTML = `
      <div class="cooking-timers-label">⏱ Timers for this step</div>
      <div class="cooking-timers-list">
        ${unique.map(t => `<button class="btn secondary cooking-timer" data-seconds="${t.seconds}" data-label="${escapeHtml(t.original)}">Start ${escapeHtml(t.original)}</button>`).join('')}
      </div>
    `
    cookingTimersEl.querySelectorAll('.cooking-timer').forEach(btn => {
      btn.addEventListener('click', () => {
        const seconds = parseInt(btn.dataset.seconds, 10)
        startTimer(seconds, btn.dataset.label, btn)
      })
    })
  }

  function parseStepTimes(text) {
    const times = []
    // Match patterns like "25 minutes", "1 hour", "1 hour 30 minutes", "30 min", "2 hr"
    const pattern = /(\d+(?:\.\d+)?)\s*(min(?:ute)?s?|hr?s?|hours?)/gi
    let match
    while ((match = pattern.exec(text)) !== null) {
      const value = parseFloat(match[1])
      const unit = match[2].toLowerCase()
      const seconds = unit.startsWith('hour') || unit === 'hr' || unit === 'hrs' ? value * 3600 : value * 60
      times.push({ original: match[0], value, unit, seconds })
    }
    return times
  }

  function formatTime(totalSeconds) {
    const m = Math.floor(totalSeconds / 60)
    const s = totalSeconds % 60
    return `${m}:${String(s).padStart(2, '0')}`
  }

  function startTimer(seconds, label, button) {
    const id = Date.now() + Math.random()
    let remaining = Math.round(seconds)
    if (activeTimers.has(id)) return
    activeTimers.set(id, { remaining, interval: null })

    button.disabled = true
    button.classList.add('timer-running')
    button.textContent = `${label}: ${formatTime(remaining)}`

    const interval = setInterval(() => {
      remaining--
      const t = activeTimers.get(id)
      if (!t) { clearInterval(interval); return }
      t.remaining = remaining
      if (remaining <= 0) {
        clearInterval(interval)
        activeTimers.delete(id)
        button.disabled = false
        button.classList.remove('timer-running')
        button.textContent = `${label}: done!`
        // Try a gentle alert if the user left the tab.
        try {
          if (Notification.permission === 'granted') {
            new Notification('Cookster timer', { body: `${label} is ready!` })
          } else {
            showToast(`${label} timer is up`)
          }
        } catch (e) { showToast(`${label} timer is up`) }
        return
      }
      button.textContent = `${label}: ${formatTime(remaining)}`
    }, 1000)
    activeTimers.get(id).interval = interval

    // Request notification permission on first timer.
    try {
      if (Notification.permission === 'default') Notification.requestPermission()
    } catch (e) {}
  }

  function renderCookingStep() {
    stopSpeech()
    cookingStepNum.textContent = cookingIndex + 1
    cookingStepText.textContent = cookingSteps[cookingIndex]
    cookingPrev.disabled = cookingIndex === 0
    cookingNext.textContent = cookingIndex === cookingSteps.length - 1 ? 'Done' : 'Next →'

    // Clear previous timers when changing steps.
    activeTimers.forEach(t => clearInterval(t.interval))
    activeTimers.clear()
    renderCookingTimers()
  }

  if (startCookingBtn) startCookingBtn.addEventListener('click', initCooking)
  if (cookingClose) cookingClose.addEventListener('click', closeCooking)
  if (cookingPrev) cookingPrev.addEventListener('click', () => { if (cookingIndex > 0) { cookingIndex--; renderCookingStep() } })
  if (cookingNext) cookingNext.addEventListener('click', () => { if (cookingIndex < cookingSteps.length - 1) { cookingIndex++; renderCookingStep() } else { closeCooking() } })
  if (cookingOverlay) {
    cookingOverlay.addEventListener('click', (e) => { if (e.target === cookingOverlay) closeCooking() })
  }
  if (cookingReadBtn) {
    if (!window.speechSynthesis) cookingReadBtn.style.display = 'none'
    cookingReadBtn.addEventListener('click', toggleSpeech)
  }
  if (cookingRepeat) {
    if (!window.speechSynthesis) cookingRepeat.style.display = 'none'
    cookingRepeat.addEventListener('click', readCurrentStep)
  }
  if (cookingResetTimers) cookingResetTimers.addEventListener('click', resetStepTimers)
  if (cookingVoice) {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition
    if (!SpeechRecognition) {
      cookingVoice.style.display = 'none'
    } else {
      cookingVoice.addEventListener('click', toggleVoiceListening)
    }
  }
  if (cookingFullscreen) {
    cookingFullscreen.addEventListener('click', toggleFullscreen)
    updateFullscreenIcon()
  }
  document.addEventListener('fullscreenchange', updateFullscreenIcon)
  document.addEventListener('keydown', (e) => {
    if (!cookingOverlay || !cookingOverlay.classList.contains('open')) return
    if (e.key === 'Escape') closeCooking()
    if (e.key === 'ArrowRight') cookingNext.click()
    if (e.key === 'ArrowLeft') cookingPrev.click()
    if (e.key === 'r' || e.key === 'R') toggleSpeech()
  })

  // Copy link and export ---------------------------------------------------
  const copyLinkBtn = document.getElementById('copy-link')
  const exportMarkdownBtn = document.getElementById('export-markdown')
  const pageUrl = location.href

  if (copyLinkBtn) {
    copyLinkBtn.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(pageUrl)
        showToast('Recipe link copied')
      } catch (e) {
        showToast('Could not copy link')
      }
    })
  }

  async function copyLinkFallback() {
    try {
      await navigator.clipboard.writeText(pageUrl)
      showToast('Recipe link copied')
    } catch (e) {
      showToast('Could not copy link')
    }
  }

  if (shareBtn) {
    shareBtn.addEventListener('click', async () => {
      if (navigator.share) {
        try {
          await navigator.share({ title: document.title, url: pageUrl })
        } catch (e) {
          if (e.name !== 'AbortError') {
            console.error('[cookster] share error:', e)
            await copyLinkFallback()
          }
        }
      } else {
        await copyLinkFallback()
      }
    })
  }

  function escapeMarkdownLine(text) {
    return text.replace(/^#/gm, '\\#')
  }

  if (exportMarkdownBtn) {
    exportMarkdownBtn.addEventListener('click', () => {
      const title = document.querySelector('.recipe-header h1').textContent.trim()
      const source = getRecipeSource()
      const ingredients = getIngredients()
      const steps = Array.from(document.querySelectorAll('.step')).map(s => s.textContent.trim()).filter(Boolean)
      let md = `# ${escapeMarkdownLine(title)}\n\n*From ${escapeMarkdownLine(source)}*\n\n## Ingredients\n\n`
      md += ingredients.map(i => `- ${escapeMarkdownLine(i)}`).join('\n')
      md += `\n\n## Method\n\n`
      md += steps.map((s, i) => `${i + 1}. ${escapeMarkdownLine(s)}`).join('\n\n')
      md += `\n\n[View in Cookster](${pageUrl})\n`
      navigator.clipboard.writeText(md).catch(() => {})
      const blob = new Blob([md], { type: 'text/markdown' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${title.replace(/[^a-z0-9]+/gi, '_').toLowerCase()}.md`
      a.click()
      URL.revokeObjectURL(url)
      showToast('Markdown exported')
    })
  }

  // Ingredient checkboxes --------------------------------------------------
  function loadIngredientChecks() {
    try {
      const map = JSON.parse(localStorage.getItem(INGREDIENT_CHECKS_KEY) || '{}')
      return Array.isArray(map[recipeId]) ? map[recipeId] : []
    } catch (e) {
      return []
    }
  }
  function saveIngredientChecks(indices) {
    try {
      const map = JSON.parse(localStorage.getItem(INGREDIENT_CHECKS_KEY) || '{}')
      map[recipeId] = indices
      localStorage.setItem(INGREDIENT_CHECKS_KEY, JSON.stringify(map))
    } catch (e) {}
  }
  function initIngredientChecks() {
    const checked = loadIngredientChecks()
    document.querySelectorAll('.ingredient-check input[type="checkbox"]').forEach(cb => {
      const idx = cb.dataset.index
      const isChecked = checked.includes(idx)
      cb.checked = isChecked
      cb.closest('.ingredient-check').classList.toggle('checked', isChecked)
      cb.addEventListener('change', () => {
        const all = Array.from(document.querySelectorAll('.ingredient-check input[type="checkbox"]'))
        const indices = all.filter(c => c.checked).map(c => c.dataset.index)
        saveIngredientChecks(indices)
        cb.closest('.ingredient-check').classList.toggle('checked', cb.checked)
      })
    })
  }

  // Lightbox ---------------------------------------------------------------
  function openLightbox() {
    if (!lightbox || !lightboxImg || !heroImg) return
    lightboxImg.src = heroImg.src
    lightbox.classList.add('open')
    lightbox.setAttribute('aria-hidden', 'false')
  }
  function closeLightbox() {
    if (!lightbox) return
    lightbox.classList.remove('open')
    lightbox.setAttribute('aria-hidden', 'true')
  }
  if (heroImg) {
    heroImg.style.cursor = 'zoom-in'
    heroImg.addEventListener('click', openLightbox)
  }
  if (lightbox) {
    lightbox.addEventListener('click', (e) => { if (e.target === lightbox || e.target === lightboxClose) closeLightbox() })
  }
  if (lightboxClose) lightboxClose.addEventListener('click', closeLightbox)
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && lightbox && lightbox.classList.contains('open')) closeLightbox()
  })

  // Substitutions & tweaks -------------------------------------------------
  function renderSubstitution() {
    const saved = Lists.getSubstitution(recipeId)
    if (!savedSubstitutionEl) return
    savedSubstitutionEl.textContent = saved
    savedSubstitutionEl.style.display = saved ? 'block' : 'none'
  }
  function saveSubstitution() {
    if (!substitutionInput) return
    Lists.setSubstitution(recipeId, substitutionInput.value)
    renderSubstitution()
  }
  if (substitutionInput) {
    substitutionInput.value = Lists.getSubstitution(recipeId)
    let subTimer = null
    substitutionInput.addEventListener('input', () => {
      clearTimeout(subTimer)
      subTimer = setTimeout(saveSubstitution, 400)
    })
    substitutionInput.addEventListener('blur', saveSubstitution)
  }
  if (saveSubstitutionBtn) saveSubstitutionBtn.addEventListener('click', saveSubstitution)
  if (removeSubstitutionBtn) {
    removeSubstitutionBtn.addEventListener('click', () => {
      if (substitutionInput) substitutionInput.value = ''
      Lists.setSubstitution(recipeId, '')
      renderSubstitution()
    })
  }

  // Video links --------------------------------------------------------------
  function isValidUrl(url) {
    return /^https?:\/\//i.test((url || '').trim())
  }
  function renderVideo(url) {
    if (!videoActions || !watchVideoLink) return
    if (url && isValidUrl(url)) {
      watchVideoLink.href = url
      videoActions.style.display = ''
    } else {
      videoActions.style.display = 'none'
    }
  }
  function saveVideo() {
    if (!videoInput) return
    const url = videoInput.value.trim()
    if (url && !isValidUrl(url)) {
      showToast('Please enter a valid http(s) URL')
      return
    }
    Lists.setVideoLink(recipeId, url)
    renderVideo(url)
    showToast(url ? 'Video link saved' : 'Video link removed')
  }
  if (videoInput) {
    videoInput.value = Lists.getVideoLink(recipeId)
    renderVideo(videoInput.value)
  }
  if (saveVideoBtn) saveVideoBtn.addEventListener('click', saveVideo)
  if (videoInput) videoInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') saveVideo() })
  if (removeVideoBtn) {
    removeVideoBtn.addEventListener('click', () => {
      if (videoInput) videoInput.value = ''
      Lists.setVideoLink(recipeId, '')
      renderVideo('')
    })
  }

  // Nutrition estimate -----------------------------------------------------
  async function loadNutrition() {
    const badge = document.getElementById('nutrition-badge')
    if (!badge) return
    const db = badge.dataset.db || 'cookster.db'
    if (nutritionAbortController) nutritionAbortController.abort()
    nutritionAbortController = new AbortController()
    try {
      const res = await fetch(`/api/nutrition/${encodeURIComponent(recipeId)}?db=${encodeURIComponent(db)}`, { signal: nutritionAbortController.signal })
      if (!res.ok) throw new Error('nutrition failed')
      const data = await res.json()
      if (data.estimated_calories && data.estimated_calories > 0) {
        badge.textContent = `🔥 ${data.estimated_calories} kcal / serving`
        badge.classList.remove('empty')
      } else {
        badge.textContent = 'No nutrition estimate available'
        badge.classList.add('empty')
      }
    } catch (err) {
      if (err.name === 'AbortError') return
      console.error('[cookster] nutrition error:', err)
      badge.textContent = 'No nutrition estimate available'
      badge.classList.add('empty')
    }
  }

  loadRelated()
  updateFav()
  updateWant()
  renderRating()
  renderCooked()
  renderSubstitution()
  initIngredientChecks()
  loadNutrition()
  Lists.addRecentView(recipeId)
})()
