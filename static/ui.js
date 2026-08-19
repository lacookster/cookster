// Cookster shared UI module — theme toggle and lists-panel drawer.
// Loaded on every page after lists.js so user-data helpers are available.

(function (root) {
  function hasAppJs() {
    return !!document.querySelector('script[src*="/static/app.js"]')
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme)
    document.querySelectorAll('#theme').forEach(btn => {
      btn.textContent = theme === 'dark' ? '☀️ Light' : '🌙 Dark'
    })
  }

  function initTheme() {
    const saved = localStorage.getItem('theme') || 'light'
    applyTheme(saved)

    // index.html loads app.js which already binds the theme button.
    if (hasAppJs()) return

    document.querySelectorAll('#theme').forEach(btn => {
      btn.addEventListener('click', () => {
        const isDark = document.documentElement.getAttribute('data-theme') === 'dark'
        const next = isDark ? 'light' : 'dark'
        applyTheme(next)
        localStorage.setItem('theme', next)
      })
    })
  }

  function initListsPanel() {
    const listsPanel = document.getElementById('lists-panel')
    const listsBackdrop = document.getElementById('lists-backdrop')
    const listsToggle = document.getElementById('lists-toggle')
    const listsClose = document.getElementById('lists-close')
    if (!listsPanel) return

    // index.html loads app.js which already binds the lists panel toggles.
    if (hasAppJs()) return

    function openListsPanel() {
      listsPanel.classList.add('open')
      if (listsBackdrop) listsBackdrop.classList.add('open')
      listsPanel.setAttribute('aria-hidden', 'false')
      if (root.CooksterLists) {
        const data = root.CooksterLists.load()
        const favCount = document.getElementById('fav-count')
        const wantCount = document.getElementById('want-count')
        if (favCount) favCount.textContent = data.favorites.length
        if (wantCount) wantCount.textContent = data.wantToTry.length
      }
    }

    function closeListsPanel() {
      listsPanel.classList.remove('open')
      if (listsBackdrop) listsBackdrop.classList.remove('open')
      listsPanel.setAttribute('aria-hidden', 'true')
    }

    if (listsToggle) listsToggle.addEventListener('click', openListsPanel)
    if (listsClose) listsClose.addEventListener('click', closeListsPanel)
    if (listsBackdrop) listsBackdrop.addEventListener('click', closeListsPanel)
  }

  function init() {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', () => {
        initTheme()
        initListsPanel()
      })
    } else {
      initTheme()
      initListsPanel()
    }
  }

  init()

  root.CooksterUi = { applyTheme }
})(window)
