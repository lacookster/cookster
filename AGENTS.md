# Cookster — Agent Guide

This file is written for AI coding agents that need to understand, modify, or extend the Cookster project. It covers the architecture, tech stack, important conventions, security model, and common workflows. Read this before making non-trivial changes.

## Project overview

Cookster is a local web app for indexing EPUB, PDF, and MOBI cookbooks and searching their recipes. It:

- Extracts recipes from books in `books/` using heuristics and book-specific HTML extractors.
- Writes one JSON file per book into `data/recipes/` for inspection and incremental re-indexing.
- Loads recipes into a SQLite database (`cookster.db` by default) with an optional FTS5 full-text search table.
- Serves a FastAPI backend and a vanilla-JS frontend.
- Supports favourites, custom lists, shopping lists, meal plans, notes, ratings, "I cooked this" history, and approximate nutrition estimates.

All source cookbooks and generated data live locally. The intended runtime is a single developer/self-hosted machine or a small container.

## Tech stack

- **Python**: 3.11 (see `runtime.txt`).
- **Web framework**: FastAPI + Jinja2 templates + Uvicorn.
- **Database**: SQLite (main index `cookster.db`; user data `cookster_user_data.db`).
- **Full-text search**: SQLite FTS5 when available, with a fallback scan.
- **Search ranking**: `rank-bm25` with title boosting and a small synonym map from `thesaurus.json`.
- **EPUB parsing**: `ebooklib` + `BeautifulSoup` (`lxml`).
- **PDF parsing**: `pypdf`.
- **MOBI parsing**: `mobi` package, unpacked to HTML/EPUB and run through the normal EPUB pipeline.
- **Frontend**: vanilla JS, Jinja2 HTML templates, CSS with light/dark theme variables. It is a simple PWA with `manifest.json` and `sw.js`.
- **Testing**: `pytest` using FastAPI's `TestClient`.
- **Deployment**: Dockerfile + Heroku-style `Procfile`/`runtime.txt`; CI via GitHub Actions.

## Repository layout

```
.
├── api.py                  # FastAPI app, routes, auth, DB helpers, search logic
├── indexer.py              # EPUB/PDF/MOBI extraction, JSON pipeline, DB loading
├── ranking.py              # BM25 ranking + stopwords + synonym expansion
├── search.py               # CLI search utility against a DB
├── run_api.py              # Dev server entry point (uvicorn with reload=True)
├── run_index.py            # CLI indexer entry point
├── watch_books.py          # Polling watcher that rebuilds the index on changes
├── templates/              # Jinja2 HTML pages
│   ├── index.html          # Search homepage
│   ├── recipe.html         # Recipe detail page
│   ├── books.html          # Browse all books
│   ├── book.html           # Single-book recipe list
│   ├── collections.html    # Curated collections landing page
│   ├── login.html          # Password login
│   └── offline.html        # PWA offline page
├── static/                 # Static assets served under /static
│   ├── style.css           # All UI styling
│   ├── app.js              # Search page logic
│   ├── recipe.js           # Recipe detail page logic
│   ├── lists.js            # localStorage + server-synced user data module
│   ├── manifest.json       # PWA manifest
│   └── sw.js               # Service worker
├── books/                  # New/queued EPUB/PDF/MOBI files (ignored by git)
├── books/added/            # Already-indexed source files (ignored by git)
├── data/recipes/           # Preprocessed JSON per book (ignored by git)
├── static/epub_images/     # Extracted EPUB images (ignored by git)
├── cookster.db             # Main SQLite index (ignored by git)
├── cookster_user_data.db   # Server-side user data (ignored by git)
├── thesaurus.json          # Ingredient/locale synonym map
├── requirements.txt        # Pinned Python dependencies
├── Dockerfile              # Container build
├── Procfile                # Heroku process definition
├── runtime.txt             # Heroku Python version
├── .github/workflows/      # CI workflow
├── tests/                  # pytest suite
│   ├── test_api.py
│   ├── test_indexer.py
│   ├── test_auth.py
│   ├── test_search_ranking.py
│   ├── test_thesaurus.py
│   ├── test_pdf_extraction.py
│   ├── test_ui_static.py
│   └── test_user_data.py
└── scripts/                # Utility scripts
    ├── compress_epub_images.py
    └── push_epub_images_batches.py
```

## Architecture and data flow

```
books/  ──►  indexer.py  ──►  data/recipes/*.json
                                  │
                                  ▼
                    index_preprocessed_dir()  ──►  cookster.db
                                  │
                                  ▼
            FastAPI (api.py)  ◄──  ranking.py (BM25)
                      │
                      ▼
        Jinja2 templates + static/ JS/CSS
```

1. **Extraction**: `indexer.preprocess_dir()` walks `books/` recursively, extracts recipes, writes one JSON file per book to `data/recipes/`, and saves EPUB images to `static/epub_images/<slug_hash>/`.
2. **DB load**: `indexer.index_preprocessed_dir()` creates/updates `cookster.db`, populates `recipes`, maintains an FTS5 virtual table `recipes_fts` when supported, and tracks state in `book_index_log`.
3. **Search**: `api.py` uses FTS5 to fetch candidate IDs where possible, then calls `ranking.rank_recipes()` for BM25 scoring with title boosting.
4. **Serving**: HTML pages are rendered server-side; interactivity is added by the vanilla JS files. Static assets (including extracted images) are served under `/static`.

## Key concepts and conventions

### Stable IDs are the long-lived recipe identifier

Recipes have both an auto-increment integer `id` and a `stable_id` (truncated SHA-256 of `title + source + ingredients + steps`).

- Use `stable_id` for URLs, bookmarks, favourites, lists, and any client-side references.
- API endpoints accept **either** an integer ID or `stable_id` in paths like `/recipe/{id}` and `/download/{id}`.
- The DB has a unique index on `stable_id`.
- `api._ensure_schema()` adds `stable_id` (and `serves`/`image`) to older DBs and backfills missing values on first API access.
- `indexer._stable_id()` computes the same hash as `api.compute_stable_id()`.

### Book directories

- `books/` — new EPUB/PDF/MOBI cookbooks waiting to be indexed.
- `books/added/` — already-indexed source files. The indexer walks `books/` recursively, so both locations are discovered automatically.
- Workflow: drop a new book into `books/`, run `run_index.py`, verify quality, then move the source file into `books/added/`. Moving the file does **not** require re-indexing because the DB stores the relative `file_path`.

### User data persistence

Favourites, custom lists, shopping list, meal plan, notes, ratings, cooked history, substitutions, and video links are persisted in two places:

1. **Server-side SQLite** in `cookster_user_data.db`, keyed by a long-lived `cookster_user` HTTP-only cookie (10-year expiry, **not** cleared on logout).
2. **Browser localStorage** as a fast cache. Key: `cookster_lists_v3` (legacy keys `cookster_lists_v2` and `cookster_lists` are migrated on first read).

Endpoints:

- `GET /api/user-data` — fetch server blob.
- `POST /api/user-data` — save server blob.
- `GET /api/user-data/export` — export recovery token + data.
- `POST /api/user-data/import` — adopt another token/data.
- `POST /api/user-data/reset` — delete server-side data and clear the user cookie.

### Image handling

- EPUB images are extracted to `static/epub_images/<slug_hash>/` and served as static files.
- The DB `image` column stores a relative path; `api._image_path_to_url()` converts it to a `/static/...` URL.
- `_find_image_for_doc()` first looks for `<img>` tags inside the recipe's own HTML document, then scans up to three neighbouring short (≤80 chars of text) documents in both directions for image-only interleaving pages.
- Decorative images (`vector.png`, `line.jpg`, tiny spacers) are skipped.
- PDFs and MOBIs currently have no images extracted.
- `preprocess_dir()` removes stale image directories when their source book is deleted or renamed.

### Search details

- FTS5 table `recipes_fts(title, ingredients, steps)` pre-filters candidates when present.
- If FTS5 is unavailable, the API falls back to a full scan and applies `_candidate_matches()` (positive/negative tokens must appear in title/ingredients/steps).
- User queries are sanitized for FTS5 (`_sanitize_fts_query()`) and tokenized in `ranking.py`.
- `ranking.py` removes stopwords, expands synonyms from `thesaurus.json`, and boosts title matches.
- Search supports `filter` chips: `vegetarian`, `vegan`, `gluten-free`, `nut-free`, `dessert`, `one-pot`, `quick`, `breakfast`, `lunch`, `dinner`, `side`, `snack`.
- Search supports negative keywords (`chicken -soup`), the `exclude` parameter (comma-separated), the `have` parameter for "what can I make?" mode, and the `pantry` parameter for a small relevance boost.
- Sort options: `relevance` (default), `az`, `recent`, `random`.

### Authentication

- Simple password login. Password is read from `COOKSTER_PASSWORD`; if unset, a default is used. Always set `COOKSTER_PASSWORD` for production.
- Session cookie `cookster_session` is HTTP-only, `SameSite=Lax`, 7 days.
- Middleware redirects unauthenticated HTML requests to `/login` and returns `401` for `/api/*`.
- Public paths: `/login`, `/logout`, `/favicon.ico`, `/static/*`.
- `COOKSTER_SECRET` signs the session cookie; if unset a random secret is generated at startup (sessions do not survive restart in that case).

### Path sandboxing

- `api.resolve_db_path()` resolves the `db` query parameter to a path inside `api.DB_DIR` (the project root). It rejects `..`, absolute paths outside the project, and empty paths.
- `api.resolve_download_path()` resolves stored `file_path` values for downloads, but only allows files inside `api.BOOKS_DIR` (which includes `books/` and `books/added/`).
- Tests monkeypatch `api.DB_DIR` and `api.BOOKS_DIR` to temp directories.

## Build, run, and index commands

### Install dependencies

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate
pip install -r requirements.txt
```

### Run tests

```bash
python -m pytest tests/ -q
```

### Build or update the index

Incremental (only changed/new books):

```bash
python run_index.py --books-dir books --recipes-dir data/recipes --db cookster.db
```

Force a full rebuild (re-parse all source files and reload the DB):

```bash
python run_index.py --books-dir books --recipes-dir data/recipes --db cookster.db --force
```

### Watch the books folder

```bash
# Poll every 10 seconds
python watch_books.py

# One pass and exit
python watch_books.py --once
```

It is safe to run alongside `run_api.py` because the database uses WAL mode.

### Run the web server

Development:

```bash
python run_api.py
# Opens http://127.0.0.1:8000 with auto-reload
```

Production/Docker:

```bash
uvicorn api:app --host 0.0.0.0 --port 8000
```

Environment variables:

- `COOKSTER_PASSWORD` — login password.
- `COOKSTER_SECRET` — session cookie signing secret.
- `PORT` — port to bind (used by `Procfile`/`Dockerfile`).

### Search from the command line

```bash
python search.py --db cookster.db --query "chicken" --limit 10
```

### Trigger indexing from the UI/API

```bash
# Check status
curl http://127.0.0.1:8000/api/index/status

# Start an incremental re-index
curl -X POST http://127.0.0.1:8000/api/index/start

# Force a full rebuild
curl -X POST "http://127.0.0.1:8000/api/index/start?force=true"
```

## API endpoints

HTML pages:

- `GET /` — search page.
- `GET /login`, `POST /login` — login form.
- `GET /logout` — clear session.
- `GET /collections` — curated collections.
- `GET /books` — browse indexed books.
- `GET /book?source=...` — single-book recipe list.
- `GET /recipe/{id_or_stable_id}` — recipe detail.
- `GET /offline` — offline page.

JSON API:

- `GET /search?q=...&db=...&limit=...&page=...&source=...&filters=...&sort=...&pantry=...&exclude=...&have=...`
- `GET /api/sources?db=...` — distinct source books.
- `GET /api/stats?db=...` — recipe/book counts.
- `GET /api/recipes?ids=...&db=...` — batch recipe summaries.
- `GET /api/suggest?q=...&db=...` — title autocomplete.
- `GET /api/suggest-correction?q=...&db=...` — spelling suggestion.
- `GET /api/random?db=...` — random recipe.
- `GET /api/related/{stable_id}?db=...` — same-book + similar recipes.
- `GET /api/recipes-by-source?source=...&db=...` — paginated recipes from one book.
- `GET /api/new-books?db=...` — recently indexed books.
- `GET /api/nutrition/{id_or_stable_id}?db=...` — approximate kcal per serving.
- `GET|POST /api/user-data*` — user data CRUD/export/import/reset.
- `GET /api/index/status`, `POST /api/index/start` — background indexing control.

Download/export:

- `GET /download/{id_or_stable_id}?db=...` — exports the recipe as a Markdown file (`text/markdown`).

## Testing strategy

- Run the full suite with `python -m pytest tests/ -q`.
- `test_api.py` covers endpoints, path sandboxing, stable IDs, pagination, filters, and background indexing status.
- `test_indexer.py` covers book-specific extractors, image helpers, incremental indexing, and extractor registry invariants.
- Other test files cover auth, ranking/thesaurus, PDF extraction, static UI behaviour, and user data.
- Tests create temporary DBs and monkeypatch `api.DB_DIR`/`api.BOOKS_DIR` so they do not touch the real `cookster.db` or `books/`.
- The `autouse` fixture in `test_api.py` bypasses auth so endpoint tests run as an authenticated user.

## Deployment

### Docker

The `Dockerfile` builds a Python 3.11 slim image, installs dependencies, downloads NLTK wordnet data, copies the project, and runs:

```bash
uvicorn api:app --host 0.0.0.0 --port ${PORT:-8000}
```

Expose port `8000`.

### Heroku

- `runtime.txt` pins Python 3.11.
- `Procfile` runs `uvicorn api:app --host 0.0.0.0 --port $PORT`.
- Set `COOKSTER_PASSWORD` and `COOKSTER_SECRET` via config vars.

### GitHub Actions

`.github/workflows/python-app.yml` runs on `push`/`pull_request` to `main`/`master`:

1. Sets up Python 3.11.
2. Installs `requirements.txt`.
3. Runs `pytest -q`.

## Security considerations

- **Set `COOKSTER_PASSWORD` and `COOKSTER_SECRET` in production.** The defaults are only for local development.
- The `db` query parameter is sandboxed to `api.DB_DIR`. Never remove or loosen `resolve_db_path()`.
- Downloads are sandboxed to `api.BOOKS_DIR`. `/download/{id}` exports Markdown, not the raw source file, and only uses the stored path for display/validation.
- User data cookies are HTTP-only and `SameSite=Lax`; mark `secure=True` if serving over HTTPS.
- Do not commit the books, generated DBs, JSON recipes, or extracted images. They are listed in `.gitignore`.

## Development conventions

- Keep backend logic in `api.py`, `indexer.py`, and `ranking.py`. Avoid putting business logic in templates.
- Frontend modules attach to `window` (e.g., `window.CooksterLists`).
- Escape user-facing strings before injecting HTML. The frontend has a local `escapeHtml()` helper; the backend uses Jinja2 autoescaping plus `html.escape()` where needed.
- CSS uses CSS variables for theming; consider both light and dark modes when adding styles.
- Handle older DBs gracefully: many API helpers wrap optional `image`/`serves`/`stable_id` column reads in `try/except sqlite3.OperationalError`.
- Prefer `stable_id` for any new feature that identifies a recipe across re-indexing.
- Book-specific extractors are registered in `indexer.py` via `register_extractor()`. Register them **before** the generic extractors (`paragraph`, `heading`, `fallback`). Once a book-specific extractor's predicate matches, generic extractors are skipped for that file, so each book-specific extractor must handle its own front matter/indices or return empty results for them.

## Common gotchas

- **No results unexpectedly**: check whether `recipes_fts` exists. Without it, the fallback requires query tokens to appear in the recipe text.
- **Favourites/lists disappear after re-index**: ensure `stable_id` is populated and the localStorage key is `cookster_lists_v3`.
- **Images not showing**: verify `static/epub_images/` exists and the DB `image` value maps through `_image_path_to_url()`.
- **Download fails**: the stored `file_path` must resolve inside `books/`. Re-index if you moved source files.
- **Indexer returns empty recipes for a known book**: check whether a book-specific extractor is registered and whether its predicate matches the filename. Adding a new extractor is usually easier than fixing the generic fallback for one badly formatted EPUB.
- **Large image folders**: `static/epub_images/` is ignored by git. If you need to commit images to GitHub, run `scripts/compress_epub_images.py` first and push in batches with `scripts/push_epub_images_batches.py`.

## Useful one-liners

```bash
# Quick API smoke test
python -c "from api import app; print('ok')"

# Count recipes in the local DB
python -c "import sqlite3; print(sqlite3.connect('cookster.db').execute('SELECT COUNT(*) FROM recipes').fetchone()[0])"

# List indexed books
python -c "import sqlite3; import api; [print(api._clean_source(r[0])) for r in sqlite3.connect('cookster.db').execute('SELECT DISTINCT source FROM recipes') if r[0]]"
```
