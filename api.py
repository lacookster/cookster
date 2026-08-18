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


# Dietary / style filter helpers ------------------------------------------------
_FILTER_MEAT = {
    'chicken', 'beef', 'pork', 'lamb', 'duck', 'turkey', 'bacon', 'ham',
    'sausage', 'sausages', 'mince', 'meat', 'steak', 'venison', 'goose',
    'rabbit', 'quail', 'pancetta', 'prosciutto', 'salami', 'chorizo',
    'anchovy', 'anchovies', 'prawn', 'prawns', 'shrimp', 'fish', 'salmon',
    'cod', 'haddock', 'tuna', 'mackerel', 'trout', 'sardine', 'sardines',
    'mussel', 'mussels', 'clam', 'clams', 'oyster', 'oysters', 'squid',
    'octopus', 'calamari'
}
_FILTER_DAIRY_EGG = {
    'egg', 'eggs', 'milk', 'cream', 'butter', 'cheese', 'yogurt', 'yoghurt',
    'cheddar', 'mozzarella', 'parmesan', 'feta', 'ricotta', 'brie', 'camembert',
    'gouda', 'gruyere', 'honey'
}
_FILTER_GLUTEN = {
    'flour', 'wheat', 'barley', 'rye', 'couscous', 'semolina', 'breadcrumbs',
    'bread', 'pasta', 'noodle', 'noodles', 'spaghetti', 'macaroni', 'penne',
    'fusilli', 'lasagne', 'pizza', 'pastry', 'croissant', 'bagel', 'baguette'
}
_FILTER_NUTS = {
    'nut', 'nuts', 'almond', 'almonds', 'peanut', 'peanuts', 'cashew',
    'cashews', 'walnut', 'walnuts', 'pecan', 'pecans', 'pistachio', 'pistachios',
    'hazelnut', 'hazelnuts', 'macadamia', 'macadamias', 'pine nut', 'pine nuts',
    'peanut butter', 'almond butter'
}
_FILTER_DESSERT = {
    'chocolate', 'cake', 'cookie', 'cookies', 'pastry', 'pudding', 'tart',
    'pie', 'ice cream', 'dessert', 'sweet', 'sugar', 'brownie', 'muffin',
    'cupcake', 'cheesecake', 'biscuit', 'biscuits', 'donut', 'doughnut'
}
_FILTER_ONE_POT = {
    'one pan', 'one-pot', 'one pot', 'single pan', 'single pot', 'skillet',
    'traybake', 'sheet pan', 'baking tray', 'roasting tin', 'one tray'
}
_FILTER_BREAKFAST = {
    'egg', 'toast', 'bacon', 'pancake', 'cereal', 'oatmeal', 'oats', 'breakfast', 'muffin', 'croissant'
}
_FILTER_LUNCH = {
    'sandwich', 'wrap', 'soup', 'salad', 'lunch', 'quiche', 'tartine'
}
_FILTER_DINNER = {
    'dinner', 'roast', 'stew', 'curry', 'casserole', 'main', 'entrée', 'entree', 'pasta', 'risotto', 'grilled'
}
_FILTER_SIDE = {
    'side', 'accompaniment', 'salad', 'bread', 'rice', 'potatoes', 'vegetables', 'slaw'
}
_FILTER_SNACK = {
    'snack', 'appetizer', 'starter', 'dip', 'nuts', 'chips', 'crackers', 'hummus', 'bruschetta'
}


def _contains_whole_word(text: str, word: str) -> bool:
    """Return True if word appears as a whole word in text (case-insensitive)."""
    return bool(re.search(r'\b' + re.escape(word.lower()) + r'\b', text.lower()))


def _matches_filter(candidate: dict, name: str) -> bool:
    """Return True if candidate satisfies the named heuristic filter."""
    title = (candidate.get('title') or '').lower()
    ingredients = (candidate.get('ingredients') or '').lower()
    steps = (candidate.get('steps') or '').lower()
    full_text = title + ' ' + ingredients + ' ' + steps

    if name == 'vegetarian':
        return not any(w in full_text for w in _FILTER_MEAT)
    if name == 'vegan':
        return (not any(w in full_text for w in _FILTER_MEAT) and
                not any(w in full_text for w in _FILTER_DAIRY_EGG))
    if name == 'gluten-free':
        # Allow if explicitly marked gluten-free; otherwise exclude gluten grains.
        if 'gluten-free' in full_text or 'gluten free' in full_text:
            return True
        return not any(w in full_text for w in _FILTER_GLUTEN)
    if name == 'nut-free':
        # Allow if explicitly marked nut-free; otherwise exclude nuts.
        if 'nut-free' in full_text or 'nut free' in full_text:
            return True
        return not any(w in full_text for w in _FILTER_NUTS)
    if name == 'dessert':
        return any(w in full_text for w in _FILTER_DESSERT)
    if name == 'one-pot':
        return any(w in steps for w in _FILTER_ONE_POT)
    if name == 'quick':
        # Look for explicit short cooking times in steps, or a short overall method.
        for m in re.finditer(r'(\d+)\s*(?:min|minute|minutes|min)', steps):
            if int(m.group(1)) <= 30:
                return True
        # Very short methods (≤5 steps and ≤600 chars) are likely quick.
        step_lines = [s for s in (candidate.get('steps') or '').splitlines() if s.strip()]
        if len(step_lines) <= 5 and len(steps) <= 600:
            return True
        return False
    if name == 'breakfast':
        return any(w in full_text for w in _FILTER_BREAKFAST)
    if name == 'lunch':
        return any(w in full_text for w in _FILTER_LUNCH)
    if name == 'dinner':
        return any(w in full_text for w in _FILTER_DINNER)
    if name == 'side':
        return any(w in full_text for w in _FILTER_SIDE)
    if name == 'snack':
        return any(w in full_text for w in _FILTER_SNACK)
    return True


# Curated collections shown on the /collections page. Each card links to the
# existing search with a keyword query and/or heuristic filter.
CURATED_COLLECTIONS = [
    {'name': 'Comfort Food', 'icon': '🍲', 'q': 'comfort', 'filters': ''},
    {'name': 'Date Night', 'icon': '🥂', 'q': 'dinner', 'filters': ''},
    {'name': 'Summer BBQ', 'icon': '🔥', 'q': 'barbecue', 'filters': ''},
    {'name': 'Quick Weeknight', 'icon': '⚡', 'q': '', 'filters': 'quick'},
    {'name': 'Batch Cook', 'icon': '🍲', 'q': 'casserole', 'filters': ''},
    {'name': 'One-Pot Wonders', 'icon': '🍳', 'q': '', 'filters': 'one-pot'},
    {'name': 'Vegetarian Favourites', 'icon': '🥬', 'q': '', 'filters': 'vegetarian'},
    {'name': 'Sweet Tooth', 'icon': '🍰', 'q': '', 'filters': 'dessert'},
]


def _levenshtein(a: str, b: str) -> int:
    """Return the Levenshtein distance between two strings."""
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(
                cur[j - 1] + 1,
                prev[j] + 1,
                prev[j - 1] + (0 if ca == cb else 1)
            )
        prev = cur
    return prev[-1]


# In-memory cache of common words from the current database.
_SPELLING_VOCAB: set = set()


def _build_spelling_vocab(db_path: str) -> set:
    """Build a vocabulary of words from recipe titles and ingredients."""
    global _SPELLING_VOCAB
    if _SPELLING_VOCAB:
        return _SPELLING_VOCAB
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    try:
        rows = c.execute("SELECT title, ingredients FROM recipes WHERE title IS NOT NULL OR ingredients IS NOT NULL").fetchall()
    except sqlite3.OperationalError:
        rows = []
    conn.close()
    vocab = set()
    for title, ingredients in rows:
        text = f"{(title or '')} {(ingredients or '')}"
        for word in re.findall(r"[a-zA-Z']+", text.lower()):
            if len(word) > 2:
                vocab.add(word)
    _SPELLING_VOCAB = vocab
    return vocab


def _suggest_correction(q: str, vocab: set) -> str:
    """Suggest a corrected query if the original returns no likely matches."""
    if not vocab or not q:
        return ''
    tokens = q.lower().split()
    suggestions = []
    for token in tokens:
        if not token or token in vocab:
            suggestions.append(token)
            continue
        best = None
        best_score = 999
        for word in vocab:
            dist = _levenshtein(token, word)
            if dist < best_score and dist <= max(1, len(token) // 3):
                best_score = dist
                best = word
        suggestions.append(best or token)
    suggestion = ' '.join(suggestions)
    return suggestion if suggestion != q.lower() else ''


def _query_db(db_path: str, q: str, limit: int = 10, page: int = 1, source: str = None, filters: List[str] = None, sort: str = 'relevance', pantry: str = None, exclude: str = None, have: str = None):
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

    # rank candidates with BM25, then apply positive/negative token and filter chips.
    filters = filters or []
    ranked = rank_recipes(candidates, q, top_n=len(candidates) if not use_fts else limit * page)
    ranked = [c for c in ranked if _candidate_matches(c, q)]
    for f in filters:
        ranked = [c for c in ranked if _matches_filter(c, f)]

    # Exclude ingredients: drop candidates whose title, ingredients, or steps
    # contain any excluded item as a whole word (case-insensitive).
    exclude_items = [e.strip() for e in (exclude or '').split(',') if e.strip()]
    if exclude_items:
        def _is_excluded(c):
            text = ((c.get('title') or '') + ' ' + (c.get('ingredients') or '') + ' ' + (c.get('steps') or '')).lower()
            return any(_contains_whole_word(text, e) for e in exclude_items)
        ranked = [c for c in ranked if not _is_excluded(c)]

    # "What can I make?" mode: sort by percentage of provided ingredients found
    # in the recipe's ingredients. Whole-word, case-insensitive matching.
    have_items = [h.strip() for h in (have or '').split(',') if h.strip()]
    if have_items:
        for c in ranked:
            ing = (c.get('ingredients') or '').lower()
            matches = sum(1 for h in have_items if _contains_whole_word(ing, h))
            c['have_match_count'] = matches
            c['have_total'] = len(have_items)
            c['have_match_pct'] = int(round(100 * matches / len(have_items)))
        # Sort primarily by match percentage, then by existing relevance score.
        ranked.sort(key=lambda c: (c.get('have_match_pct', 0), c.get('score', 0.0) or 0.0), reverse=True)

    # Small pantry boost: bonus for recipes that contain pantry items as whole words.
    pantry_items = [p.strip() for p in (pantry or '').split(',') if p.strip()] if pantry else []
    if pantry_items and sort == 'relevance':
        for c in ranked:
            ing = (c.get('ingredients') or '').lower()
            bonus = sum(0.05 for p in pantry_items if re.search(r'\b' + re.escape(p.lower()) + r'\b', ing))
            c['score'] = (c.get('score', 0.0) or 0.0) + bonus
        ranked.sort(key=lambda c: c['score'], reverse=True)

    # "What can I make?" mode: sort by percentage of provided ingredients found
    # in the recipe's ingredients. Whole-word, case-insensitive matching.
    have_items = [h.strip() for h in (have or '').split(',') if h.strip()]
    if have_items:
        for c in ranked:
            ing = (c.get('ingredients') or '').lower()
            matches = sum(1 for h in have_items if _contains_whole_word(ing, h))
            c['have_match_count'] = matches
            c['have_total'] = len(have_items)
            c['have_match_pct'] = int(round(100 * matches / len(have_items)))
        # Sort primarily by match percentage, then by existing relevance score.
        ranked.sort(key=lambda c: (c.get('have_match_pct', 0), c.get('score', 0.0) or 0.0), reverse=True)

    # apply sort order (have mode overrides to percentage sort)
    sort = (sort or 'relevance').lower()
    if not have_items:
        if sort == 'az':
            ranked.sort(key=lambda c: (c.get('title') or '').lower())
        elif sort == 'recent':
            ranked.sort(key=lambda c: c.get('id', 0), reverse=True)
        elif sort == 'random':
            import random
            random.shuffle(ranked)

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
            'have_match_count': r.get('have_match_count', 0),
            'have_total': r.get('have_total', 0),
            'have_match_pct': r.get('have_match_pct', 0),
        })
    conn.close()
    return results, total


@app.get('/', response_class=HTMLResponse)
def ui(request: Request):
    tmpl = templates.env.get_template('index.html')
    content = tmpl.render(request=request)
    return HTMLResponse(content)


@app.get('/offline', response_class=HTMLResponse)
def offline_page(request: Request):
    tmpl = templates.env.get_template('offline.html')
    content = tmpl.render(request=request)
    return HTMLResponse(content)


@app.get('/collections', response_class=HTMLResponse)
def collections_page(request: Request):
    """Render a curated collections page with links to pre-filtered searches."""
    tmpl = templates.env.get_template('collections.html')
    content = tmpl.render(request=request, collections=CURATED_COLLECTIONS)
    return HTMLResponse(content)


@app.get('/search')
def search(q: str = Query(''),
           db: str = Query('cookster.db'),
           limit: int = Query(10, ge=1, le=100),
           page: int = Query(1, ge=1, le=10000),
           source: str = Query(None),
           filters: str = Query(None),
           sort: str = Query('relevance'),
           pantry: str = Query(None),
           exclude: str = Query(None),
           have: str = Query(None)):
    try:
        db_path = resolve_db_path(db)
    except ValueError as e:
        return JSONResponse({'error': str(e)}, status_code=400)
    if not os.path.exists(db_path):
        return JSONResponse({'error': 'DB not found', 'db': db}, status_code=400)
    filter_list = [f.strip() for f in (filters or '').split(',') if f.strip()]
    results, total = _query_db(db_path, q, limit, page, source=source, filters=filter_list, sort=sort, pantry=pantry, exclude=exclude, have=have)
    pantry_list = [p.strip() for p in (pantry or '').split(',') if p.strip()]
    exclude_list = [e.strip() for e in (exclude or '').split(',') if e.strip()]
    have_list = [h.strip() for h in (have or '').split(',') if h.strip()]
    return {'query': q, 'results': results, 'page': page, 'total': total, 'source': source, 'filters': filter_list, 'sort': sort, 'pantry': pantry_list, 'exclude': exclude_list, 'have': have_list}


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
    source_raw = recipe['source']
    recipe['source'] = _clean_source(recipe['source'])
    image_url = _image_path_to_url(recipe.get('image', ''))
    # Determine whether method steps are already numbered so we can avoid
    # adding duplicate CSS counters.
    step_lines = [s.strip() for s in (recipe.get('steps') or '').split('\n') if s.strip()]
    numbered = sum(1 for s in step_lines if re.match(r'^\d+[\.\)]\s*', s)) if step_lines else 0
    steps_numbered = numbered > len(step_lines) // 2

    tmpl = templates.env.get_template('recipe.html')
    content = tmpl.render(request=request, recipe=recipe, image_url=image_url, source_raw=source_raw,
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


@app.get('/api/suggest-correction')
def suggest_correction(q: str = Query(..., min_length=1), db: str = Query('cookster.db')):
    """Return a spelling correction suggestion for a query, or empty string."""
    try:
        db_path = resolve_db_path(db)
    except ValueError as e:
        return JSONResponse({'error': str(e)}, status_code=400)
    if not os.path.exists(db_path):
        return JSONResponse({'error': 'DB not found', 'db': db}, status_code=400)
    vocab = _build_spelling_vocab(db_path)
    suggestion = _suggest_correction(q, vocab)
    return {'query': q, 'suggestion': suggestion}


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


# Approximate nutrition lookup. Values are kcal per 100 g (or per unit for eggs).
_NUTRITION_CALORIES = {
    'chicken': 165, 'beef': 250, 'pork': 240, 'lamb': 250, 'duck': 340,
    'turkey': 135, 'bacon': 540, 'ham': 145, 'sausage': 300, 'mince': 250,
    'salmon': 200, 'fish': 150, 'cod': 80, 'haddock': 90, 'tuna': 130,
    'prawn': 100, 'prawns': 100, 'shrimp': 100,
    'rice': 130, 'pasta': 130, 'noodle': 130, 'noodles': 130, 'spaghetti': 130,
    'bread': 250, 'flour': 360, 'sugar': 400, 'honey': 300,
    'potato': 80, 'potatoes': 80, 'carrot': 40, 'carrots': 40, 'onion': 40,
    'onions': 40, 'tomato': 20, 'tomatoes': 20, 'garlic': 150, 'ginger': 80,
    'lemon': 30, 'orange': 50, 'apple': 50, 'apples': 50, 'banana': 90, 'bananas': 90,
    'mushroom': 25, 'mushrooms': 25, 'spinach': 25, 'peas': 80, 'beans': 130,
    'chickpea': 160, 'chickpeas': 160, 'lentil': 110, 'lentils': 110,
    'egg': 70, 'eggs': 70, 'milk': 60, 'cheese': 400, 'butter': 700,
    'oil': 900, 'olive oil': 900, 'yogurt': 60, 'yoghurt': 60, 'cream': 340,
    'coconut milk': 230, 'stock': 10, 'water': 0, 'salt': 0, 'pepper': 0,
    'chocolate': 550, 'cocoa': 230, 'vanilla': 0, 'cinnamon': 0, 'nutmeg': 0,
    'oregano': 0, 'basil': 0, 'parsley': 0, 'coriander': 0, 'cumin': 0,
    'paprika': 0, 'chilli': 0, 'chili': 0,
}

_FRACTION_CHARS = {
    '½': 0.5, '¼': 0.25, '¾': 0.75, '⅓': 1/3, '⅔': 2/3,
    '⅛': 0.125, '⅜': 0.375, '⅝': 0.625, '⅞': 0.875,
}

_UNIT_CONVERSIONS = {
    'g': 1.0, 'kg': 1000.0, 'mg': 0.001,
    'ml': 1.0, 'l': 1000.0, 'litre': 1000.0, 'liter': 1000.0,
    'cup': 240.0, 'cups': 240.0,
    'tbsp': 15.0, 'tsp': 5.0,
    'oz': 28.35, 'lb': 453.6, 'lbs': 453.6,
}


def _parse_fraction_quantity(text: str):
    """Return a decimal quantity for the first number/fraction in text, or None."""
    for char, val in _FRACTION_CHARS.items():
        if char in text:
            idx = text.index(char)
            before = text[:idx].strip()
            m = re.search(r'(\d+)\s*$', before)
            whole = int(m.group(1)) if m else 0
            return whole + val
    m = re.search(r'(\d+(?:\.\d+)?)(?:\s*\/\s*(\d+))?', text)
    if not m:
        return None
    val = float(m.group(1))
    if m.group(2):
        val /= float(m.group(2))
    return val


def _parse_unit(text: str) -> str:
    """Return the first known unit found in the ingredient line, or ''."""
    pattern = re.compile(r'\b(litre|liter|kg|ml|g|l|cups|cup|tbsp|tsp|oz|lb|lbs)\b', re.I)
    m = pattern.search(text)
    if not m:
        return ''
    return m.group(1).lower()


def _find_ingredient(text: str) -> str:
    """Return the first known ingredient keyword found in the line, or ''."""
    lower = text.lower()
    # Prefer longer matches first (e.g. 'olive oil' before 'oil').
    for ingredient in sorted(_NUTRITION_CALORIES, key=len, reverse=True):
        if re.search(r'\b' + re.escape(ingredient) + r'\b', lower):
            return ingredient
    return ''


def _calories_for_quantity(qty: float, unit: str, per_100g: float, ingredient: str) -> float:
    """Estimate calories from a quantity, unit, and kcal-per-100-g value."""
    if ingredient in ('egg', 'eggs'):
        if unit in ('', 'unit', 'units', 'pc', 'pcs', 'piece', 'pieces'):
            return qty * per_100g
        if unit in _UNIT_CONVERSIONS:
            return qty * _UNIT_CONVERSIONS[unit] * per_100g / 100
        return 0
    if unit in _UNIT_CONVERSIONS:
        return qty * _UNIT_CONVERSIONS[unit] * per_100g / 100
    # Unknown unit: assume a modest default serving of 100 g.
    return per_100g


def _parse_servings(serves: str) -> int:
    """Extract a number from the serves text; default to 4 if unclear."""
    if not serves:
        return 4
    m = re.search(r'(\d+)', str(serves))
    if m:
        n = int(m.group(1))
        return n if n > 0 else 1
    return 4


def estimate_recipe_calories(recipe: dict) -> int:
    """Best-effort estimate of calories per serving for a recipe."""
    ingredients = (recipe.get('ingredients') or '').split('\n')
    total = 0.0
    for line in ingredients:
        line = line.strip()
        if not line:
            continue
        ingredient = _find_ingredient(line)
        if not ingredient:
            continue
        per_100 = _NUTRITION_CALORIES.get(ingredient, 0)
        if per_100 <= 0:
            continue
        qty = _parse_fraction_quantity(line)
        unit = _parse_unit(line)
        if qty is None:
            # No quantity found: assume a small default for countable items or 100 g.
            if ingredient in ('egg', 'eggs'):
                qty = 1
            else:
                qty = 100
                unit = 'g'
        total += _calories_for_quantity(qty, unit, per_100, ingredient)
    if total <= 0:
        return 0
    servings = _parse_servings(recipe.get('serves', ''))
    return round(total / servings)


@app.get('/api/nutrition/{recipe_id}')
def nutrition_estimate(recipe_id: str, db: str = Query('cookster.db')):
    """Return an approximate calorie estimate per serving for a recipe."""
    try:
        db_path = resolve_db_path(db)
    except ValueError as e:
        return JSONResponse({'error': str(e)}, status_code=400)
    if not os.path.exists(db_path):
        return JSONResponse({'error': 'DB not found', 'db': db}, status_code=400)
    conn = sqlite3.connect(db_path)
    _ensure_schema(conn)
    recipe = _lookup_recipe(conn, recipe_id)
    conn.close()
    if not recipe:
        return JSONResponse({'error': 'recipe not found'}, status_code=404)
    cals = estimate_recipe_calories(recipe)
    return JSONResponse({
        'estimated_calories': cals,
        'note': 'approximate',
    })


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
        # Use the source file's mtime as the "added" date; this survives
        # JSON regenerations and reflects when the book was dropped into books/.
        src_path = os.path.join(BOOKS_ADDED_DIR, raw)
        if not os.path.exists(src_path):
            src_path = os.path.join(BOOKS_DIR, raw)
        added_at = os.path.getmtime(src_path) if os.path.exists(src_path) else 0.0
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
    books = []
    for raw, count in rows:
        if not raw:
            continue
        # Use source file mtime so the list stays correct across re-indexing.
        src_path = os.path.join(BOOKS_ADDED_DIR, raw)
        if not os.path.exists(src_path):
            src_path = os.path.join(BOOKS_DIR, raw)
        added_at = os.path.getmtime(src_path) if os.path.exists(src_path) else 0.0
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
