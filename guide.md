# Cookster Agent Guide

This guide is for anyone (or any agent) picking up the Cookster project to fix bugs, add features, or refactor. It explains the architecture, key files, conventions, and how to make common changes safely.

## What Cookster is

Cookster is a local web app for indexing EPUB, PDF and MOBI cookbooks and searching their recipes. It:

1. Extracts recipes from books in `books/` using heuristics and book-specific extractors.
2. Stores them as JSON in `data/recipes/`.
3. Loads them into a SQLite database (`cookster.db` by default).
4. Serves a FastAPI backend and a vanilla-JS frontend.
5. Lets users search by ingredient/recipe name, save favourites, create custom lists, build shopping lists and meal plans, and view/download recipes.

All source cookbooks and generated data live locally. The intended runtime is a single developer/self-hosted machine or a small container.

## Feature highlights

- **Search**: full-text search with BM25 ranking, source filtering, negative filters (`chicken -soup`), autocomplete, spelling suggestions, and "use what I have" mode.
- **Browse by book**: click a book title on any recipe card to see every recipe from that book.
- **Collections**: curated recipe collections (e.g. vegetarian, quick meals) on a dedicated landing page.
- **Lists & favourites**: save recipes to Favourites, Want-to-try, or custom lists (Lists panel).
- **Shopping list**: add ingredients from any recipe, group by aisle, merge duplicates, and copy/download the list.
- **Meal planner**: plan recipes on specific dates and drag them between days.
- **Pantry**: save staples you always have and optionally boost matching recipes.
- **Recipe extras**: star ratings, personal notes, substitutions, video links, "I cooked this" history, serving scaler, unit converter, and full-screen cooking mode with timers and voice commands.
- **Share & export**: copy a stable link to any recipe or download it as Markdown.
- **Backup/restore**: export/import all local data from the Backup tab, or link devices with a recovery code.
- **PWA**: installable app with a service worker, offline fallback, and image caching.
- **Incremental indexing**: the indexer skips unchanged books; a watcher can auto-index new EPUBs.

## Tech stack

- **Backend**: Python 3.11, FastAPI, SQLite (+ FTS5 when available), Jinja2 templates.
- **Search ranking**: `rank-bm25` with title boosting + a small synonym map from `thesaurus.json`.
- **EPUB parsing**: `ebooklib` + `BeautifulSoup` (`lxml`).
- **PDF parsing**: `pypdf`.
- **MOBI parsing**: `mobi` (unpacked to HTML/EPUB and run through the normal EPUB pipeline).
- **Image processing**: `Pillow` for compressing EPUB images to JPEG.
- **Frontend**: Vanilla JS, Jinja2 templates, CSS variables for light/dark themes, PWA (`manifest.json` + `sw.js`).
- **Tests**: `pytest` with FastAPI's `TestClient`.
- **Server**: `uvicorn` (development with `reload=True`).

## Repository layout

```
.
├── api.py                  # FastAPI app, endpoints, auth, DB query helpers
├── indexer.py              # EPUB/PDF/MOBI extraction, JSON pipeline, DB loading
├── ranking.py              # BM25 recipe ranking + synonyms
├── search.py               # CLI search against a DB
├── run_api.py              # Dev server entry point
├── run_index.py            # CLI indexer entry point
├── watch_books.py          # Polling watcher that rebuilds the index on changes
├── templates/
│   ├── index.html          # Search page
│   ├── recipe.html         # Recipe detail page
│   ├── books.html          # Browse all books
│   ├── book.html           # Single-book recipe list
│   ├── collections.html    # Curated collections
│   ├── login.html          # Password login
│   └── offline.html        # PWA offline page
├── static/
│   ├── style.css           # All UI styling
│   ├── app.js              # Search page logic
│   ├── recipe.js           # Recipe detail page logic
│   ├── lists.js            # User-data module (localStorage + server sync)
│   ├── ui.js               # Shared UI (theme, lists panel, mobile nav)
│   ├── icons.js            # Inline SVG icon library
│   ├── sw.js               # Service worker
│   └── manifest.json       # PWA manifest
├── books/                  # New EPUB/PDF/MOBI files waiting to be indexed (not committed)
├── books/added/            # Already-indexed source files (not committed)
├── data/recipes/           # Preprocessed JSON per book (not committed)
├── static/epub_images/     # Extracted EPUB images (not committed)
├── cookster.db             # SQLite database (not committed)
├── cookster_user_data.db   # Server-side user data (not committed)
├── tests/                  # pytest test suite
├── thesaurus.json          # Ingredient/locale synonym map
├── pytest.ini              # pytest configuration (pythonpath, testpaths)
└── requirements.txt        # Pinned Python dependencies
```

## How to get set up

1. Create a virtual environment and install dependencies:

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate
pip install -r requirements.txt
```

2. Run tests to confirm everything works:

```bash
python -m pytest -q
```

3. (Optional) Put EPUB/PDF/MOBI files in `books/` and build the index:

```bash
python run_index.py --books-dir books --db cookster.db
```

4. Run the web server:

```bash
python run_api.py
# Open http://127.0.0.1:8000
```

## Architecture

```
books/  ──►  indexer.py  ──►  data/recipes/*.json
                                  │
                                  ▼
                    index_preprocessed_dir  ──►  cookster.db
                                  │
                                  ▼
            FastAPI (api.py)  ◄──  ranking.py (BM25)
                      │
                      ▼
        Jinja2 templates + static/ JS/CSS
```

### Backend flow

- `indexer.py` walks `books/` recursively, extracts recipes, and writes one JSON file per book to `data/recipes/`.
- EPUB images referenced by recipe documents are compressed to JPEG and saved to `static/epub_images/<slug_hash>/`.
- `indexer.index_preprocessed_dir()` creates/updates the SQLite DB, loads the JSON recipes, and populates an optional FTS5 table.
- `api.py` exposes endpoints. Search uses FTS5 to find candidate IDs when available, then ranks with `ranking.py`.
- Recipe detail pages and downloads are served directly from the DB.
- SQLite connections are pooled per-database for performance.

### Frontend flow

- Every page loads `lists.js`, then `ui.js`, then page-specific JS (`app.js` for search, `recipe.js` for recipes), then `icons.js`.
- `lists.js` owns all user-data state (favourites, lists, shopping, meal plan, notes, ratings, etc.). It keeps an in-memory cache, syncs to the server, and emits `cookster-lists-changed` events.
- `ui.js` initialises theme toggles, lists-panel drawer, mobile-nav active state, and SVG icon injection.
- `icons.js` exposes `window.CooksterIcons` and replaces `[data-icon]` elements with inline SVGs.
- `app.js` handles search, pagination, source filtering, autocomplete, favourites, lists, and URL state.
- `recipe.js` handles the recipe page actions, cooking mode, and related recipes.
- `sw.js` provides offline caching for the app shell, pages, and EPUB images.

## Key concepts and gotchas

### Stable IDs (very important)

Recipes have a **stable_id**: a truncated SHA-256 hash of `title + source + ingredients + steps`. This survives DB re-indexing so that favourites and lists don't break when the DB is rebuilt.

- The DB has an `INTEGER PRIMARY KEY` `id` column (auto-increment) and a `stable_id TEXT` column with a unique index.
- API endpoints accept **either** an integer ID or a `stable_id` in URL paths like `/recipe/{id}` and `/download/{id}`.
- Frontend links and user-data keys use `stable_id`.
- `_ensure_schema()` in `api.py` adds the `stable_id` column if missing and backfills `NULL` values on first API access.
- When re-indexing, `indexer.py` computes the same `stable_id` algorithm as `api.compute_stable_id()`.

**Rule**: when adding a new feature that identifies a recipe, use `stable_id`. Never use integer `id` for long-lived references (URLs, localStorage, bookmarks).

### User data persistence

Favourites, custom lists, shopping list, meal plan, notes, ratings, cooked history, substitutions and video links are persisted in two places:

1. **Server-side SQLite** in `cookster_user_data.db`, keyed by a long-lived `cookster_user` HTTP-only cookie (10-year expiry, **not** cleared on logout).
2. **Browser localStorage** as a fast cache. Key: `cookster_lists_v3` (legacy keys `cookster_lists_v2` and `cookster_lists` are migrated on first read).

`lists.js` keeps an in-memory cache of the parsed data so repeated lookups (`isFavorite`, `isInList`, etc.) don't re-parse JSON on every call. Changes are debounced and POSTed to `/api/user-data` with exponential-backoff retries.

Endpoints:

- `GET /api/user-data` – fetch server blob.
- `POST /api/user-data` – save server blob.
- `GET /api/user-data/export` – export recovery token + data.
- `POST /api/user-data/import` – adopt another token/data.
- `POST /api/user-data/reset` – delete server-side data and clear the user cookie.

### Book directories

- **`books/`** – new EPUB/PDF/MOBI cookbooks waiting to be indexed.
- **`books/added/`** – already-indexed source files. The indexer walks `books/` recursively, so both locations are discovered automatically.

**Workflow:**

1. Drop new EPUB/PDF/MOBI files into `books/`.
2. Run `run_index.py` to parse them and add their recipes to the database.
3. Move the successfully indexed source files from `books/` into `books/added/`.

You do **not** need to rebuild the database after moving a book into `books/added/`.

### Book title cleaning

`api._clean_source()` converts raw source filenames into display-friendly book titles. It strips:

- Anna's Archive metadata tails (` -- Author -- Place, Year -- isbn13 ...`).
- `libgen.li` / `libgen` markers.
- Curly-brace metadata like `{Smith, Delia}{112392336}`.
- Trailing `(year, publisher)` tags.
- PDFDrive suffixes.
- Known book extensions (`.epub`, `.pdf`, `.mobi`, `.azw3`, `.azw`, `.txt`), including double extensions.
- Mojibake (`�` → `'`, `&amp_` → `&`).

It also normalises underscores used as separators (`Title_ Subtitle` → `Title: Subtitle`). If you add a new source of books with different filename junk, extend `_clean_source()` and add a test in `tests/test_api.py`.

### Security model

- The `db` query parameter is sandboxed: paths must resolve inside `api.DB_DIR` (project root by default). `..` and absolute paths outside the project are rejected.
- `/download/{id}` resolves the stored `file_path` against the project root and only permits downloads inside `api.BOOKS_DIR` (which includes `books/` and `books/added/`). Paths outside `books/` are rejected.
- Tests monkeypatch `api.DB_DIR` and `api.BOOKS_DIR` to temp directories so they can still test.
- Authentication is a simple password login. Set `COOKSTER_PASSWORD` and `COOKSTER_SECRET` in production.

### FTS5 fallback

- If the `recipes_fts` virtual table exists, search pre-filters candidates with it.
- If not, the API falls back to a full-table scan and filters results by whether query tokens actually appear in the candidate text.
- Query text is sanitized before FTS5 `MATCH` to avoid syntax errors from special characters.

### Image handling

- Only EPUB images referenced by recipe documents are saved; decorative images are skipped.
- Images are compressed to JPEG (quality ~75) before being written to `static/epub_images/<slug_hash>/`.
- The DB stores an `image` column with a project-relative path; `_image_path_to_url()` converts it to a `/static/...` URL.
- For generic extractors, per-recipe image mapping is used so each recipe gets the correct image from its own document or neighbouring image-only pages.
- `preprocess_dir()` removes orphaned image directories when their source book is deleted or renamed.

## Common changes

### Add a new API endpoint

1. Add the route in `api.py`.
2. Use `resolve_db_path(db)` for DB access.
3. Use `_ensure_schema(conn)` if you need the `stable_id` column to exist.
4. Handle DBs without the `image` column with a `try/except sqlite3.OperationalError` pattern.
5. Add a test in `tests/test_api.py` and set `api.DB_DIR` to the temp directory.
6. Run `python -m pytest -q`.

### Add a frontend feature on the search page

1. Add the markup in `templates/index.html`.
2. Add logic in `static/app.js`.
3. Add styles in `static/style.css`.
4. Keep URL state in `syncUrl()` so back/forward/refresh work.
5. Use SVG icons via `<span class="icon" data-icon="name"></span>` and call `CooksterIcons.initIcons(container)` after injecting HTML.
6. Run tests and do a manual browser check.

### Add a frontend feature on the recipe page

1. Add markup in `templates/recipe.html`.
2. Add logic in `static/recipe.js`.
3. Add styles in `static/style.css`.
4. Remember `recipe.stable_id` is available in the template.

### Add a new book-specific extractor

`indexer.py` uses a small pluggable extractor registry (`register_extractor()`). Existing book-specific extractor cores include:

- `_extract_30min_meals`
- `_extract_every_grain_recipes`
- `_extract_one_pan_wonders_recipes`
- `_extract_gordon_ramsay_recipes`
- `_extract_simply_japanese_recipes`
- `_extract_plenty_more_recipes`
- `_extract_flavour_recipes`
- `_extract_plenty_recipes`
- `_extract_veganomicon_recipes`
- `_extract_french_provincial_recipes`
- `_extract_delias_cakes_recipes`
- `_extract_good_things_recipes`
- `_extract_everyday_super_food_recipes`
- `_extract_jamie_veg_recipes`
- `_extract_seven_fires_recipes`
- `_extract_cocolat_recipes`
- `_extract_kitchen_diaries_recipes`
- `_extract_nigella_how_to_eat_recipes`
- `_extract_nigella_domestic_goddess_recipes`

1. Write an extractor function. It should accept `(soup, epub_path)` and return a list of dicts with keys `title`, `ingredients`, `steps`, `source`, `file_path`, and optionally `serves`:

   ```python
   def _extract_my_book(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
       ...
       return [{
           'title': title,
           'ingredients': ingredients,
           'steps': steps,
           'source': os.path.basename(epub_path),
           'file_path': epub_path,
           'image': '',
           'serves': serves,
       }]
   ```

2. Add a `_with_image` wrapper near the other wrappers (the dispatcher calls extractors with `(soup, epub_path, image_path)`):

   ```python
   def _extract_my_book_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
       recipes = _extract_my_book(soup, epub_path)
       for r in recipes:
           r['image'] = image_path
       return recipes
   ```

3. Register it near the bottom of `indexer.py` (order matters — the first extractor whose predicate returns `True` and returns non-empty recipes wins). Book-specific extractors should be registered **before** the generic `paragraph`/`heading`/`fallback` extractors:

   ```python
   register_extractor(
       _source_predicate('my book'),  # filename substring, or write a custom predicate
       _extract_my_book_with_image,
       'my book',
   )
   ```

   For content-based matching, use a custom predicate:

   ```python
   def _my_book_predicate(soup: BeautifulSoup, epub_path: str) -> bool:
       return bool(soup.select_one('.my-book-marker'))

   register_extractor(_my_book_predicate, _extract_my_book_with_image, 'my book')
   ```

4. The dispatcher adds the extracted recipe image automatically, so you don't need to set `image` yourself inside the extractor.
5. Re-run `python run_index.py --force` to rebuild the index with the new extractor.
6. Verify recipe counts/images in the UI.

**Important**: once a book-specific extractor's predicate matches a file, later generic extractors (`paragraph`, `heading`, `fallback`) are **not** run for that file. This prevents generic extractors from inserting garbage into books that have a dedicated extractor. Make sure the book-specific extractor handles every file for that book (returning an empty list for non-recipe pages like front matter, indices, etc.) or accept that those files will contribute no recipes.

### How image extraction works

- `_save_epub_images()` extracts images referenced by recipe documents and compresses them to JPEG in `static/epub_images/<slug_hash>/`.
- For each recipe document, `_find_image_for_doc()` first looks for `<img>` tags inside the document itself, then scans up to three preceding and three following short (≤80 characters of text) EPUB documents for image-only interleaving pages.
- Decorative images (`vector.png`, `line.jpg`, tiny spacers) are skipped.
- `preprocess_dir()` removes orphaned image directories when their source book is deleted or renamed, so `static/epub_images/` doesn't accumulate stale files.

### Improve search ranking

- Edit `ranking.py` for tokenization, stopwords, or synonym handling.
- Add entries to `thesaurus.json` for ingredient aliases (e.g., `aubergine -> eggplant`).
- `rank_recipes()` returns `[]` for empty candidate lists.

### Re-index from scratch

```bash
python run_index.py --books-dir books --db cookster.db --force
```

`--force` re-parses every EPUB and re-loads every book. Without `--force`, `run_index.py` is incremental:
- `preprocess_dir()` only re-parses a book if its JSON file is missing or older than the source EPUB.
- `index_preprocessed_dir()` only re-loads books whose JSON file has changed; unchanged books are skipped, and books that have been deleted are removed from the DB.

Stable IDs mean user favourites/lists will still point to the right recipes after a re-index.

### Watch the books folder for changes

`watch_books.py` polls the books directory and re-runs the incremental indexer whenever a file is added, removed, or changed.

```bash
# Run once and exit
python watch_books.py --once

# Poll every 10 seconds (default)
python watch_books.py

# Custom paths
python watch_books.py --books-dir books --recipes-dir data/recipes --db cookster.db --interval 30
```

Stop it with `Ctrl+C`. It is safe to run alongside `python run_api.py` because the database uses WAL mode.

### Trigger indexing from the web UI or another script

The API exposes background indexing endpoints. They run in a daemon thread so the site keeps serving requests.

- `GET /api/index/status` – current indexer state (`idle`/`running`/`complete`/`error`).
- `POST /api/index/start` – start a background re-index. Returns `409` if an index is already running.
- `GET /api/index/start` – convenience GET wrapper.

Examples:

```bash
# Check status
curl http://127.0.0.1:8000/api/index/status

# Start an incremental re-index
curl -X POST http://127.0.0.1:8000/api/index/start

# Force a full rebuild
curl -X POST "http://127.0.0.1:8000/api/index/start?force=true"
```

You can also call `indexer.index_preprocessed_dir(recipes_dir, db_path, force=False)` directly from Python when you only need to reload existing JSON files.

## Testing

```bash
# Run the full suite
python -m pytest -q

# Run a specific test
python -m pytest tests/test_api.py::test_search_endpoint -q
```

`pytest.ini` sets `pythonpath = .` and `testpaths = tests`, so running `pytest -q` directly also works (this is what CI uses).

Tests create temp DBs and monkeypatch `api.DB_DIR`/`api.BOOKS_DIR` so they don't touch the real `cookster.db` or `books/`. The `autouse` fixture in `test_api.py` bypasses auth so endpoint tests run as an authenticated user.

## CI/CD

`.github/workflows/python-app.yml` runs on `push`/`pull_request` to `main` or `master`:

1. Sets up Python 3.11.
2. Installs `requirements.txt`.
3. Runs `python -m pytest -q`.

The `pytest.ini` file is required so that `import api` works when pytest runs from the repo root without `python -m`.

## Conventions

- Keep backend logic in `api.py` / `indexer.py` / `ranking.py`. Avoid adding business logic to templates.
- Frontend modules attach to `window` (e.g., `window.CooksterLists`, `window.CooksterUi`, `window.CooksterIcons`).
- Use `escapeHtml()` before injecting user-facing strings into HTML.
- Use SVG icons via `data-icon` attributes instead of emojis for UI actions.
- CSS uses CSS variables for theming; both light and dark modes must be considered. Use the design tokens (`--shadow-*`, `--radius-*`, `--font-*`, `--ease-*`) where possible.
- Don't commit generated files (`cookster.db`, `data/recipes/`, `static/epub_images/`, `__pycache__`, etc.). They are in `.gitignore`.

## Image compression and batch push

Because `static/epub_images/` can grow very large, it is listed in `.gitignore` by default. If you need to commit the images to GitHub, compress them first and push in batches to stay under GitHub's ~2 GB pack-size limit.

### Scripts

- `scripts/compress_epub_images.py`: compresses every image in `static/epub_images/` in-place.
  - JPEGs are saved at quality 75 with progressive encoding.
  - PNGs are optimized; images with a small colour set are quantized to 256 colours.
  - GIFs are reduced to a 128-colour palette.
  - Original filenames and formats are preserved so existing DB `image` URLs remain valid.

- `scripts/push_epub_images_batches.py`: adds the directories under `static/epub_images/` to git in ~250 MB batches, commits each batch, and pushes it to `origin/master`. It uses `git add -f` because the folder is ignored.

### Procedure used for the 2025-08-14 upload

```bash
# 1. Restore images if they are not in the working tree
# (they existed in commit 9635d45 before static/epub_images was ignored)
git checkout 9635d45 -- static/epub_images

# 2. Compress
python scripts/compress_epub_images.py

# 3. Push in batches
python scripts/push_epub_images_batches.py
```

Results of that run:

- 9,326 images
- Original size: 2.93 GB
- Compressed size: 1.33 GB
- Space saved: 54.5%
- Pushed in 7 batches (41 book directories), each under 250 MB.

## Troubleshooting

- **Search returns 0 results unexpectedly**: Check whether the DB has the `recipes_fts` table. Without it, the fallback requires query tokens to actually appear in the recipe text.
- **Favourites/lists disappear after re-index**: Ensure `stable_id` was populated and the localStorage key is `cookster_lists_v3`. Old integer-based lists were abandoned intentionally.
- **Images not showing**: Check that `static/epub_images/` exists and the DB `image` column contains a relative path that `_image_path_to_url()` can convert.
- **Download fails with 400**: The stored `file_path` must resolve inside `books/`. If you moved books, re-index so paths are updated.
- **CI fails with `ModuleNotFoundError: No module named 'api'`**: Make sure `pytest.ini` exists and CI runs `python -m pytest -q`.
- **CI fails on static file MIME types**: Linux and Windows report different MIME types for `.js` (`text/javascript` vs `application/javascript`). Tests should accept both.

## Useful one-liners

```bash
# Quick API smoke test
python -c "from api import app; print('ok')"

# Count recipes in the local DB
python -c "import sqlite3; print(sqlite3.connect('cookster.db').execute('SELECT COUNT(*) FROM recipes').fetchone()[0])"

# List indexed books
python -c "import sqlite3; import api; [print(api._clean_source(r[0])) for r in sqlite3.connect('cookster.db').execute('SELECT DISTINCT source FROM recipes') if r[0]]"
```
