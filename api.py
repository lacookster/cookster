import hashlib
import hmac
import html
import json
import os
import secrets
import threading
import time
from typing import List, Tuple

try:
    import nltk
    from nltk.stem import WordNetLemmatizer
    lemmatizer = WordNetLemmatizer()
    # ensure wordnote data is available; download if missing
    try:
        lemmatizer.lemmatize('test')
    except LookupError:
        nltk.download('wordnet')
        nltk.download('omw-1.4')
except Exception:
    # fallback simple identity function
    lemmatizer = None

from fastapi import FastAPI, Form, Query, Request, Response, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import re
import sqlite3

from ranking import rank_recipes
from indexer import _image_path_to_url, _slug_for_path, build_index

app = FastAPI(title='Cookster API')
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), 'templates'))

static_dir = os.path.join(os.path.dirname(__file__), 'static')
if os.path.isdir(static_dir):
    app.mount('/static', StaticFiles(directory=static_dir), name='static')

# Simple password protection --------------------------------------------------
# The password can be set via the COOKSTER_PASSWORD environment variable.
# If not set, a default is used. In production you should always use the env var.
_COOKSTER_PASSWORD = os.environ.get('COOKSTER_PASSWORD') or 'C))kstERn@p5t3r'
_PASSWORD_HASH = hashlib.sha256(_COOKSTER_PASSWORD.encode('utf-8')).hexdigest()
_SECRET_KEY = os.environ.get('COOKSTER_SECRET') or secrets.token_hex(32)
_SESSION_COOKIE = 'cookster_session'
_SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 days
_session_serializer = URLSafeTimedSerializer(_SECRET_KEY, salt='cookster-auth')

# Persistent user-data cookie. This is separate from the auth session and is
# intentionally NOT cleared on logout so user data survives re-authentication.
_USER_COOKIE = 'cookster_user'
_USER_COOKIE_MAX_AGE = 60 * 60 * 24 * 365 * 10  # 10 years


def _verify_password(password: str) -> bool:
    """Constant-time password comparison."""
    provided = hashlib.sha256(password.encode('utf-8')).hexdigest()
    return hmac.compare_digest(provided, _PASSWORD_HASH)


def _set_session(response: RedirectResponse) -> None:
    token = _session_serializer.dumps({'auth': True})
    response.set_cookie(
        _SESSION_COOKIE,
        token,
        max_age=_SESSION_MAX_AGE,
        httponly=True,
        samesite='lax',
        secure=False,  # set to True if serving over HTTPS
    )


def _clear_session(response: RedirectResponse) -> None:
    response.delete_cookie(_SESSION_COOKIE)


def _is_authenticated(request: Request) -> bool:
    token = request.cookies.get(_SESSION_COOKIE)
    if not token:
        return False
    try:
        data = _session_serializer.loads(token, max_age=_SESSION_MAX_AGE)
        return bool(data.get('auth'))
    except (BadSignature, SignatureExpired):
        return False


def _get_or_create_user_token(request: Request) -> str:
    """Return the persistent user-data token from the cookie, creating one if absent."""
    token = request.cookies.get(_USER_COOKIE)
    if not token:
        token = secrets.token_urlsafe(32)
    return token


def _set_user_token(response: Response, token: str) -> None:
    """Set the long-lived user-data cookie."""
    response.set_cookie(
        _USER_COOKIE,
        token,
        max_age=_USER_COOKIE_MAX_AGE,
        httponly=True,
        samesite='lax',
        path='/',
        secure=False,  # set to True if serving over HTTPS
    )


def _get_user_db() -> sqlite3.Connection:
    """Return a connection to the dedicated user-data SQLite database."""
    conn = sqlite3.connect(_USER_DB_PATH)
    conn.row_factory = sqlite3.Row
    _ensure_user_data_schema(conn)
    return conn


def _ensure_user_data_schema(conn: sqlite3.Connection) -> None:
    """Create the user_data table if it does not exist."""
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS user_data (
        token TEXT PRIMARY KEY,
        data TEXT NOT NULL,
        updated_at REAL NOT NULL
    )''')
    conn.commit()


def _load_user_data(token: str) -> dict:
    """Return the stored JSON blob for a user token, or an empty dict."""
    try:
        conn = _get_user_db()
        c = conn.cursor()
        row = c.execute('SELECT data, updated_at FROM user_data WHERE token = ?', (token,)).fetchone()
        conn.close()
        if row:
            return {'data': json.loads(row['data']), 'updated_at': row['updated_at']}
    except Exception:
        pass
    return {'data': {}, 'updated_at': 0}


def _save_user_data(token: str, data: dict) -> None:
    """Persist a JSON blob for a user token."""
    conn = _get_user_db()
    c = conn.cursor()
    c.execute('INSERT INTO user_data (token, data, updated_at) VALUES (?, ?, ?) ON CONFLICT(token) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at',
              (token, json.dumps(data), time.time()))
    conn.commit()
    conn.close()


def _delete_user_data(token: str) -> None:
    """Delete all server-side data for a user token."""
    conn = _get_user_db()
    c = conn.cursor()
    c.execute('DELETE FROM user_data WHERE token = ?', (token,))
    conn.commit()
    conn.close()


@app.middleware('http')
async def auth_middleware(request: Request, call_next):
    """Redirect unauthenticated users to /login except for public paths."""
    public_paths = {'/login', '/logout', '/favicon.ico'}
    path = request.url.path

    # Static files and login/logout are always public.
    if path.startswith('/static/') or path in public_paths:
        return await call_next(request)

    if _is_authenticated(request):
        return await call_next(request)

    # API routes return 401; HTML routes redirect to login.
    if path.startswith('/api/'):
        return JSONResponse({'error': 'Authentication required'}, status_code=401)

    return RedirectResponse(url='/login', status_code=302)


@app.get('/login', response_class=HTMLResponse)
def login_page(request: Request, error: str = Query('')):
    tmpl = templates.env.get_template('login.html')
    content = tmpl.render(request=request, error=error)
    return HTMLResponse(content)


@app.post('/login')
def login(request: Request, password: str = Form('')):
    if _verify_password(password):
        response = RedirectResponse(url='/', status_code=302)
        _set_session(response)
        token = _get_or_create_user_token(request)
        _set_user_token(response, token)
        return response
    return RedirectResponse(url='/login?error=1', status_code=302)


@app.get('/logout')
def logout():
    response = RedirectResponse(url='/login', status_code=302)
    _clear_session(response)
    # NOTE: we deliberately do NOT clear _USER_COOKIE so that logging back in
    # restores the same favourites/lists/etc.
    return response


@app.get('/api/user-data')
def get_user_data(request: Request):
    """Return the current user's persisted data blob."""
    token = _get_or_create_user_token(request)
    payload = _load_user_data(token)
    response = JSONResponse(payload)
    _set_user_token(response, token)
    return response


@app.post('/api/user-data')
def save_user_data(request: Request, payload: dict):
    """Persist the current user's data blob."""
    token = _get_or_create_user_token(request)
    data = payload.get('data', {})
    _save_user_data(token, data)
    response = JSONResponse({'ok': True})
    _set_user_token(response, token)
    return response


@app.get('/api/user-data/export')
def export_user_data(request: Request):
    """Export the user's recovery token and data for safekeeping."""
    token = _get_or_create_user_token(request)
    payload = _load_user_data(token)
    response = JSONResponse({
        'token': token,
        'data': payload['data'],
        'updated_at': payload['updated_at'],
    })
    _set_user_token(response, token)
    return response


@app.post('/api/user-data/import')
def import_user_data(request: Request, payload: dict):
    """Adopt another user's token (and optionally their data) on this device."""
    token = payload.get('token', '').strip()
    if not token:
        raise HTTPException(status_code=400, detail='token is required')
    provided_data = payload.get('data')
    if isinstance(provided_data, dict):
        _save_user_data(token, provided_data)
    response = JSONResponse(_load_user_data(token))
    _set_user_token(response, token)
    return response


@app.post('/api/user-data/reset')
def reset_user_data(request: Request):
    """Permanently delete the current user's server-side data."""
    token = request.cookies.get(_USER_COOKIE)
    if token:
        _delete_user_data(token)
    response = JSONResponse({'ok': True})
    # Clear the user cookie so a fresh empty token is created next time.
    response.delete_cookie(_USER_COOKIE, path='/')
    return response


# Directories that file paths must stay within.
DB_DIR = os.path.dirname(os.path.abspath(__file__))
BOOKS_DIR = os.path.join(DB_DIR, 'books')
BOOKS_ADDED_DIR = os.path.join(BOOKS_DIR, 'added')

# Dedicated SQLite DB for user data (favourites, lists, shopping, etc.)
_USER_DB_PATH = os.path.join(DB_DIR, 'cookster_user_data.db')


def _is_under(path: str, base: str) -> bool:
    base = os.path.abspath(base)
    path = os.path.abspath(path)
    return path == base or path.startswith(base + os.sep)


def resolve_db_path(db: str) -> str:
    """Resolve a DB filename/path to an absolute path inside DB_DIR.

    Rejects absolute paths outside DB_DIR and relative paths containing '..'.
    """
    if not db or db.strip() in ('', '.'):
        raise ValueError('db parameter is required')
    if '..' in db.split(os.sep) or '..' in db.split('/'):
        raise ValueError('invalid db path')
    if os.path.isabs(db):
        resolved = os.path.abspath(db)
    else:
        resolved = os.path.abspath(os.path.join(DB_DIR, db))
    if not _is_under(resolved, DB_DIR):
        raise ValueError('db path is outside allowed directory')
    return resolved


def resolve_download_path(file_path: str) -> str:
    """Resolve a download path to an absolute path inside BOOKS_DIR.

    Stored paths are usually relative to the project root (e.g.
    ``books\\file.epub`` or ``books\\added\\file.epub``). Paths that are
    already absolute and lie inside ``BOOKS_DIR`` are also accepted.
    """
    if not file_path:
        raise ValueError('file_path is required')

    candidates = []
    if os.path.isabs(file_path):
        candidates.append(os.path.abspath(file_path))
    else:
        # Primary interpretation: path is relative to the project root.
        candidates.append(os.path.abspath(os.path.join(DB_DIR, file_path)))
        # Older/bare paths may be relative to BOOKS_DIR itself.
        candidates.append(os.path.abspath(os.path.join(BOOKS_DIR, file_path)))
        candidates.append(os.path.abspath(os.path.join(BOOKS_ADDED_DIR, file_path)))
        # Filename-only fallback for books moved into books/added.
        candidates.append(os.path.abspath(os.path.join(BOOKS_ADDED_DIR, os.path.basename(file_path))))

    for resolved in candidates:
        if _is_under(resolved, BOOKS_DIR) and os.path.isfile(resolved):
            return resolved

    # Fallback to the primary candidate even if missing, so the error message
    # remains predictable when a file genuinely does not exist.
    resolved = candidates[0]
    if not _is_under(resolved, BOOKS_DIR):
        raise ValueError('download path is outside allowed directory')
    return resolved


def _clean_source(source: str) -> str:
    """Return a display-friendly book title from a filename.

    Strips Anna's Archive metadata tails and PDFDrive suffixes.
    """
    if not source:
        return ''
    base = os.path.splitext(source)[0]
    # Anna's Archive pattern: "Title -- Author -- Place, Year -- Publisher -- isbn13 ..."
    if ' -- ' in base:
        base = base.split(' -- ')[0]
    # PDFDrive copy suffixes like " ( PDFDrive.com )(1)"
    base = re.sub(r'\s*\(\s*PDFDrive\.com\s*\)\s*(?:\(\d+\))?\s*$', '', base, flags=re.I)
    # Trailing underscores/hyphens used in slugified names
    base = re.sub(r'[_\-]+$', '', base)
    return base.strip(' -_')


def _snippet(text: str, q: str, radius: int = 120, full_if_short: int = 320):
    """Return a snippet of text around the first query match, or a useful fallback.

    If the text is short enough, return it in full. If the query is not found,
    return the first `fallback` characters. Matches are highlighted.
    """
    if not text:
        return ''
    escaped = html.escape(text).replace('\n', '<br>')
    if not q:
        return escaped[:full_if_short]
    if len(text) <= full_if_short:
        return _highlight_html(text, q).replace('\n', '<br>')
    idx = text.lower().find(q.lower())
    if idx == -1:
        return escaped[:200]
    start = max(0, idx - radius)
    end = min(len(text), idx + radius)
    s = (('...' if start > 0 else '') + text[start:end] + ('...' if end < len(text) else ''))
    return _highlight_html(s, q).replace('\n', '<br>')


def _highlight_html(text: str, q: str):
    # Build match ranges on the original text, then escape when rebuilding
    orig = text
    q_lower = q.lower()
    tokens = [t for t in re.findall(r"\w+", q_lower) if t]

    def norm(w):
        if lemmatizer:
            try:
                return lemmatizer.lemmatize(w)
            except Exception:
                return w
        return w

    norm_tokens = [norm(t) for t in tokens]

    # Build phrase n-grams from query tokens (length >=2)
    phrases = []
    n = len(tokens)
    for L in range(n, 1, -1):
        for i in range(0, n - L + 1):
            phrases.append(' '.join(tokens[i:i+L]))
    # include full normalized phrase forms
    norm_phrases = [' '.join([norm(w) for w in ph.split()]) for ph in phrases]

    matches = []
    # find phrase matches first (longer spans)
    for ph in norm_phrases:
        pattern = re.compile(r'\b' + re.escape(ph) + r'\b', re.I)
        for m in pattern.finditer(orig.lower()):
            matches.append((m.start(), m.end()))

    # find token matches
    for t in norm_tokens:
        pattern = re.compile(r'\b' + re.escape(t) + r'\b', re.I)
        for m in pattern.finditer(orig.lower()):
            matches.append((m.start(), m.end()))

    # sort matches by start then by length desc and select non-overlapping
    matches = sorted(matches, key=lambda x: (x[0], -(x[1]-x[0])))
    selected = []
    last_end = -1
    for s,e in matches:
        if s >= last_end:
            selected.append((s,e))
            last_end = e

    # rebuild escaped string with <b> tags at selected ranges
    if not selected:
        return html.escape(orig)

    out_parts = []
    idx = 0
    for s,e in selected:
        if idx < s:
            out_parts.append(html.escape(orig[idx:s]))
        out_parts.append('<b>' + html.escape(orig[s:e]) + '</b>')
        idx = e
    if idx < len(orig):
        out_parts.append(html.escape(orig[idx:]))
    return ''.join(out_parts)


def _sanitize_fts_query(q: str) -> str:
    """Escape FTS5 special characters so user input cannot break MATCH.

    FTS5 treats double quotes, asterisks, and NEAR/OR/AND specially. We keep
    alphanumeric tokens separated by spaces, which is robust and still allows
    multi-word searches.
    """
    # Extract word tokens; drop everything else.
    tokens = re.findall(r"\w+", q)
    return ' '.join(tokens)


def compute_stable_id(recipe: dict) -> str:
    """Return a stable identifier for a recipe based on content.

    The hash is computed from the title, source, and full ingredients/steps
    so it survives DB re-indexing and is very unlikely to collide.
    """
    text = '::'.join([
        (recipe.get('title') or '').strip().lower(),
        (recipe.get('source') or '').strip().lower(),
        (recipe.get('ingredients') or '').strip().lower(),
        (recipe.get('steps') or '').strip().lower(),
    ])
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:24]


def _ensure_schema(conn: sqlite3.Connection):
    """Make sure the recipes table has the stable_id column and an index, and backfill any missing values."""
    c = conn.cursor()
    cols = [r[1] for r in c.execute('PRAGMA table_info(recipes)')]
    if 'stable_id' not in cols:
        c.execute('ALTER TABLE recipes ADD COLUMN stable_id TEXT')
    if 'serves' not in cols:
        c.execute('ALTER TABLE recipes ADD COLUMN serves TEXT')
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_recipes_stable_id ON recipes(stable_id)')
    conn.commit()
    # Backfill any rows that don't have a stable_id yet.
    needs_backfill = c.execute('SELECT 1 FROM recipes WHERE stable_id IS NULL LIMIT 1').fetchone() is not None
    if needs_backfill:
        rows = c.execute('SELECT id, title, ingredients, source, file_path, steps FROM recipes WHERE stable_id IS NULL').fetchall()
        upd = conn.cursor()
        for row in rows:
            rid, title, ingredients, source, file_path, steps = row
            stable_id = compute_stable_id({
                'title': title or '',
                'source': source or '',
                'ingredients': ingredients or '',
                'steps': steps or '',
            })
            upd.execute('UPDATE recipes SET stable_id = ? WHERE id = ?', (stable_id, rid))
        conn.commit()


def _lookup_recipe(conn: sqlite3.Connection, recipe_id_or_stable: str):
    """Find a recipe by integer id or stable_id. Returns a dict or None.

    Handles DBs without an image column.
    """
    c = conn.cursor()
    base_cols = 'id, title, ingredients, steps, source, file_path, stable_id'
    try:
        cols = base_cols + ', image, serves'
        if recipe_id_or_stable.isdigit():
            c.execute(f'SELECT {cols} FROM recipes WHERE id = ?', (int(recipe_id_or_stable),))
        else:
            c.execute(f'SELECT {cols} FROM recipes WHERE stable_id = ?', (recipe_id_or_stable,))
        row = c.fetchone()
    except sqlite3.OperationalError:
        cols = base_cols
        try:
            cols = base_cols + ', image'
            if recipe_id_or_stable.isdigit():
                c.execute(f'SELECT {cols} FROM recipes WHERE id = ?', (int(recipe_id_or_stable),))
            else:
                c.execute(f'SELECT {cols} FROM recipes WHERE stable_id = ?', (recipe_id_or_stable,))
            row = c.fetchone()
        except sqlite3.OperationalError:
            cols = base_cols
            if recipe_id_or_stable.isdigit():
                c.execute(f'SELECT {cols} FROM recipes WHERE id = ?', (int(recipe_id_or_stable),))
            else:
                c.execute(f'SELECT {cols} FROM recipes WHERE stable_id = ?', (recipe_id_or_stable,))
            row = c.fetchone()
    if not row:
        return None
    has_image = 'image' in cols
    has_serves = 'serves' in cols
    return {
        'id': row[0],
        'title': row[1],
        'ingredients': row[2] or '',
        'steps': row[3] or '',
        'source': row[4],
        'file_path': row[5],
        'stable_id': row[6] or '',
        'image': row[7] if has_image else '',
        'serves': row[8] if has_serves else '',
    }


def _parse_query_tokens(q: str) -> Tuple[List[str], List[str]]:
    tokens = [t for t in (q or '').lower().split() if t]
    positive = [t for t in tokens if not t.startswith('-')]
    negative = [t[1:] for t in tokens if t.startswith('-') and len(t) > 1]
    return positive, negative


def _candidate_matches(candidate: dict, q: str) -> bool:
    positive, negative = _parse_query_tokens(q)
    text = ((candidate.get('title') or '') + ' ' + (candidate.get('ingredients') or '') + ' ' + (candidate.get('steps') or '')).lower()
    if positive and not all(t in text for t in positive):
        return False
    if negative and any(t in text for t in negative):
        return False
    return True


def _query_db(db_path: str, q: str, limit: int = 10, page: int = 1, source: str = None):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    _ensure_schema(conn)
    results = []

    def _fetch_rows(where: str, params=()):
        """Fetch candidate rows, gracefully handling DBs without image/serves columns."""
        base_cols = "id, title, source, ingredients, steps, stable_id, serves"
        try:
            c.execute(f'SELECT {base_cols}, image FROM recipes {where}', params)
            rows = c.fetchall()
            return [dict(id=r[0], title=r[1], source=r[2], ingredients=r[3] or '', steps=r[4] or '', stable_id=r[5] or '', serves=r[6] or '', image=r[7] or '') for r in rows]
        except sqlite3.OperationalError:
            c.execute(f'SELECT id, title, source, ingredients, steps, stable_id FROM recipes {where}', params)
            rows = c.fetchall()
            return [dict(id=r[0], title=r[1], source=r[2], ingredients=r[3] or '', steps=r[4] or '', stable_id=r[5] or '', serves='', image='') for r in rows]

    source_filter = ''
    source_params = []
    if source:
        source_filter = "WHERE source = ?"
        source_params = [source]

    use_fts = True
    try:
        safe_q = _sanitize_fts_query(q)
        if safe_q:
            c.execute(f"SELECT rowid FROM recipes_fts WHERE recipes_fts MATCH ?", (safe_q,))
            ids = [r[0] for r in c.fetchall()]
            if not ids:
                conn.close()
                return [], 0
            if source_filter:
                where = f"WHERE id IN ({','.join('?' for _ in ids)}) AND source = ?"
                params = ids + [source]
            else:
                where = f"WHERE id IN ({','.join('?' for _ in ids)})"
                params = ids
            candidates = _fetch_rows(where, params)
        else:
            candidates = _fetch_rows(source_filter, source_params) if source_filter else _fetch_rows("")
            use_fts = False
    except sqlite3.OperationalError:
        candidates = _fetch_rows(source_filter, source_params) if source_filter else _fetch_rows("")
        use_fts = False

    # rank candidates with BM25, then apply positive/negative token filters.
    ranked = rank_recipes(candidates, q, top_n=len(candidates) if not use_fts else limit * page)
    ranked = [c for c in ranked if _candidate_matches(c, q)]
    total = len(ranked)

    # pagination
    start = (page - 1) * limit
    page_items = ranked[start:start + limit]

    for r in page_items:
        results.append({
            'id': r['id'],
            'stable_id': r['stable_id'],
            'title': r['title'],
            'source': _clean_source(r['source']),
            'source_raw': r['source'],
            'serves': r.get('serves', ''),
            'ingredients_snippet': _snippet(r.get('ingredients',''), q),
            'steps_snippet': _snippet(r.get('steps',''), q),
            'image_url': _image_path_to_url(r.get('image', '')),
            'score': r.get('score', 0.0),
        })
    conn.close()
    return results, total


@app.get('/', response_class=HTMLResponse)
def ui(request: Request):
    tmpl = templates.env.get_template('index.html')
    content = tmpl.render(request=request)
    return HTMLResponse(content)


@app.get('/search')
def search(q: str = Query(..., min_length=1),
           db: str = Query('cookster.db'),
           limit: int = Query(10, ge=1, le=100),
           page: int = Query(1, ge=1, le=10000),
           source: str = Query(None)):
    try:
        db_path = resolve_db_path(db)
    except ValueError as e:
        return JSONResponse({'error': str(e)}, status_code=400)
    if not os.path.exists(db_path):
        return JSONResponse({'error': 'DB not found', 'db': db}, status_code=400)
    results, total = _query_db(db_path, q, limit, page, source=source)
    return {'query': q, 'results': results, 'page': page, 'total': total, 'source': source}


@app.get('/api/sources')
def list_sources(db: str = Query('cookster.db')):
    """Return distinct source books for the filter dropdown (raw DB value + cleaned label)."""
    try:
        db_path = resolve_db_path(db)
    except ValueError as e:
        return JSONResponse({'error': str(e)}, status_code=400)
    if not os.path.exists(db_path):
        return JSONResponse({'error': 'DB not found', 'db': db}, status_code=400)
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    try:
        rows = c.execute('SELECT DISTINCT source FROM recipes WHERE source IS NOT NULL AND source != ""').fetchall()
    except sqlite3.OperationalError:
        rows = []
    conn.close()
    seen = set()
    sources = []
    for r in rows:
        raw = r[0]
        if not raw or raw in seen:
            continue
        seen.add(raw)
        sources.append({'raw': raw, 'clean': _clean_source(raw)})
    sources.sort(key=lambda x: x['clean'])
    return {'sources': sources}


@app.get('/api/stats')
def stats(db: str = Query('cookster.db')):
    """Return high-level index statistics."""
    try:
        db_path = resolve_db_path(db)
    except ValueError as e:
        return JSONResponse({'error': str(e)}, status_code=400)
    if not os.path.exists(db_path):
        return JSONResponse({'error': 'DB not found', 'db': db}, status_code=400)
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    try:
        total = c.execute('SELECT COUNT(*) FROM recipes').fetchone()[0]
        books = c.execute('SELECT COUNT(DISTINCT source) FROM recipes WHERE source IS NOT NULL AND source != ""').fetchone()[0]
    except sqlite3.OperationalError:
        total = 0
        books = 0
    conn.close()
    return {'total_recipes': total, 'total_books': books}


@app.get('/api/recipes')
def batch_recipes(ids: str = Query(...), db: str = Query('cookster.db')):
    """Return recipe summaries for a comma-separated list of integer ids or stable_ids."""
    try:
        db_path = resolve_db_path(db)
    except ValueError as e:
        return JSONResponse({'error': str(e)}, status_code=400)
    if not os.path.exists(db_path):
        return JSONResponse({'error': 'DB not found', 'db': db}, status_code=400)

    raw = [x.strip() for x in ids.split(',') if x.strip()]
    if not raw:
        return []
    if len(raw) > 500:
        return JSONResponse({'error': 'too many ids (max 500)'}, status_code=400)

    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    _ensure_schema(conn)

    # Split into integer ids and stable_ids.
    int_ids = [int(x) for x in raw if x.isdigit()]
    stable_ids = [x for x in raw if not x.isdigit()]

    base_cols = 'id, title, source, stable_id, ingredients, serves'
    has_image = True
    try:
        cols = base_cols + ', image'
        rows = []
        if int_ids:
            placeholders = ','.join('?' * len(int_ids))
            rows += c.execute(f'SELECT {cols} FROM recipes WHERE id IN ({placeholders})', int_ids).fetchall()
        if stable_ids:
            placeholders = ','.join('?' * len(stable_ids))
            rows += c.execute(f'SELECT {cols} FROM recipes WHERE stable_id IN ({placeholders})', stable_ids).fetchall()
    except sqlite3.OperationalError:
        has_image = False
        cols = base_cols
        rows = []
        if int_ids:
            placeholders = ','.join('?' * len(int_ids))
            rows += c.execute(f'SELECT {cols} FROM recipes WHERE id IN ({placeholders})', int_ids).fetchall()
        if stable_ids:
            placeholders = ','.join('?' * len(stable_ids))
            rows += c.execute(f'SELECT {cols} FROM recipes WHERE stable_id IN ({placeholders})', stable_ids).fetchall()
    conn.close()

    seen = set()
    out = []
    for r in rows:
        key = r[0]
        if key in seen:
            continue
        seen.add(key)
        out.append({
            'id': r[0],
            'title': r[1] or '',
            'source': _clean_source(r[2] or ''),
            'source_raw': r[2] or '',
            'stable_id': r[3] or '',
            'ingredients': r[4] or '',
            'serves': r[5] if not has_image else r[5] or '',
            'image_url': _image_path_to_url(r[6] or '') if has_image else '',
        })
    return out


@app.get('/recipe/{recipe_id}', response_class=HTMLResponse)
def recipe_view(request: Request, recipe_id: str, db: str = Query('cookster.db')):
    try:
        db_path = resolve_db_path(db)
    except ValueError as e:
        return templates.TemplateResponse('recipe.html', {'request': request, 'error': str(e)})
    if not os.path.exists(db_path):
        return templates.TemplateResponse('recipe.html', {'request': request, 'error': 'DB not found'})
    conn = sqlite3.connect(db_path)
    _ensure_schema(conn)
    recipe = _lookup_recipe(conn, recipe_id)
    conn.close()
    if not recipe:
        return templates.TemplateResponse('recipe.html', {'request': request, 'error': 'Recipe not found'})
    recipe['source'] = _clean_source(recipe['source'])
    image_url = _image_path_to_url(recipe.get('image', ''))
    # Determine whether method steps are already numbered so we can avoid
    # adding duplicate CSS counters.
    step_lines = [s.strip() for s in (recipe.get('steps') or '').split('\n') if s.strip()]
    numbered = sum(1 for s in step_lines if re.match(r'^\d+[\.\)]\s*', s)) if step_lines else 0
    steps_numbered = numbered > len(step_lines) // 2

    tmpl = templates.env.get_template('recipe.html')
    content = tmpl.render(request=request, recipe=recipe, image_url=image_url,
                          steps_numbered=steps_numbered, serves=recipe.get('serves', ''))
    return HTMLResponse(content)


@app.get('/download/{recipe_id}')
def download_recipe(recipe_id: str, db: str = Query('cookster.db')):
    """Download a single recipe as a Markdown file."""
    try:
        db_path = resolve_db_path(db)
    except ValueError as e:
        return JSONResponse({'error': str(e)}, status_code=400)
    if not os.path.exists(db_path):
        return JSONResponse({'error': 'DB not found'}, status_code=400)
    conn = sqlite3.connect(db_path)
    _ensure_schema(conn)
    recipe = _lookup_recipe(conn, recipe_id)
    conn.close()
    if not recipe:
        return JSONResponse({'error': 'recipe not found'}, status_code=404)

    title = recipe.get('title', 'Recipe')
    source = recipe.get('source', '')
    serves = recipe.get('serves', '')
    ingredients = recipe.get('ingredients', '')
    steps = recipe.get('steps', '')

    lines = [f'# {title}', '']
    if source:
        lines.append(f'From: {source}')
    if serves:
        lines.append(f'Serves: {serves}')
    lines.append('')
    lines.append('## Ingredients')
    lines.append('')
    for line in ingredients.split('\n'):
        line = line.strip()
        if line:
            lines.append(f'- {line}')
    lines.append('')
    lines.append('## Method')
    lines.append('')
    for i, step in enumerate((s for s in steps.split('\n') if s.strip()), 1):
        lines.append(f'{i}. {step.strip()}')
    content = '\n'.join(lines)

    safe_title = re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '-').replace('--', '-')[:60] or 'recipe'
    filename = f'{safe_title}.md'
    headers = {'Content-Disposition': f'attachment; filename="{filename}"'}
    return PlainTextResponse(content, media_type='text/markdown; charset=utf-8', headers=headers)


@app.get('/api/suggest')
def suggest(q: str = Query(..., min_length=1),
            db: str = Query('cookster.db'),
            limit: int = Query(8, ge=1, le=20)):
    """Return recipe title suggestions matching the query prefix."""
    try:
        db_path = resolve_db_path(db)
    except ValueError as e:
        return JSONResponse({'error': str(e)}, status_code=400)
    if not os.path.exists(db_path):
        return JSONResponse({'error': 'DB not found', 'db': db}, status_code=400)
    safe = re.sub(r'[^\w\s]', '', q).strip()
    if not safe:
        return {'query': q, 'suggestions': []}
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    like = f'%{safe}%'
    rows = c.execute('SELECT title FROM recipes WHERE title LIKE ? ORDER BY title LIMIT ?', (like, limit)).fetchall()
    conn.close()
    return {'query': q, 'suggestions': [r[0] for r in rows]}


@app.get('/api/random')
def random_recipe(db: str = Query('cookster.db')):
    """Return a single random recipe summary."""
    try:
        db_path = resolve_db_path(db)
    except ValueError as e:
        return JSONResponse({'error': str(e)}, status_code=400)
    if not os.path.exists(db_path):
        return JSONResponse({'error': 'DB not found', 'db': db}, status_code=400)
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    _ensure_schema(conn)
    try:
        row = c.execute('SELECT id, title, source, stable_id, image FROM recipes ORDER BY RANDOM() LIMIT 1').fetchone()
    except sqlite3.OperationalError:
        row = c.execute('SELECT id, title, source, stable_id FROM recipes ORDER BY RANDOM() LIMIT 1').fetchone()
    conn.close()
    if not row:
        return JSONResponse({'error': 'no recipes'}, status_code=404)
    has_image = len(row) >= 5
    return {
        'id': row[0],
        'title': row[1],
        'source': _clean_source(row[2]),
        'stable_id': row[3],
        'image_url': _image_path_to_url(row[4] if has_image else ''),
    }


@app.get('/api/related/{stable_id}')
def related_recipes(stable_id: str, db: str = Query('cookster.db')):
    """Return recipes related to the given one: more from the same book and similar recipes."""
    try:
        db_path = resolve_db_path(db)
    except ValueError as e:
        return JSONResponse({'error': str(e)}, status_code=400)
    if not os.path.exists(db_path):
        return JSONResponse({'error': 'DB not found', 'db': db}, status_code=400)
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    _ensure_schema(conn)
    target = _lookup_recipe(conn, stable_id)
    if not target:
        conn.close()
        return JSONResponse({'error': 'recipe not found'}, status_code=404)

    raw_source = target.get('source') or ''

    # More from the same book
    try:
        c.execute('SELECT id, title, source, stable_id, image FROM recipes WHERE source = ? AND stable_id != ? ORDER BY title LIMIT 6',
                  (raw_source, stable_id))
        rows = c.fetchall()
    except sqlite3.OperationalError:
        c.execute('SELECT id, title, source, stable_id FROM recipes WHERE source = ? AND stable_id != ? ORDER BY title LIMIT 6',
                  (raw_source, stable_id))
        rows = [(*r, '') for r in c.fetchall()]

    same_book = []
    seen = {stable_id}
    for r in rows:
        sid = r[3]
        if sid in seen:
            continue
        seen.add(sid)
        same_book.append({
            'id': r[0],
            'title': r[1],
            'source': _clean_source(r[2]),
            'stable_id': sid,
            'image_url': _image_path_to_url(r[4] or ''),
        })

    # Similar recipes from other books using BM25 with the target's content as the query
    candidates = []
    try:
        c.execute('SELECT id, title, source, stable_id, ingredients, steps, image FROM recipes WHERE stable_id != ? AND source != ?',
                  (stable_id, raw_source))
        rows = c.fetchall()
    except sqlite3.OperationalError:
        c.execute('SELECT id, title, source, stable_id, ingredients, steps FROM recipes WHERE stable_id != ? AND source != ?',
                  (stable_id, raw_source))
        rows = [(*r, '') for r in c.fetchall()]
    for r in rows:
        candidates.append({
            'id': r[0],
            'title': r[1],
            'source': r[2],
            'stable_id': r[3],
            'ingredients': r[4] or '',
            'steps': r[5] or '',
            'image': r[6] or '',
        })

    query = (target.get('title') or '') + ' ' + (target.get('ingredients') or '')
    ranked = rank_recipes(candidates, query, top_n=6)
    similar = []
    for r in ranked:
        if r['stable_id'] in seen:
            continue
        seen.add(r['stable_id'])
        similar.append({
            'id': r['id'],
            'title': r['title'],
            'source': _clean_source(r['source']),
            'stable_id': r['stable_id'],
            'image_url': _image_path_to_url(r.get('image', '')),
        })

    conn.close()
    return {'same_book': same_book, 'similar': similar}


@app.get('/api/recipes-by-source')
def recipes_by_source(
    source: str = Query(...),
    db: str = Query('cookster.db'),
    limit: int = Query(50, ge=1, le=200),
    page: int = Query(1, ge=1, le=10000),
):
    """Return all recipes from a single source book, paginated and ranked by title."""
    try:
        db_path = resolve_db_path(db)
    except ValueError as e:
        return JSONResponse({'error': str(e)}, status_code=400)
    if not os.path.exists(db_path):
        return JSONResponse({'error': 'DB not found', 'db': db}, status_code=400)
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    _ensure_schema(conn)
    base_cols = 'id, title, source, stable_id, serves'
    try:
        cols = base_cols + ', image'
        c.execute(f'SELECT {cols} FROM recipes WHERE source = ? ORDER BY title COLLATE NOCASE LIMIT ? OFFSET ?',
                  (source, limit, (page - 1) * limit))
        rows = c.fetchall()
        c.execute('SELECT COUNT(*) FROM recipes WHERE source = ?', (source,))
        total = c.fetchone()[0]
    except sqlite3.OperationalError:
        cols = base_cols
        c.execute(f'SELECT {cols} FROM recipes WHERE source = ? ORDER BY title COLLATE NOCASE LIMIT ? OFFSET ?',
                  (source, limit, (page - 1) * limit))
        rows = c.fetchall()
        c.execute('SELECT COUNT(*) FROM recipes WHERE source = ?', (source,))
        total = c.fetchone()[0]
    conn.close()
    results = []
    for r in rows:
        # Columns: id, title, source, stable_id, serves, image (6 total)
        has_image = len(r) >= 6
        results.append({
            'id': r[0],
            'title': r[1] or '',
            'source': _clean_source(r[2] or ''),
            'source_raw': r[2] or '',
            'stable_id': r[3] or '',
            'serves': r[4] or '',
            'image_url': _image_path_to_url(r[5] or '') if has_image else '',
        })
    return {'source': _clean_source(source), 'source_raw': source, 'page': page, 'limit': limit, 'total': total, 'results': results}


@app.get('/books', response_class=HTMLResponse)
def books_list(request: Request, db: str = Query('cookster.db')):
    """Render a page listing all indexed books with recipe counts."""
    try:
        db_path = resolve_db_path(db)
    except ValueError as e:
        tmpl = templates.env.get_template('books.html')
        content = tmpl.render(request=request, error=str(e))
        return HTMLResponse(content)
    if not os.path.exists(db_path):
        tmpl = templates.env.get_template('books.html')
        content = tmpl.render(request=request, error='DB not found')
        return HTMLResponse(content)
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    _ensure_schema(conn)
    try:
        rows = c.execute(
            'SELECT source, COUNT(*) FROM recipes '
            'WHERE source IS NOT NULL AND source != "" GROUP BY source ORDER BY source'
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    # Pick one representative image per source for a cover thumbnail.
    cover_images: Dict[str, str] = {}
    try:
        for raw_source, image in c.execute(
            "SELECT source, image FROM recipes "
            "WHERE image IS NOT NULL AND image != '' ORDER BY id"
        ):
            if raw_source not in cover_images:
                cover_images[raw_source] = _image_path_to_url(image)
    except sqlite3.OperationalError:
        pass
    conn.close()
    books = []
    seen = set()
    recipes_dir = os.path.join(DB_DIR, 'data', 'recipes')
    for raw, count in rows:
        if not raw or raw in seen:
            continue
        seen.add(raw)
        json_path = os.path.join(recipes_dir, f"{_slug_for_path(raw)}.json")
        added_at = os.path.getmtime(json_path) if os.path.exists(json_path) else 0.0
        books.append({
            'raw': raw,
            'clean': _clean_source(raw),
            'count': count,
            'image_url': cover_images.get(raw, ''),
            'added_at': added_at,
        })
    books.sort(key=lambda x: x['clean'])
    new_books = sorted(
        [b for b in books if b['added_at'] > 0],
        key=lambda x: x['added_at'],
        reverse=True,
    )[:12]
    tmpl = templates.env.get_template('books.html')
    content = tmpl.render(request=request, books=books, new_books=new_books, db=db)
    return HTMLResponse(content)


@app.get('/api/new-books', response_class=JSONResponse)
def api_new_books(request: Request, db: str = Query('cookster.db'), limit: int = Query(5, ge=1, le=50)):
    """Return the most recently indexed cookbooks for the homepage."""
    try:
        db_path = resolve_db_path(db)
    except ValueError as e:
        return JSONResponse({'error': str(e)}, status_code=400)
    if not os.path.exists(db_path):
        return JSONResponse({'books': []})
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    _ensure_schema(conn)
    rows = c.execute(
        'SELECT source, COUNT(*) FROM recipes '
        'WHERE source IS NOT NULL AND source != "" GROUP BY source'
    ).fetchall()
    cover_images: Dict[str, str] = {}
    try:
        for raw_source, image in c.execute(
            "SELECT source, image FROM recipes "
            "WHERE image IS NOT NULL AND image != '' ORDER BY id"
        ):
            if raw_source not in cover_images:
                cover_images[raw_source] = _image_path_to_url(image)
    except sqlite3.OperationalError:
        pass
    conn.close()
    recipes_dir = os.path.join(DB_DIR, 'data', 'recipes')
    books = []
    for raw, count in rows:
        if not raw:
            continue
        json_path = os.path.join(recipes_dir, f"{_slug_for_path(raw)}.json")
        added_at = os.path.getmtime(json_path) if os.path.exists(json_path) else 0.0
        books.append({
            'source': raw,
            'title': _clean_source(raw),
            'count': count,
            'image_url': cover_images.get(raw, ''),
            'added_at': added_at,
        })
    books.sort(key=lambda x: x['added_at'], reverse=True)
    return JSONResponse({'books': books[:limit]})


@app.get('/book', response_class=HTMLResponse)
def book_view(request: Request, source: str = Query(...), db: str = Query('cookster.db')):
    """Render a browse-by-book page for a single source."""
    try:
        db_path = resolve_db_path(db)
    except ValueError as e:
        tmpl = templates.env.get_template('book.html')
        content = tmpl.render(request=request, error=str(e))
        return HTMLResponse(content)
    if not os.path.exists(db_path):
        tmpl = templates.env.get_template('book.html')
        content = tmpl.render(request=request, error='DB not found')
        return HTMLResponse(content)
    tmpl = templates.env.get_template('book.html')
    content = tmpl.render(request=request, source=source, source_clean=_clean_source(source), db=db)
    return HTMLResponse(content)


# Background indexing state and endpoints -------------------------------------

_index_lock = threading.Lock()
_index_state = {
    'running': False,
    'state': 'idle',
    'message': '',
    'started_at': None,
    'finished_at': None,
    'books_total': 0,
    'books_done': 0,
}


def _resolve_index_dirs(books_dir: str = None, recipes_dir: str = None):
    """Return safe default directories for background indexing."""
    if books_dir:
        books = os.path.abspath(books_dir)
        if not _is_under(books, BOOKS_DIR):
            raise ValueError('books_dir outside project')
    else:
        books = BOOKS_DIR
    if recipes_dir:
        recipes = os.path.abspath(recipes_dir)
        if not _is_under(recipes, DB_DIR):
            raise ValueError('recipes_dir outside project')
    else:
        recipes = os.path.join(DB_DIR, 'data', 'recipes')
    return books, recipes


def _run_indexer(books_dir: str, recipes_dir: str, db_path: str, force: bool):
    global _index_state
    with _index_lock:
        _index_state.update({
            'running': True,
            'state': 'running',
            'message': 'Indexing started',
            'started_at': time.time(),
            'finished_at': None,
            'books_total': 0,
            'books_done': 0,
        })
    try:
        build_index(books_dir, recipes_dir, db_path, force=force)
        with _index_lock:
            _index_state.update({
                'running': False,
                'state': 'complete',
                'message': 'Indexing complete',
                'finished_at': time.time(),
            })
    except Exception as e:
        with _index_lock:
            _index_state.update({
                'running': False,
                'state': 'error',
                'message': str(e),
                'finished_at': time.time(),
            })


@app.get('/api/index/status')
def index_status():
    """Return the current state of the background indexer."""
    with _index_lock:
        status = dict(_index_state)
    status['started_at'] = status['started_at']
    status['finished_at'] = status['finished_at']
    return status


@app.post('/api/index/start')
def index_start(
    db: str = Query('cookster.db'),
    books_dir: str = Query(None),
    recipes_dir: str = Query(None),
    force: bool = Query(False),
):
    """Start a background re-index. Returns immediately with the new status."""
    try:
        db_path = resolve_db_path(db)
    except ValueError as e:
        return JSONResponse({'error': str(e)}, status_code=400)
    try:
        books, recipes = _resolve_index_dirs(books_dir, recipes_dir)
    except ValueError as e:
        return JSONResponse({'error': str(e)}, status_code=400)

    with _index_lock:
        if _index_state['running']:
            return JSONResponse({
                'error': 'Indexer already running',
                'status': dict(_index_state),
            }, status_code=409)

    thread = threading.Thread(
        target=_run_indexer,
        args=(books, recipes, db_path, force),
        daemon=True,
    )
    thread.start()
    return {
        'running': True,
        'state': 'running',
        'message': 'Indexing started',
        'started_at': _index_state['started_at'],
        'finished_at': None,
        'books_total': 0,
        'books_done': 0,
    }


@app.get('/api/index/start')
def index_start_get(
    db: str = Query('cookster.db'),
    books_dir: str = Query(None),
    recipes_dir: str = Query(None),
    force: bool = Query(False),
):
    """GET convenience wrapper for /api/index/start."""
    return index_start(db=db, books_dir=books_dir, recipes_dir=recipes_dir, force=force)
