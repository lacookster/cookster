import json
import os
import re
import shutil
import sqlite3
import tempfile
import hashlib
import time
from typing import List, Dict, Tuple
from urllib.parse import unquote

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import warnings
warnings.filterwarnings("ignore", category=UserWarning)
try:
    from bs4 import XMLParsedAsHTMLWarning
    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
except ImportError:
    pass

try:
    from PIL import Image as PILImage
    _PIL_AVAILABLE = True
except Exception:
    PILImage = None
    _PIL_AVAILABLE = False


RECIPE_KEYS = [r'ingredients?', r'directions?', r'methods?', r'instructions?', r'preparation', r'steps']

# Measurement words that help distinguish ingredient lines from prose.
_MEASURE_WORDS = {
    'gram', 'grams', 'kg', 'ml', 'litre', 'litres', 'liter', 'liters',
    'cup', 'cups', 'tbsp', 'tablespoon', 'tablespoons', 'tsp', 'teaspoon', 'teaspoons',
    'oz', 'ounce', 'ounces', 'lb', 'lbs', 'pound', 'pounds', 'pinch', 'pinches',
    'bunch', 'bunches', 'handful', 'handfuls', 'clove', 'cloves', 'slice', 'slices',
    'piece', 'pieces', 'can', 'cans', 'tin', 'tins', 'pack', 'packs', 'packet', 'packets',
    'bottle', 'bottles', 'stick', 'sticks', 'fillet', 'fillets', 'breast', 'breasts',
}

# Imperative verbs that suggest a step line.
_STEP_VERBS = {
    'preheat', 'heat', 'put', 'place', 'add', 'mix', 'stir', 'cook', 'bake', 'roast',
    'grill', 'fry', 'simmer', 'boil', 'whisk', 'beat', 'pour', 'serve', 'drain', 'season',
    'slice', 'chop', 'dice', 'cut', 'crush', 'mince', 'grate', 'peel', 'remove', 'leave',
    'set', 'cool', 'blend', 'process', 'brush', 'arrange', 'top', 'garnish', 'finish',
    'combine', 'fold', 'spread', 'scatter', 'sprinkle', 'turn', 'return', 'reduce',
    'transfer', 'discard', 'reserve', 'strain', 'sieve', 'make', 'prepare', 'first',
    'meanwhile', 'until', 'while', 'when', 'then', 'tip', 'toss', 'repeat', 'break',
    'lightly', 'roughly', 'finely', 'thinly', 'thickly', 'cover', 'uncover', 'rest',
    'using', 'wipe', 'clean', 'trim', 'halve', 'quarter', 'deseed', 'core', 'squeeze',
}


def create_db(db_path: str):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS recipes (
        id INTEGER PRIMARY KEY,
        title TEXT,
        ingredients TEXT,
        steps TEXT,
        source TEXT,
        file_path TEXT,
        image TEXT,
        stable_id TEXT UNIQUE,
        serves TEXT
    )''')
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_recipes_stable_id ON recipes(stable_id)')
    # Track which source JSON files have been loaded and when, so DB loading can be incremental.
    c.execute('''CREATE TABLE IF NOT EXISTS book_index_log (
        source TEXT PRIMARY KEY,
        slug TEXT,
        json_mtime REAL,
        indexed_at REAL
    )''')
    # Try to create an FTS5 table for full-text search; ignore if unavailable
    try:
        c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS recipes_fts USING fts5(title, ingredients, steps, content='recipes', content_rowid='id')")
    except sqlite3.OperationalError:
        pass
    # WAL mode lets the API keep serving reads while a background indexer writes.
    try:
        c.execute('PRAGMA journal_mode=WAL;')
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()

    # ensure 'image' and 'stable_id' columns exist for older DBs
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cols = [r[1] for r in cur.execute("PRAGMA table_info(recipes)")]
        if 'image' not in cols:
            cur.execute('ALTER TABLE recipes ADD COLUMN image TEXT')
        if 'serves' not in cols:
            cur.execute('ALTER TABLE recipes ADD COLUMN serves TEXT')
        if 'stable_id' not in cols:
            cur.execute('ALTER TABLE recipes ADD COLUMN stable_id TEXT')
            cur.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_recipes_stable_id ON recipes(stable_id)')
        conn.commit()
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _normalize_whitespace(text: str) -> str:
    return ' '.join(text.split())


def _text_lines_from_html(html: str) -> List[str]:
    soup = BeautifulSoup(html, 'lxml')
    text = soup.get_text('\n')
    lines = [ln.strip() for ln in text.splitlines()]
    return [ln for ln in lines if ln]


def _is_all_caps(text: str, strict_component_check: bool = False) -> bool:
    # reject parenthetical production notes like "(THE BRACKETED WEIGHTS...)"
    if text.strip().startswith(('(', '[')):
        return False
    letters = re.findall(r'[A-Za-z]', text)
    if not (len(letters) > 3 and all(c.isupper() for c in letters)):
        return False
    words = re.findall(r'\w+', text)
    # exclude single-word section headings like 'KNIVES', 'FISH', 'SAUCES'
    if len(words) == 1 and len(words[0]) < 10:
        return False
    # exclude common section headings regardless of length
    if len(words) == 1 and words[0].upper() in {
        'INTRODUCTION', 'CONTENTS', 'EQUIPMENT', 'INGREDIENTS', 'INDEX',
        'DEDICATION', 'COPYRIGHT', 'NUTRITION', 'ACKNOWLEDGEMENTS',
        'ACKNOWLEDGMENTS',
    }:
        return False
    # exclude obvious component/subheadings that are not standalone recipes
    component_words = {'ICING', 'GLAZE', 'SAUCE', 'GANACHE', 'FILLING', 'TOPPING',
                       'DECORATION', 'DECORATE', 'MARSHMALLOW', 'CREAM', 'CURD',
                       'PASTE', 'CRUMBLE', 'GREMOLATA', 'SALSA', 'DRESSING',
                       'VINAIGRETTE', 'RELISH', 'CHUTNEY', 'COUSCOUS', 'QUICK',
                       'BUTTER', 'OIL', 'SEEDS', 'PORRIDGE', 'MIX', 'FROSTING',
                       'BUTTERCREAM', 'CARAMEL'}
    first_word = words[0].upper() if words else ''
    last_word = words[-1].upper() if words else ''
    if first_word in component_words:
        return False
    # In books with mixed case titles (Ottolenghi/Sweet), short all-caps
    # headings ending with component words are usually sub-recipes.
    # In all-caps books (Gordon Ramsay/One-pan), these can be real recipes,
    # so only apply the stricter check when sentence-case titles are present.
    if strict_component_check:
        if len(words) <= 5 and (last_word in component_words or last_word in {'SALAD'}):
            return False
    # exclude headings that end with punctuation (e.g. 'IN OUR ROUTINES.')
    if re.search(r'[.:;]$', text):
        return False
    # exclude SERVES/MAKES lines that happen to be all caps
    if _is_serves_line(text):
        return False
    # exclude "TO DECORATE", "TO SERVE", "FOR THE CONFETTI", etc.
    low = text.lower()
    if low.startswith('to ') or low.startswith('for the '):
        return False
    return True


def _is_title_like(text: str, strict_component_check: bool = False) -> bool:
    """Return True if text looks like a recipe title."""
    if not text:
        return False
    text = _normalize_whitespace(text)
    low = text.lower()
    # skip obvious non-titles
    bad_starts = (
        'preheat', 'serve', 'serves', 'using', 'mix', 'add', 'pour', 'place', 'heat',
        'cook', 'bake', 'chop', 'slice', 'for the', 'to serve', 'to finish',
        'method', 'ingredients', 'directions', 'instructions', 'preparation', 'steps',
        '1.', '2.', '3.', '4.', '5.', '6.', '7.', '8.', '9.', 'tbsp', 'tsp',
    )
    if any(low.startswith(bs) for bs in bad_starts):
        return False
    # All-caps titles are common in these EPUBs
    if _is_all_caps(text, strict_component_check=strict_component_check):
        return 3 <= len(text) <= 120
    # Titles don't start with bare quantities/numbers ("3 tbsp..." is an ingredient)
    if re.match(r'^\d', text):
        return False
    # Title Case heuristic
    words = re.findall(r"\w+", text)
    if not words:
        return False
    # all-caps titles are handled by _is_all_caps; don't accept them here too
    if all(w.isupper() for w in words if w.isalpha()):
        return False
    cap_count = sum(1 for w in words if w[0].isupper())
    # Single-word, non-all-caps text like "Salt" or "Butter" is usually an
    # ingredient, not a recipe title. Require at least two words for Title Case.
    # Also require a reasonable length: very short Title Case strings are
    # usually section headings ("Noodles and Pasta") rather than recipe titles.
    if (len(words) > 1 and cap_count / len(words) > 0.5 and 20 <= len(text) <= 100):
        return True
    return False


_SENTENCE_CASE_BAD_STARTS = (
    'for ', 'if ', 'when ', 'while ', 'get ', 'make ', 'place ', 'heat ', 'add ', 'pour ',
    'using ', 'as ', 'i ', 'you ', 'we ', 'this ', 'these ', 'there ', 'there\'s ', 'it ',
    'to ', 'in ', 'on ', 'at ', 'with ', 'from ', 'by ', 'about ', 'after ', 'before ',
    'meanwhile ', 'once ', 'until ', 'although ', 'because ', 'since ', 'so ', 'but ', 'and ',
)


def _looks_like_sentence_case_title(text: str) -> bool:
    """Sentence-case recipe titles like 'Rice noodle salad with...' or
    'Pasta alla Norma'. Weed out descriptions and section headings."""
    if not text:
        return False
    text = _normalize_whitespace(text)
    if not (10 <= len(text) <= 70):
        return False
    low = text.lower()
    if low.endswith('.'):
        return False
    if low.startswith(_SENTENCE_CASE_BAD_STARTS):
        return False
    if _is_ingredient_line(text) or _is_step_line(text) or _is_serves_line(text):
        return False
    # require at least two words and first letter uppercase
    words = re.findall(r'\w+', text)
    if len(words) < 2:
        return False
    if not text[0].isupper():
        return False
    return True


def _is_serves_line(text: str) -> bool:
    low = text.lower().strip()
    return bool(re.match(r'^(serves|makes|yield|yields)(\s+about|\s+around)?\s+(\d|a\s+few|six|eight|ten|four|two|three|five|twelve)', low))


def _extract_serves_from_text(text: str) -> str:
    """Return a SERVES/MAKES/YIELD fragment from a block of text, or ''."""
    if not text:
        return ''
    # Look for a Serves/Makes/Yield line (possibly at the end of a paragraph).
    m = re.search(r'\b(Serves|Makes|Yield|Yields)\s+[^.\n]*', text, re.I)
    if m:
        line = m.group(0).strip()
        if _is_serves_line(line):
            return line
    return ''


_QUANTITY_WORDS = {
    'a', 'an', 'few', 'couple', 'handful', 'pinch', 'dash', 'splash', 'bunch',
    'slice', 'pieces', 'piece', 'clove', 'cloves', 'sprig', 'sprigs', 'knob',
    'thumb-sized', 'small', 'medium', 'large',
}


def _is_ingredient_line(text: str) -> bool:
    """Ingredients often contain a quantity/measurement or are short food lines."""
    text = text.strip()
    if not text:
        return False
    low = text.lower()
    # skip obvious non-ingredients
    if low.startswith('for the ') or low.startswith('to serve') or _is_serves_line(text):
        return False
    if re.match(r'^\d+\.', text):  # numbered step
        return False
    # Starts with a number or fraction
    if re.match(r'^[\d\s¼½¾⅓⅔⅛⅜⅝⅞]', text):
        return True
    # Starts with a quantity word
    if re.match(r'^(' + '|'.join(re.escape(w) for w in _QUANTITY_WORDS) + r')\b', low):
        return True
    words = re.findall(r'\w+', text)
    is_title_case = bool(words) and sum(1 for w in words if w[0].isupper()) / len(words) >= 0.5
    # Contains a measurement word and is not a title/sentence
    has_measure = bool(re.search(r'\b(?:' + '|'.join(re.escape(w) for w in _MEASURE_WORDS) + r')\b', low)) or re.search(r'\b\d+\s*[a-z/]+', low)
    if has_measure and not is_title_case and len(text) <= 80 and ',' not in text:
        return True
    # short standalone food items (e.g. "Olive oil", "Sea salt")
    if len(text) <= 40 and not is_title_case and not any(low.startswith(v) for v in _STEP_VERBS):
        return True
    return False


def _is_step_line(text: str) -> bool:
    text = text.strip()
    if not text:
        return False
    low = text.lower()
    # numbered steps are definitive
    if re.match(r'^\d+[\.\)]\s+', text):
        return True
    # imperative step starting with a known cooking verb
    if any(low.startswith(v) for v in _STEP_VERBS):
        return True
    return False


def _is_subheading(text: str) -> bool:
    low = text.lower().strip()
    return low.startswith('for the ') or low.startswith('to serve') or low.startswith('to finish')


def _is_prose_paragraph(text: str) -> bool:
    """A long description paragraph between title and ingredients."""
    low = text.lower()
    if len(text) > 120 and not _is_ingredient_line(text) and not _is_step_line(text):
        return True
    if re.search(r'[.!?][\s]+[A-Z][a-z]', text):
        return True
    return False


def _extract_paragraph_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Extract recipes where each recipe is a sequence of <p> tags:
       ALL CAPS title (or title-like), optional SERVES line, optional prose,
       ingredient lines, then numbered step lines.
    """
    recipes = []
    paragraphs = [p for p in soup.find_all('p') if p.get_text(strip=True)]
    i = 0
    n = len(paragraphs)

    def _next_text(idx: int) -> str:
        return _normalize_whitespace(paragraphs[idx].get_text(strip=True)) if idx < n else ''

    # Detect whether this document uses sentence-case titles. If it does,
    # short all-caps headings are likely component sub-recipes.
    _has_sentence_case = any(
        _looks_like_sentence_case_title(_next_text(k)) for k in range(n)
    )

    def _is_all_caps_local(text: str) -> bool:
        return _is_all_caps(text, strict_component_check=_has_sentence_case)

    def _is_title_like_local(text: str) -> bool:
        return _is_title_like(text, strict_component_check=_has_sentence_case)

    def _recipe_start_looks_valid(start_idx: int) -> bool:
        """A title is only a recipe start if the next few paragraphs contain a
        serves line or an ingredient line that starts with a quantity. Otherwise
        it's likely a table of contents or list heading."""
        for k in range(start_idx, min(n, start_idx + 5)):
            line = _next_text(k)
            if _is_serves_line(line):
                return True
            if re.match(r'^[\d\s¼½¾⅓⅔⅛⅜⅝⅞]', line) or re.match(r'^(' + '|'.join(re.escape(w) for w in _QUANTITY_WORDS) + r')\b', line.lower()):
                return True
        return False

    while i < n:
        text = _next_text(i)

        # Look for a title-like paragraph. ALL CAPS titles often wrap onto
        # multiple consecutive p tags, so merge adjacent ALL CAPS paragraphs.
        # Also accept a paragraph if a SERVES/MAKES line appears within the
        # next few paragraphs, even if the title is in sentence case.
        title_parts = []
        title_end = i
        if _is_all_caps_local(text):
            title_parts.append(text)
            j = i + 1
            while j < n and _is_all_caps_local(_next_text(j)):
                title_parts.append(_next_text(j))
                j += 1
            title_end = j
        elif _is_title_like_local(text):
            title_parts.append(text)
            title_end = i + 1
        elif (_looks_like_sentence_case_title(text)
              and any(_is_serves_line(_next_text(k)) for k in range(i + 1, min(n, i + 4)))):
            title_parts.append(text)
            title_end = i + 1
        else:
            i += 1
            continue

        title = ' '.join(title_parts)
        # Some EPUBs put title and "SERVES 4" in the same paragraph.
        # Split them out so the serves line is handled separately.
        serves = ''
        m = re.search(r'\b(SERVES|MAKES)\s+\d.*$', title, re.I)
        if m:
            serves = m.group(0)
            title = title[:m.start()].strip()
        # advance i to the paragraph after the title
        i = title_end
        if i >= n:
            break

        # Validate: must look like a real recipe start, not a TOC entry
        if not _recipe_start_looks_valid(i):
            continue

        # Skip any prose/descriptions and pick up the SERVES/MAKES line, which
        # may appear immediately after the title or after a short intro.
        while i < n:
            line = _next_text(i)
            if _is_serves_line(line):
                serves = line
                i += 1
                continue
            if _is_ingredient_line(line) or _is_subheading(line) or _is_step_line(line):
                break
            if _is_title_like_local(line):
                break
            i += 1

        if i >= n:
            break

        # collect ingredients
        ingredients = []
        while i < n:
            line = _next_text(i)
            if _is_step_line(line):
                break
            if _is_title_like_local(line) or _is_serves_line(line):
                break
            if _is_subheading(line):
                ingredients.append(line)
                i += 1
                continue
            if _is_ingredient_line(line):
                ingredients.append(line)
                i += 1
                continue
            # A long, sentence-like line that is not an ingredient probably
            # means we already entered the method; stop collecting.
            if _is_prose_paragraph(line) and ingredients:
                break
            # otherwise skip short junk
            i += 1

        # collect steps
        steps = []
        while i < n:
            line = _next_text(i)
            if _is_title_like_local(line) or _is_serves_line(line):
                break
            if _is_step_line(line):
                steps.append(line)
                i += 1
                continue
            # allow continuation lines between numbered steps (some formats
            # put the whole method in a couple of long paragraphs)
            if steps and len(line) <= 1000 and not _is_ingredient_line(line):
                steps.append(line)
                i += 1
                continue
            if steps and not _is_ingredient_line(line):
                break
            i += 1

        # Quality gate: we need either a couple of real ingredient lines OR a
        # SERVES/MAKES line, plus at least two paragraphs in the method. If
        # there is no SERVES line, at least one of those paragraphs must look
        # like a step (numbered or imperative) to avoid grabbing random prose.
        real_ingredients = [l for l in ingredients if re.match(r'^[\d\s¼½¾⅓⅔⅛⅜⅝⅞]', l) or
                            re.match(r'^(' + '|'.join(re.escape(w) for w in _QUANTITY_WORDS) + r')\b', l.lower())]
        if len(steps) < 2 or (len(real_ingredients) < 2 and not serves):
            continue
        if not serves and not any(_is_step_line(l) for l in steps):
            continue

        # clean up title: strip leading numbering, collapse whitespace
        title = re.sub(r"^[0-9]+[\).\s]+", "", title).strip()
        if not title:
            title = os.path.splitext(os.path.basename(epub_path))[0]

        recipes.append({
            'title': title,
            'ingredients': '\n'.join(ingredients).strip(),
            'steps': '\n'.join(steps).strip(),
            'source': os.path.basename(epub_path),
            'file_path': epub_path,
            'image': '',
            'serves': serves,
        })

    return recipes


def _extract_30min_meals(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Extract the main recipe from Jamie's 30-Minute Meals chapters.

    Each chapter is a complete meal: a main title + side-dish titles, then
    'SERVES N', 'INGREDIENTS', component ingredient lists, 'TO START',
    interleaved method paragraphs and 'TO SERVE'. We extract one recipe
    per chapter using the main title and all ingredients/steps.
    """
    recipes = []
    lines = _text_lines_from_html(str(soup))
    n = len(lines)
    if n < 20:
        return recipes

    def _text(idx: int) -> str:
        return lines[idx] if 0 <= idx < n else ''

    # Find the 'INGREDIENTS' heading; the main title and SERVES line come before it.
    ing_idx = None
    for idx in range(n):
        if re.match(r'^INGREDIENTS\b', _text(idx), re.I):
            ing_idx = idx
            break
    if ing_idx is None or ing_idx < 3:
        return recipes

    # Main title is the first substantial line before the SERVES line.
    serves = ''
    title_idx = None
    for idx in range(ing_idx):
        line = _text(idx)
        if _is_serves_line(line):
            serves = line
            if title_idx is None:
                title_idx = idx - 1 if idx > 0 else 0
            break
        # first real text line becomes the title candidate
        if title_idx is None and line and len(line) >= 3:
            title_idx = idx
    if title_idx is None:
        return recipes

    main_title = _text(title_idx)
    if not main_title or re.match(r'^(SERVES|INGREDIENTS)\b', main_title, re.I):
        return recipes

    # Collect ingredients until a method marker.
    i = ing_idx + 1
    ingredients = []
    while i < n:
        line = _text(i)
        if re.match(r'^(TO START|METHOD|TO SERVE|NUTRITION)\b', line, re.I):
            break
        if line:
            ingredients.append(line)
        i += 1

    # Skip method marker.
    if i < n and re.match(r'^(TO START|METHOD)\b', _text(i), re.I):
        i += 1

    # Collect steps until TO SERVE or end.
    steps = []
    while i < n:
        line = _text(i)
        if re.match(r'^TO SERVE\b', line, re.I):
            steps.append(line)
            i += 1
            continue
        if line and not re.match(r'^INGREDIENTS\b', line, re.I):
            steps.append(line)
        i += 1

    if len(ingredients) < 3 or len(steps) < 2:
        return recipes

    recipes.append({
        'title': main_title,
        'ingredients': '\n'.join(ingredients).strip(),
        'steps': '\n'.join(steps).strip(),
        'source': os.path.basename(epub_path),
        'file_path': epub_path,
        'image': '',
        'serves': serves,
    })
    return recipes


def _extract_superfood_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Extract recipes from Super Food Family Classics.

    Format: ALL CAPS title, optional ALL CAPS subtitle, health blurb,
    'SERVES N', 'XX MINUTES'/'X HOUR...', flat ingredient list, method.
    """
    recipes = []
    lines = _text_lines_from_html(str(soup))
    n = len(lines)
    if n < 8:
        return recipes

    def _text(idx: int) -> str:
        return lines[idx] if 0 <= idx < n else ''

    def _is_all_caps_line(text: str) -> bool:
        letters = re.findall(r'[A-Za-z]', text)
        return len(letters) > 3 and all(c.isupper() for c in letters)

    _SECTION_HEADINGS = {
        'INTRODUCTION', 'CONTENTS', 'INDEX', 'BREAKFAST', 'QUICK FIXES',
        'HEALTHY CLASSICS', 'SALADS', 'CURRIES & STEWS', 'TRAYBAKES',
        'PASTA & RISOTTO', 'SOUPS', 'KITCHEN HACKS', 'HEALTH & HAPPINESS',
        'VEGGIE', 'MEAT', 'FISH', 'DESSERTS', 'FULL RECIPE LIST',
        'SPECIAL DIET-FRIENDLY RECIPES', 'FOR EACH PORTION',
        'SWEET TREATS', 'SMOOTHIES', 'EGGS',
    }

    i = 0
    while i < n:
        line = _text(i)
        # recipe title: all-caps, not a section heading, not time/serves
        if not _is_all_caps_line(line) or len(line) < 4 or len(line) > 120:
            i += 1
            continue
        # reject bare timing lines like '21 MINUTES', '1 HOUR', '2 HOURS 15 MINUTES'
        if re.match(r'^(\d+\s*(MINUTES?|HOURS?)\s*)+\d*\s*(MINUTES?|HOURS?)?$', line, re.I):
            i += 1
            continue
        if any(line.upper().startswith(h) for h in _SECTION_HEADINGS):
            i += 1
            continue

        # absorb optional all-caps subtitle, but only if the following line
        # is not another all-caps title (avoids merging a list of recipes).
        title_parts = [line]
        i += 1
        if i < n and _is_all_caps_line(_text(i)) and 3 <= len(_text(i)) <= 120:
            if i + 1 < n and not _is_all_caps_line(_text(i + 1)):
                title_parts.append(_text(i))
                i += 1
        title = ' '.join(title_parts)

        # skip health blurb until we hit SERVES
        serves = ''
        time_info = ''
        while i < n:
            line = _text(i)
            if _is_serves_line(line):
                serves = line
                i += 1
                continue
            m = re.match(r'^(\d+\s*(MINUTES|HOURS?)|\d+\s+HOUR\s+\d+\s+MINUTES?)$', line, re.I)
            if m:
                time_info = line
                i += 1
                continue
            # ingredient lines usually start with quantity or measurement
            if _is_ingredient_line(line) or (len(line) <= 80 and _is_ingredient_line(line)):
                break
            i += 1

        if i >= n:
            break

        # collect ingredients until a clear method paragraph
        ingredients = []
        while i < n:
            line = _text(i)
            if _is_all_caps_line(line):
                break
            # method paragraphs start with imperative verbs and are usually long
            if (len(line) > 80 and any(line.lower().startswith(v) for v in _STEP_VERBS)) or \
               re.match(r'^Preheat\s+', line, re.I):
                break
            if line:
                ingredients.append(line)
            i += 1

        # collect steps until next all-caps title
        steps = []
        while i < n:
            line = _text(i)
            if _is_all_caps_line(line) and 3 <= len(line) <= 120:
                break
            if line:
                steps.append(line)
            i += 1

        if len(ingredients) < 2 or len(steps) < 1:
            continue

        recipes.append({
            'title': title,
            'ingredients': '\n'.join(ingredients).strip(),
            'steps': '\n'.join(steps).strip(),
            'source': os.path.basename(epub_path),
            'file_path': epub_path,
            'image': '',
            'serves': serves,
        })

    return recipes


def _extract_northern_italy_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Extract recipes from The Classic Food of Northern Italy.

    Recipes are embedded in chapter prose. Each starts with an Italian title
    (often followed by an English subtitle), then 'Serves N', ingredients,
    and method paragraphs, ending at the next recipe title.
    """
    recipes = []
    lines = _text_lines_from_html(str(soup))
    n = len(lines)
    if n < 8:
        return recipes

    def _text(idx: int) -> str:
        return lines[idx] if 0 <= idx < n else ''

    def _looks_like_prose(text: str) -> bool:
        return len(text) > 120 and ('. ' in text or text.endswith('.'))

    i = 0
    while i < n:
        line = _text(i)
        # A recipe title is short (1-5 words), starts with uppercase, and is
        # followed within 3 paragraphs by a serves line or ingredient list.
        if not _looks_like_recipe_title(line):
            i += 1
            continue

        # absorb optional English subtitle on next line
        title_parts = [line]
        j = i + 1
        if j < n:
            next_line = _text(j)
            if 1 <= len(re.findall(r'\w+', next_line)) <= 6 and next_line[0].isupper() and \
               not _is_serves_line(next_line) and not _is_ingredient_line(next_line) and \
               not _looks_like_prose(next_line):
                title_parts.append(next_line)
                j += 1
        title = ' '.join(title_parts)

        # validate by checking for a 'Serves N' line soon; real recipes always
        # have one, glossary terms don't.
        valid = False
        serves = ''
        for k in range(j, min(n, j + 4)):
            cand = _text(k)
            if _is_serves_line(cand):
                valid = True
                serves = cand
                break
        if not valid:
            i += 1
            continue

        i = j
        # skip subtitle/serves lines
        while i < n and (_is_serves_line(_text(i)) or _text(i) in title_parts):
            if _is_serves_line(_text(i)):
                serves = _text(i)
            i += 1
        if i >= n:
            break

        # collect ingredients until method paragraph or next title
        ingredients = []
        while i < n:
            line = _text(i)
            if _looks_like_recipe_title(line):
                break
            if _is_step_line(line) and len(line) > 60 and not _is_ingredient_line(line):
                break
            if line:
                ingredients.append(line)
            i += 1

        # collect steps until next recipe title
        steps = []
        while i < n:
            line = _text(i)
            if _looks_like_recipe_title(line):
                break
            if line:
                steps.append(line)
            i += 1

        if len(ingredients) < 2 or len(steps) < 1:
            continue

        recipes.append({
            'title': title,
            'ingredients': '\n'.join(ingredients).strip(),
            'steps': '\n'.join(steps).strip(),
            'source': os.path.basename(epub_path),
            'file_path': epub_path,
            'image': '',
            'serves': serves,
        })

    return recipes


def _looks_like_recipe_title(text: str) -> bool:
    """Heuristic for prose-embedded Italian recipe titles."""
    text = _normalize_whitespace(text)
    words = re.findall(r'\w+', text)
    if not (1 <= len(words) <= 6 and 4 <= len(text) <= 60 and text[0].isupper()):
        return False
    # real titles are not ALL CAPS in this book; all-caps lines are section headings
    letters = [c for c in text if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        return False
    low = text.lower()
    if low.startswith(('for the ', 'for a ', 'for an ')):
        return False
    if _is_serves_line(text) or _is_ingredient_line(text):
        return False
    if len(text) > 120:
        return False
    # reject section headings and references
    if any(w in low for w in ('larousse', 'gastronomique', 'measures and quantities',
                                'the recipes', 'introduction', 'contents', 'equipment')):
        return False
    if '(' in text and any(w in text.lower() for w in ('equipment', 'serves', 'introduction')):
        return False
    # reject common ingredient-only lines that wrap as standalone titles
    common_ingredients = {'salt', 'pepper', 'oil', 'butter', 'flour', 'sugar', 'water',
                        'milk', 'eggs', 'onions', 'leeks', 'garlic', 'lemon', 'vinegar', 'wine',
                        'nutmeg', 'cinnamon', 'parsley', 'basil', 'grated'}
    if all(w.lower() in common_ingredients for w in words):
        return False
    # reject lines that look like "Salt and freshly ground black pepper"
    if 'freshly ground' in low and 'pepper' in low:
        return False
    return True


def _is_non_cookbook_source(source: str) -> bool:
    """Return True for sources that are reference/parenting/health books, not
    cookbooks. This prevents indexing garbage from non-recipe PDFs.
    """
    low = re.sub(r'[_\-]+', ' ', source.lower())
    non_cookbook_phrases = {
        'complete reference to plant-based nutrition',
        'blue zones',
        'every parent s guide to raising healthy',
        'every parents guide to raising healthy',
        'raising healthy happy kids',
        'lessons for living longer',
        'the complete reference',
    }
    return any(p in low for p in non_cookbook_phrases)


# Phrases that are never recipe titles.
_PDF_TITLE_BLACKLIST = {
    'bibliographical', 'references and index', 'printed in', 'all rights reserved',
    'copyright', 'isbn', 'http', 'www.', '.com', '.co.', '.org', 'llc', 'inc.',
    'company', 'penguin random house', 'ten speed press', 'book publishing',
    'cover and interior design', 'environmental defense', 'paper calculator',
    'printed on recycled', 'green press initiative', 'member of',
}


def _is_pdf_title(text: str) -> bool:
    """Title heuristics tuned for PDF page text.

    Rejects section headings, copyright lines, URLs, and prose fragments.
    Titles may be all-caps (common in cookbooks) or Title/sentence case.
    """
    text = text.strip()
    if not text or text[0].islower():
        return False
    low = text.lower()
    if any(b in low for b in _PDF_TITLE_BLACKLIST):
        return False
    # reject obvious section headings / table of contents entries
    if low.startswith(('soundtrack', 'yield', 'serves', 'makes', 'ingredients',
                       'directions', 'method', 'instructions', 'chapter', 'appendix',
                       'contents', 'index', 'introduction', 'acknowledgment',
                       'acknowledgement', 'conversion charts', 'about the author',
                       'selected bibliography', 'notes', 'resources')):
        return False
    # reject lines that are mostly numbers / punctuation
    letters = re.findall(r'[A-Za-z]', text)
    if not letters or len(letters) / len(text) < 0.5:
        return False
    words = re.findall(r"\w+", text)
    # all-caps titles are often single words (e.g. "BERBERE"); allow them
    is_all_caps = letters and all(c.isupper() for c in letters)
    min_words = 1 if is_all_caps else 2
    if not (min_words <= len(words) <= 12):
        return False
    min_len = 4 if is_all_caps else 8
    if not (min_len <= len(text) <= 90):
        return False
    # reject lines that are clearly ingredient lists (lots of commas)
    if text.count(',') >= 4:
        return False
    # all-caps titles are allowed, but reject all-caps section headings that
    # end with punctuation or contain too many words.
    is_all_caps = letters and all(c.isupper() for c in letters)
    if is_all_caps:
        if text.endswith(('.', '!', '?', ':', ';')):
            return False
        if len(words) > 8:
            return False
        # reject all-caps lines that are mostly a list (lots of commas/ands)
        if text.count(',') + text.lower().count(' and ') >= 3:
            return False
    else:
        # titles don't end with sentence punctuation or contain a full sentence
        if text.endswith(('.', '!', '?', ':', ';')):
            return False
        if '. ' in text or '  ' in text:
            return False
        # must look like Title Case or sentence case (first word capitalized)
        cap_count = sum(1 for w in words if w[0].isupper())
        if cap_count / len(words) < 0.5:
            return False
    if _is_serves_line(text) or _is_ingredient_line(text) or _is_step_line(text):
        return False
    return True


def _has_recipe_marker_after(idx: int, n: int, text_fn) -> Tuple[bool, str]:
    """Return True if a yield/serves line or ingredient line appears soon.

    YIELD/SERVES/MAKES may be on its own line with the amount on the next line,
    so we look up to 8 lines ahead.
    """
    for k in range(idx + 1, min(n, idx + 8)):
        cand = text_fn(k)
        if re.match(r'^(YIELD|SERVES|MAKES)\b', cand, re.I):
            # amount may be on the next line
            amount = ''
            if k + 1 < n:
                nxt = text_fn(k + 1)
                if not _is_pdf_title(nxt) and not _is_ingredient_line(nxt) and not _is_step_line(nxt):
                    amount = nxt
            return True, (cand + (' ' + amount if amount else '')).strip()
        if _is_ingredient_line(cand):
            return True, ''
    return False, ''


def _extract_pdf_recipes(pdf_path: str) -> List[Dict]:
    """Extract recipes from text-based PDF cookbooks.

    PDF page text is usually line-wrapped, so we work with individual lines and
    use a small state machine: title -> yield/serves -> ingredients -> method.
    Images are not extracted from PDFs (set to empty string).
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        return []

    source = os.path.basename(pdf_path)
    if _is_non_cookbook_source(source):
        return []

    try:
        reader = PdfReader(pdf_path)
    except Exception:
        return []

    lines = []
    for page in reader.pages:
        text = page.extract_text()
        if not text:
            continue
        for line in text.splitlines():
            line = _normalize_whitespace(line)
            if line:
                lines.append(line)

    n = len(lines)
    if n < 10:
        return []

    def _text(idx: int) -> str:
        return lines[idx] if 0 <= idx < n else ''

    recipes = []
    i = 0
    while i < n:
        line = _text(i)
        if not _is_pdf_title(line):
            i += 1
            continue

        valid, serves = _has_recipe_marker_after(i, n, _text)
        if not valid:
            i += 1
            continue

        title = line
        j = i + 1
        # skip prose, yield/serves, soundtrack sections until we hit ingredients
        while j < n:
            cand = _text(j)
            if _is_ingredient_line(cand):
                break
            if re.match(r'^(YIELD|SERVES|MAKES)\b', cand, re.I):
                serves = cand
                j += 1
                continue
            # prose / soundtrack / author note: skip long lines
            if len(cand) > 100:
                j += 1
                continue
            j += 1

        if j >= n:
            break

        # collect ingredients (allow wrapped continuation lines that are short
        # and don't start with an imperative verb)
        ingredients = []
        while j < n:
            cand = _text(j)
            if _is_ingredient_line(cand):
                ingredients.append(cand)
                j += 1
                continue
            # stop at first clear method paragraph
            if len(cand) > 80 and any(cand.lower().startswith(v) for v in _STEP_VERBS):
                break
            # short uppercase section headings between ingredients are allowed but not stored
            if len(cand) <= 30 and cand[0].isupper() and not any(cand.lower().startswith(v) for v in _STEP_VERBS):
                j += 1
                continue
            break

        if len(ingredients) < 2:
            i = j if j > i else i + 1
            continue

        # collect steps until next likely recipe title
        steps = []
        while j < n:
            cand = _text(j)
            if _is_pdf_title(cand):
                has_next, _ = _has_recipe_marker_after(j, n, _text)
                if has_next:
                    break
            if cand:
                steps.append(cand)
            j += 1

        # Quality gate: we need real ingredients and at least one real step.
        real_ingredients = [l for l in ingredients if re.match(r'^[\d\s\u00bc\u00bd\u00be\u2153\u2154\u215b\u215c\u215d\u215e]', l) or
                            re.match(r'^(' + '|'.join(re.escape(w) for w in _QUANTITY_WORDS) + r')\b', l.lower())]
        if len(real_ingredients) < 2 or not steps:
            i = j if j > i else i + 1
            continue

        # steps should contain at least one imperative / numbered direction
        if not any(_is_step_line(s) for s in steps):
            i = j if j > i else i + 1
            continue

        recipes.append({
            'title': title,
            'ingredients': '\n'.join(ingredients).strip(),
            'steps': '\n'.join(steps).strip(),
            'source': source,
            'file_path': pdf_path,
            'image': '',
            'serves': serves,
        })

        i = j if j > i else i + 1

    return recipes


def _extract_every_grain_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Extract recipes from Fuchsia Dunlop's 'Every Grain of Rice'.

    Format per page: English title in a calibre3 paragraph, followed by Chinese
    name/characters, a description, ingredients in <blockquote><span.bold>,
    then method steps in calibre17 paragraphs. Variations are marked by a
    bold 'VARIATIONS' paragraph.
    """
    recipes = []
    paragraphs = soup.find_all(['p', 'blockquote'])
    i = 0
    n = len(paragraphs)

    def _para_text(idx: int) -> str:
        return _normalize_whitespace(paragraphs[idx].get_text(' ', strip=True)) if 0 <= idx < n else ''

    while i < n:
        p = paragraphs[i]
        classes = p.get('class', [])
        text = _para_text(i)

        # Title is in the calibre3 paragraph; first substantial all-caps line is the English title.
        if 'calibre3' not in classes or not text:
            i += 1
            continue

        # The English title is the text of the first direct <span> that does
        # not contain the nested Chinese-name span (calibre12).
        title = ''
        for span in p.find_all('span', recursive=False):
            if span.find('span', class_='calibre12'):
                continue
            cand = _normalize_whitespace(span.get_text(' ', strip=True))
            if len(cand) >= 4 and cand[0].isupper():
                title = cand
                break
        if not title:
            # fallback to the first line of flattened text, stripped of CJK characters
            line = text.splitlines()[0] if text else ''
            title = re.sub(r'[\u4e00-\u9fff]', '', line).strip()
        if not title:
            i += 1
            continue

        # Advance to collect ingredients (blockquote with bold text) and steps.
        i += 1
        ingredients = []
        steps = []
        in_variations = False
        while i < n:
            p = paragraphs[i]
            classes = p.get('class', [])
            cls_str = ' '.join(classes)
            text = _para_text(i)
            if not text:
                i += 1
                continue

            # Next recipe title (exact class 'calibre3', not 'calibre34' etc.)
            if 'calibre3' in classes:
                break

            # Variations section marks the end of the main recipe
            if re.match(r'^VARIATIONS?\b', text, re.I):
                in_variations = True
                i += 1
                continue

            # Ingredients live in blockquotes (typically bold spans)
            if p.name == 'blockquote':
                # Skip variation sub-recipe titles within blockquotes
                if in_variations:
                    i += 1
                    continue
                ingredients.append(text)
                i += 1
                continue

            # Method steps are normal paragraphs; skip variation paragraphs.
            if in_variations:
                i += 1
                continue

            # Accept paragraphs that look like prose instructions.
            if len(text) > 20:
                steps.append(text)
                i += 1
                continue

            i += 1

        if len(ingredients) < 2 or len(steps) < 1:
            continue

        recipes.append({
            'title': title,
            'ingredients': '\n'.join(ingredients).strip(),
            'steps': '\n'.join(steps).strip(),
            'source': os.path.basename(epub_path),
            'file_path': epub_path,
            'image': '',
        })

    return recipes


def _extract_one_pan_wonders_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Extract recipes from Jamie Oliver's 'One: Simple One-Pan Wonders'.

    Each recipe page has an ALL CAPS title, an optional subtitle, a SERVES
    line, blockquote ingredients and paragraph method steps.
    """
    recipes = []
    elems = soup.find_all(['p', 'blockquote'])
    n = len(elems)
    i = 0

    def _elem_text(idx: int) -> str:
        return _normalize_whitespace(elems[idx].get_text(' ', strip=True)) if 0 <= idx < n else ''

    while i < n:
        e = elems[i]
        if e.name != 'p':
            i += 1
            continue
        text = _elem_text(i)
        if not (4 <= len(text) <= 120 and _is_all_caps(text)):
            i += 1
            continue
        # Skip section headings rather than recipe titles.
        if text.startswith(('SERVES', 'TOTAL', 'INDEX', 'CONTENTS')) or \
           any(text.startswith(h) for h in ('FRYING PAN', 'TRAYBAKE', 'STIR-FRY', 'SOUP', 'CURRY', 'EGGS', 'SWEET', 'SALAD', 'NOODLE', 'RICE', 'MEAT', 'FISH', 'VEGETABLE')):
            i += 1
            continue

        title = text
        # Absorb an optional subtitle if the next element is a short all-caps p.
        j = i + 1
        while j < n and elems[j].name == 'p':
            sub = _elem_text(j)
            if sub and _is_all_caps(sub) and not sub.startswith(('SERVES', 'TOTAL')) and len(sub) <= 120:
                title += ' ' + sub
                j += 1
            else:
                break

        # Skip any intervening p elements (subtitle, serving line, intro text)
        # until we hit the blockquote ingredient list.
        k = j
        serves = ''
        while k < n and elems[k].name == 'p':
            line = _elem_text(k)
            if 'SERVES' in line or 'TOTAL' in line:
                serves = line
            k += 1
            continue

        # Collect ingredients from blockquotes.
        ingredients = []
        while k < n and elems[k].name == 'blockquote':
            ing = _elem_text(k)
            if ing:
                ingredients.append(ing)
            k += 1

        if len(ingredients) < 2:
            i = k if k > i else i + 1
            continue

        # Collect method steps from following paragraphs until the next title.
        steps = []
        while k < n and elems[k].name == 'p':
            step = _elem_text(k)
            if not step:
                k += 1
                continue
            if _is_all_caps(step) and len(step) > 3 and not step.startswith(('SERVES', 'TOTAL')):
                break
            steps.append(step)
            k += 1

        if len(steps) < 1:
            i = k if k > i else i + 1
            continue

        recipes.append({
            'title': title,
            'ingredients': '\n'.join(ingredients).strip(),
            'steps': '\n'.join(steps).strip(),
            'source': os.path.basename(epub_path),
            'file_path': epub_path,
            'image': '',
            'serves': serves,
        })
        i = k

    return recipes


def _extract_gordon_ramsay_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Extract recipes from Gordon Ramsay's Ultimate Cookery Course.

    Uses the book's specific CSS classes: recipe-head, serving, hang/ingredients,
    and method1.
    """
    recipes = []
    for title_tag in soup.find_all('p', class_='recipe-head'):
        title = _normalize_whitespace(title_tag.get_text(' ', strip=True))
        serves_tag = title_tag.find_next_sibling('p', class_='serving')
        serves = _normalize_whitespace(serves_tag.get_text(' ', strip=True)) if serves_tag else ''

        ingredients = []
        ing_div = title_tag.find_next_sibling('div', class_='hang')
        if ing_div:
            for p in ing_div.find_all('p', class_='ingredients'):
                ing = _normalize_whitespace(p.get_text(' ', strip=True))
                if ing:
                    ingredients.append(ing)

        steps = []
        for p in title_tag.find_all_next('p', class_='method1'):
            # Don't leak into the next recipe on the same page.
            if p.find_previous('p', class_='recipe-head') != title_tag:
                break
            step = _normalize_whitespace(p.get_text(' ', strip=True))
            if step:
                steps.append(step)

        if not ingredients and not steps:
            continue

        recipes.append({
            'title': title,
            'ingredients': '\n'.join(ingredients).strip(),
            'steps': '\n'.join(steps).strip(),
            'source': os.path.basename(epub_path),
            'file_path': epub_path,
            'image': '',
            'serves': serves,
        })
    return recipes


def _looks_like_all_caps_title(text: str) -> bool:
    """Return True for short all-caps phrases that are likely recipe titles."""
    letters = [c for c in text if c.isalpha()]
    words = text.split()
    return (len(letters) > 3 and all(c.isupper() for c in letters)
            and len(words) >= 2 and len(text) > 8)


def _extract_simply_japanese_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Extract recipes from 'Simply Japanese'.

    Recipes use classes rct (main title), ingtc/textt (sub-recipe titles),
    ing/ingt (ingredients), pret (ingredient subheading), pre/pre-1 (serves),
    text/text1 (method), and veg/t1/t2/box content to ignore.
    """
    recipes = []
    cur: Dict = None
    base_title = ''

    def _new_recipe(title: str) -> Dict:
        return {
            'title': title,
            'ingredients': [],
            'steps': [],
            'serves': '',
            'source': os.path.basename(epub_path),
            'file_path': epub_path,
            'image': '',
        }

    def _finalize():
        nonlocal cur
        if cur and cur['ingredients'] and cur['steps']:
            recipes.append(cur)
        cur = None

    for elem in soup.find_all(['p', 'div', 'section']):
        if elem.name == 'div':
            classes = set(elem.get('class', []))
            if classes & {'box', 'full', 'full50', 'full75'}:
                continue
        if elem.name != 'p':
            continue

        classes = set(elem.get('class', []))
        text = _normalize_whitespace(elem.get_text(' ', strip=True))
        if not text:
            continue

        # Ignore auxiliary paragraphs.
        if classes & {'jap', 'rct1', 'caption', 'step', 't1', 't2', 'veg'}:
            continue

        if 'rct' in classes:
            _finalize()
            base_title = text
            cur = _new_recipe(base_title)
            continue

        if cur is None:
            continue

        # Sub-recipe title candidate (e.g. RED MISO OR MAME MISO, WHITE MISO).
        if 'ingtc' in classes or ('ing' in classes and 'ingt' not in classes and _looks_like_all_caps_title(text)):
            if not cur['ingredients'] and not cur['steps']:
                # First sub-recipe under this base title.
                cur['title'] = (base_title + ' - ' + text) if base_title else text
            elif cur['steps']:
                # Previous variant finished; start a new one.
                _finalize()
                cur = _new_recipe((base_title + ' - ' + text) if base_title else text)
            else:
                # Subheading inside the current recipe.
                cur['ingredients'].append('--- ' + text)
            continue

        if 'pret' in classes:
            cur['ingredients'].append('--- ' + text)
            continue

        if 'textt' in classes:
            if cur['steps']:
                _finalize()
                cur = _new_recipe((base_title + ' - ' + text) if base_title else text)
            else:
                cur['ingredients'].append('--- ' + text)
            continue

        if 'pre-1' in classes or 'pre' in classes:
            cur['serves'] += ' ' + text
            continue

        if 'ingt' in classes or 'ing' in classes:
            # Move obvious serving info out of ingredients.
            if _is_serves_line(text):
                cur['serves'] += ' ' + text
            else:
                cur['ingredients'].append(text)
            continue

        if 'text1' in classes or 'text' in classes:
            cur['steps'].append(text)
            continue

    _finalize()
    for r in recipes:
        r['ingredients'] = '\n'.join(r['ingredients']).strip()
        r['steps'] = '\n'.join(r['steps']).strip()
    return recipes


def _extract_plenty_more_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Extract recipes from Yotam Ottolenghi's 'Plenty More'.

    Each recipe is wrapped in a <div class="recipe"> with a
    <h1 class="recipe_title">, an optional <div class="yield">, ingredients as
    <div class="IL_item"> and method steps as <div class="method_step">.
    """
    recipes = []
    for recipe in soup.find_all('div', class_='recipe'):
        title_tag = recipe.find('h1', class_='recipe_title')
        title = _normalize_whitespace(title_tag.get_text(' ', strip=True)) if title_tag else ''
        if not title:
            continue
        yield_tag = recipe.find('div', class_='yield')
        serves = _normalize_whitespace(yield_tag.get_text(' ', strip=True)) if yield_tag else ''
        ingredients = [
            _normalize_whitespace(item.get_text(' ', strip=True))
            for item in recipe.find_all('div', class_='IL_item')
        ]
        steps = [
            _normalize_whitespace(step.get_text(' ', strip=True))
            for step in recipe.find_all('div', class_='method_step')
        ]
        if len(ingredients) < 2 or not steps:
            continue
        recipes.append({
            'title': title,
            'ingredients': '\n'.join(ingredients).strip(),
            'steps': '\n'.join(steps).strip(),
            'source': os.path.basename(epub_path),
            'file_path': epub_path,
            'image': '',
            'serves': serves,
        })
    return recipes


def _extract_flavour_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Extract recipes from Yotam Ottolenghi's 'Flavour'.

    Recipes are split across many small HTML files. A recipe starts with a
    bold <p class="calibre_13"> title, followed by intro paragraphs, a SERVES
    line, ingredients in <ul class="calibre_41"> (and sub-recipe lists in
    <ul class="calibre_46">), and numbered method steps in further
    <p class="calibre_13"> elements.
    """
    recipes = []
    elems = list(soup.find_all(['p', 'ul', 'ol']))
    n = len(elems)
    i = 0

    def _txt(idx: int) -> str:
        return _normalize_whitespace(elems[idx].get_text(' ', strip=True)) if 0 <= idx < n else ''

    while i < n:
        e = elems[i]
        if e.name != 'p' or 'calibre_13' not in (e.get('class') or []):
            i += 1
            continue
        text = _txt(i)
        if not text or re.match(r'^\d', text):
            i += 1
            continue
        bold = e.find('span', class_='bold')
        if not bold:
            i += 1
            continue
        title = _normalize_whitespace(bold.get_text(' ', strip=True))
        if not title or len(title) < 4:
            i += 1
            continue

        ingredients = []
        steps = []
        serves = ''
        j = i + 1
        while j < n:
            ej = elems[j]
            if ej.name == 'p' and 'calibre_13' in (ej.get('class') or []):
                tj = _txt(j)
                if re.match(r'^\d', tj):
                    steps.append(tj)
                    j += 1
                    continue
                # Stop at the next bold title candidate.
                if ej.find('span', class_='bold') and tj.isupper() and len(tj) < 80:
                    break
                # A sub-recipe heading before numbered steps.
                if not steps and tj.isupper() and len(tj) < 80:
                    ingredients.append('--- ' + tj)
                    j += 1
                    continue
            if ej.name in ('ul', 'ol'):
                for li in ej.find_all('li'):
                    ingredients.append(_normalize_whitespace(li.get_text(' ', strip=True)))
                j += 1
                continue
            if ej.name == 'p' and 'calibre_40' in (ej.get('class') or []):
                tj = _txt(j)
                if not serves:
                    serves = tj
                j += 1
                continue
            j += 1

        if len(ingredients) < 2 or not steps:
            i += 1
            continue

        recipes.append({
            'title': title,
            'ingredients': '\n'.join(ingredients).strip(),
            'steps': '\n'.join(steps).strip(),
            'source': os.path.basename(epub_path),
            'file_path': epub_path,
            'image': '',
            'serves': serves,
        })
        i = j

    return recipes


def _extract_plenty_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Extract recipes from Yotam Ottolenghi's 'Plenty' (2011 Italian edition).

    Each recipe lives on its own small HTML page: a <h2 class="section2"> title,
    optional intro paragraphs, a <div class="recipe"> with a 'Per ...' serves
    line, ingredient paragraphs and subheadings, followed by method paragraphs.
    """
    recipes = []
    title_tag = soup.find('h2', class_='section2')
    title = _normalize_whitespace(title_tag.get_text(' ', strip=True)) if title_tag else ''
    recipe_div = soup.find('div', class_='recipe')
    if not recipe_div or not title:
        return recipes

    ingredients = []
    serves = ''
    for p in recipe_div.find_all('p'):
        text = _normalize_whitespace(p.get_text(' ', strip=True))
        if not text:
            continue
        cls = ' '.join(p.get('class', []))
        if 'recipe2' in cls:
            ingredients.append('--- ' + text)
        elif text.lower().startswith('per '):
            serves = text
        else:
            ingredients.append(text)

    steps = []
    for p in recipe_div.find_next_siblings('p'):
        cls = ' '.join(p.get('class', []))
        if 'nonindent' in cls or 'nonindent1' in cls:
            text = _normalize_whitespace(p.get_text(' ', strip=True))
            if text:
                steps.append(text)
        else:
            # Images and page breaks separate recipes; stop at unrelated content.
            if p.name == 'p' and p.find('img'):
                break

    if len(ingredients) < 2 or not steps:
        return recipes

    return [{
        'title': title,
        'ingredients': '\n'.join(ingredients).strip(),
        'steps': '\n'.join(steps).strip(),
        'source': os.path.basename(epub_path),
        'file_path': epub_path,
        'image': '',
        'serves': serves,
    }]


def _extract_veganomicon_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Extract recipes from 'Veganomicon'.

    Each chapter has one main recipe: a <div class="recipe"> preceded by a
    <h1 class="chapter-title">/<h2 class="chapter-subtitle"> (or
    chapter-subtitlea) title pair, a <p class="yield">, ingredients in
    <div class="ingredients"> paragraphs and steps in <div class="procedure">.
    """
    recipes = []
    for recipe in soup.find_all('div', class_='recipe'):
        h1 = recipe.find_previous('h1', class_='chapter-title')
        # Some chapters use chapter-subtitlea rather than chapter-subtitle.
        h2 = (recipe.find_previous('h2', class_='chapter-subtitle') or
              recipe.find_previous('h2', class_='chapter-subtitlea'))
        parts = []
        if h1:
            parts.append(_normalize_whitespace(h1.get_text(' ', strip=True)))
        if h2:
            parts.append(_normalize_whitespace(h2.get_text(' ', strip=True)))
        title = ' '.join(p for p in parts if p)
        if not title:
            continue

        yield_tag = recipe.find('p', class_='yield')
        serves = _normalize_whitespace(yield_tag.get_text(' ', strip=True)) if yield_tag else ''

        ingredients = [
            _normalize_whitespace(ing.get_text(' ', strip=True))
            for ing in recipe.find_all('p', class_='ingredient')
            if _normalize_whitespace(ing.get_text(' ', strip=True))
        ]
        steps = [
            _normalize_whitespace(step.get_text(' ', strip=True))
            for step in recipe.find_all(['p', 'div'], class_=['step', 'step-nl'])
            if _normalize_whitespace(step.get_text(' ', strip=True))
        ]

        if len(ingredients) < 2 or not steps:
            continue

        recipes.append({
            'title': title,
            'ingredients': '\n'.join(ingredients).strip(),
            'steps': '\n'.join(steps).strip(),
            'source': os.path.basename(epub_path),
            'file_path': epub_path,
            'image': '',
            'serves': serves,
        })
    return recipes


def _extract_french_provincial_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Best-effort extraction for Elizabeth David's 'French Provincial Cooking'.

    Recipes are written in prose and marked by ALL CAPS <h1> (French) and <h2>
    (English) title pairs, followed by body text in <div class="tx">/
    <div class="tx1"> paragraphs. We combine both titles and split the following
    body into ingredient lines (anything that starts with a quantity or a
    measurement word) and step sentences. Results are unavoidably approximate
    because the book interleaves ingredients and method in continuous prose.
    """
    recipes = []
    elems = list(soup.find_all(['h1', 'h2', 'h3', 'p', 'div']))
    n = len(elems)

    def _elem_text(idx: int) -> str:
        return _normalize_whitespace(elems[idx].get_text(' ', strip=True)) if 0 <= idx < n else ''

    _RECIPE_KEYWORDS = {
        'sauce', 'soups', 'soupe', 'salad', 'salade', 'tart', 'tarte', 'cake',
        'gateau', 'gâteau', 'omelette', 'soufflé', 'souffle', 'gratin',
        'casserole', 'stew', 'roast', 'bread', 'pastry', 'custard', 'cream',
        'ice', 'sorbet', 'compote', 'preserve', 'pudding', 'mousse', 'terrine',
        'pate', 'pâté', 'quiche', 'flan', 'crepes', 'crêpes', 'croquettes',
        'fritters', 'stuffing', 'stuffed', 'braised', 'baked', 'grilled',
        'fried', 'poached', 'steamed', 'stewed', 'marinated', 'ragout', 'navarin',
        'blanquette', 'daube', 'vin', 'purée', 'puree', 'jam', 'jelly', 'confit',
        'rillettes', 'potage', 'brioche', 'fondue', 'croûte', 'crostini', 'pain',
        'mayonnaise', 'vinaigrette', 'garnish', 'glacé', 'fricassée', 'civet',
        'estouffade', 'matelote', 'bouillabaisse', 'bisque', 'consommé',
        'velouté', 'béchamel', 'hollandaise', 'béarnaise', 'ravigote',
        'rémoulade', 'gribiche', 'provençale', 'catalane', 'soubise', 'tomate',
        'oseille', 'raifort', 'beurre', 'butter', 'marmelade', 'confiture',
        'croquembouche', 'punch', 'syrup', 'soufflés', 'gâteau', 'galettes',
        'crumble', 'charlotte', 'parfait', 'bavarois', 'clafoutis', 'tatin',
        'madeleines', 'meringue', 'macaroons', 'biscuits', 'sablés', 'wafers',
        'crackers', 'croûtons', 'toast', 'sandwich', 'canapés', 'hors',
        'entrée', 'entree', 'dessert', 'sweet', 'savoury', 'savory', 'paté',
        'liver', 'kidney', 'brains', 'sweetbreads', 'tripe', 'sausage',
        'ham', 'bacon', 'pork', 'lamb', 'mutton', 'beef', 'veal', 'chicken',
        'duck', 'goose', 'turkey', 'pigeon', 'quail', 'partridge', 'pheasant',
        'hare', 'rabbit', 'venison', 'boar', 'fish', 'sole', 'cod', 'hake',
        'haddock', 'mackerel', 'salmon', 'trout', 'tuna', 'sardines',
        'anchovies', 'mussels', 'oysters', 'scallops', 'prawns', 'shrimps',
        'lobster', 'crab', 'crayfish', 'vegetables', 'potatoes', 'beans',
        'lentils', 'peas', 'asparagus', 'artichokes', 'mushrooms', 'tomatoes',
        'aubergines', 'courgettes', 'spinach', 'cabbage', 'leeks', 'onions',
        'garlic', 'celery', 'carrots', 'turnips', 'beetroot', 'parsnips',
        'salsify', 'endive', 'chicory', 'radishes', 'cucumber', 'lettuce',
        'watercress', 'sorrel', 'purslane', 'tarragon', 'parsley', 'chervil',
        'basil', 'thyme', 'bay', 'rosemary', 'sage', 'mint', 'dill', 'fennel',
        'chives', 'shallots', 'capers', 'olives', 'truffles', 'morels',
        'porcini', 'chanterelles', 'chestnuts', 'walnuts', 'almonds', 'hazelnuts',
        'pistachios', 'pinenuts', 'raisins', 'prunes', 'apricots', 'peaches',
        'pears', 'apples', 'cherries', 'plums', 'figs', 'oranges', 'lemons',
        'grapes', 'strawberries', 'raspberries', 'blackberries', 'currants',
        'gooseberries', 'melon', 'rhubarb', 'quinces', 'pumpkin', 'marrows',
        'squash', 'corn', 'maize', 'rice', 'pasta', 'noodles', 'polenta',
        'semolina', 'couscous', 'buckwheat', 'barley', 'oats', 'millet',
        'eggs', 'milk', 'cream', 'cheese', 'yoghurt', 'butter', 'lard',
        'suet', 'oil', 'vinegar', 'mustard', 'honey', 'sugar', 'salt',
        'pepper', 'spices', 'herbs', 'wine', 'brandy', 'cognac', 'rum',
    }

    _SECTION_HEADINGS = {
        'INTRODUCTION', 'CONTENTS', 'EQUIPMENT', 'INDEX', 'BIBLIOGRAPHY',
        'ACKNOWLEDGEMENTS', 'ACKNOWLEDGMENTS', 'NOTE', 'NOTES', 'PREFACE',
        'FOREWORD', 'CHAPTER', 'APPENDIX', 'GLOSSARY',
    }

    _NEGATIVE_TITLE_FRAGMENTS = {
        'for the kitchen', 'for the household', 'shooting week-end', 'shooting weekend',
        'the theory of', 'a theory of', 'the history of', 'some notes on',
    }

    def _is_title(text: str) -> bool:
        if not text or len(text) < 4 or len(text) > 100:
            return False
        upper = text.upper()
        if upper in _SECTION_HEADINGS:
            return False
        low = text.lower()
        if any(neg in low for neg in _NEGATIVE_TITLE_FRAGMENTS):
            return False
        # Allow all-caps titles including accented characters (e.g. SAUCE BÉCHAMEL).
        alpha = [c for c in text if c.isalpha()]
        if len(alpha) > 3 and all(c.isupper() for c in alpha):
            return any(kw in low for kw in _RECIPE_KEYWORDS)
        return False

    def _is_body_div(elem) -> bool:
        if elem.name != 'div':
            return False
        cls = ' '.join(elem.get('class', []))
        return cls.startswith('tx')

    quantity_pattern = re.compile(
        r"^(?:\d|[\u00bc\u00bd\u00be\u2153\u2154\u215b\u215c\u215d\u215e]|"
        r"a\s+few|a\s+little|a\s+small|one|two|three|four|five|six|seven|eight|nine|ten|"
        r"about\s+\d|roughly\s+\d)"
    )

    i = 0
    while i < n:
        e = elems[i]
        if e.name not in ('h1', 'h2'):
            i += 1
            continue
        text = _elem_text(i)
        if not _is_title(text):
            i += 1
            continue

        # Build title from h1/h2 pair if h2 immediately follows h1.
        title_parts = [text]
        if e.name == 'h1' and i + 1 < n and elems[i + 1].name == 'h2':
            sub = _elem_text(i + 1)
            if _is_title(sub):
                title_parts.append(sub)
                i += 1
        title = ' – '.join(title_parts)

        # Collect following body paragraphs until the next likely title.
        body_parts = []
        j = i + 1
        while j < n:
            ej = elems[j]
            tj = _elem_text(j)
            if not tj:
                j += 1
                continue
            if ej.name in ('h1', 'h2') and _is_title(tj):
                break
            if ej.name == 'p' or _is_body_div(ej):
                body_parts.append(tj)
            j += 1

        if len(body_parts) < 1:
            i = j if j > i else i + 1
            continue

        # Split body into ingredient lines and step sentences. A sentence is
        # treated as an ingredient if it contains a digit/fraction/measurement
        # word anywhere; remaining sentences are treated as steps.
        ingredient_hint = re.compile(
            r'(?:\d|[\u00bc\u00bd\u00be\u2153\u2154\u215b\u215c\u215d\u215e]|'
            r'\b(?:oz|ounce|ounces|lb|lbs|pound|pounds|g|kg|ml|l|litre|litres|liter|liters|'
            r'cup|cups|tbsp|tablespoon|tablespoons|tsp|teaspoon|teaspoons|pinch|pinches|'
            r'glass|glasses|bottle|bottles|drop|drops|inch|inches|cm|degree|degrees|'
            r'handful|handfuls|bunch|bunches|clove|cloves|slice|slices|piece|pieces|'
            r'stick|sticks|fillet|fillets|can|cans|tin|tins|packet|packets|pack|packs)\b)'
        )
        ingredients = []
        steps = []
        for para in body_parts:
            for sentence in re.split(r'(?<=[.!?])\s+', para):
                sentence = sentence.strip()
                if not sentence:
                    continue
                if ingredient_hint.search(sentence):
                    ingredients.append(sentence)
                else:
                    steps.append(sentence)

        if len(ingredients) < 1 or len(steps) < 1:
            i = j if j > i else i + 1
            continue

        recipes.append({
            'title': title,
            'ingredients': '\n'.join(ingredients).strip(),
            'steps': '\n'.join(steps).strip(),
            'source': os.path.basename(epub_path),
            'file_path': epub_path,
            'image': '',
            'serves': '',
        })
        i = j

    return recipes


def _extract_delias_cakes_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Extract recipes from Delia's Cakes.

    Each recipe starts with a <p class="recipe-head"> title, followed by an
    optional intro (<p class="text-intro">), ingredient lines in
    recipe-text/recipe-text-top/recipe-text-bottom paragraphs, and method
    paragraphs in <p class="text-center"> (or a leading pre-heat paragraph).
    """
    recipes = []
    heads = soup.find_all('p', class_='recipe-head')
    for idx, head in enumerate(heads):
        title = _normalize_whitespace(head.get_text(' ', strip=True))
        if not title or len(title) < 3:
            continue

        ingredients = []
        steps = []
        in_steps = False

        for sib in head.find_next_siblings():
            cls = ' '.join(sib.get('class', []))
            # stop at the next recipe
            if sib.name == 'p' and 'recipe-head' in cls:
                break
            text = _normalize_whitespace(sib.get_text(' ', strip=True))
            if not text:
                continue
            # skip intro blurb
            if 'text-intro' in cls:
                continue
            # method paragraphs are text-center, or a leading pre-heat line
            if 'text-center' in cls:
                steps.append(text)
                in_steps = True
                continue
            # Once we see a recipe-text-bottom that starts like an instruction,
            # treat everything afterwards as steps.
            if 'recipe-text-bottom' in cls and in_steps:
                steps.append(text)
                continue
            # Ingredient lines live in recipe-text* paragraphs
            if any(c in cls for c in ('recipe-text', 'recipe-text-top', 'recipe-text-bottom')):
                low = text.lower()
                # subheadings like "For the filling:" or "To finish:"
                if low.startswith(('for the', 'to finish:')) or text.endswith(':'):
                    ingredients.append('--- ' + text)
                    continue
                # A lone "Preheat..." paragraph is the first method step.
                if re.match(r'^pre-?heat\s+', low) or \
                   (len(text) > 30 and any(low.startswith(v) for v in _STEP_VERBS) and
                    not _is_ingredient_line(text)):
                    in_steps = True
                    steps.append(text)
                    continue
                ingredients.append(text)
                continue

        if len(ingredients) < 2 or len(steps) < 1:
            continue

        recipes.append({
            'title': title,
            'ingredients': '\n'.join(ingredients).strip(),
            'steps': '\n'.join(steps).strip(),
            'source': os.path.basename(epub_path),
            'file_path': epub_path,
            'image': '',
        })

    return recipes


def _extract_good_things_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Best-effort extraction for Jane Grigson's 'Good Things'.

    Chapters begin with an <h1> title; individual recipes are <h2> headings
    (often italic French). Ingredients and method are embedded in paragraphs
    without semantic classes. We treat quantity-led paragraphs as ingredients
    and imperative-led paragraphs as steps.
    """
    recipes = []
    for h2 in soup.find_all('h2'):
        title = _normalize_whitespace(h2.get_text(' ', strip=True))
        if not title or len(title) < 4 or len(title) > 100:
            continue
        # Skip chapter/section headings (single short word, all-uppercase notes).
        words = title.split()
        if len(words) == 1 and len(title) < 12:
            continue
        if title.lower().startswith(('note', 'introduction', 'contents')):
            continue

        body = []
        for sib in h2.find_next_siblings():
            if sib.name in ('h1', 'h2'):
                break
            if sib.name != 'p':
                continue
            text = _normalize_whitespace(sib.get_text(' ', strip=True))
            if text:
                body.append(text)

        if len(body) < 3:
            continue

        # First body paragraph is often prose context; keep it as a step if it
        # doesn't look like an ingredient.
        ingredients = []
        steps = []
        for para in body:
            if _is_ingredient_line(para):
                ingredients.append(para)
            elif _is_step_line(para) or len(para) > 60:
                steps.append(para)
            else:
                # short, ambiguous lines are usually ingredients
                ingredients.append(para)

        if len(ingredients) < 2 or len(steps) < 1:
            continue

        recipes.append({
            'title': title,
            'ingredients': '\n'.join(ingredients).strip(),
            'steps': '\n'.join(steps).strip(),
            'source': os.path.basename(epub_path),
            'file_path': epub_path,
            'image': '',
        })

    return recipes


def _extract_everyday_super_food_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Extract recipes from Jamie Oliver's 'Everyday Super Food'.

    Each recipe page has an <h2> title, an optional health blurb in a
    .jamie_super_food_recipe_intro div, an <aside class="sidebar_wrapper">
    with a serves/time heading and ingredient paragraphs, and a
    <div class="maincontent_wrapper"> with method paragraphs.
    """
    recipes = []
    for title_tag in soup.find_all('h2'):
        title = _normalize_whitespace(title_tag.get_text(' ', strip=True))
        if not title or len(title) < 4 or len(title) > 120:
            continue
        # Section headings are short and lack a sidebar/maincontent wrapper nearby.
        sidebar = title_tag.find_next_sibling('aside', class_='sidebar_wrapper')
        if not sidebar:
            sidebar = title_tag.find_next('aside', class_='sidebar_wrapper')
        maincontent = title_tag.find_next_sibling('div', class_='maincontent_wrapper')
        if not maincontent:
            maincontent = title_tag.find_next('div', class_='maincontent_wrapper')
        if not (sidebar and maincontent):
            continue

        serves = ''
        h5 = sidebar.find('h5')
        if h5:
            serves = _normalize_whitespace(h5.get_text(' ', strip=True))

        ingredients = []
        for p in sidebar.find_all('p'):
            ing = _normalize_whitespace(p.get_text(' ', strip=True))
            if ing:
                ingredients.append(ing)

        steps = []
        for p in maincontent.find_all('p'):
            step = _normalize_whitespace(p.get_text(' ', strip=True))
            if step:
                steps.append(step)

        if len(ingredients) < 2 or len(steps) < 1:
            continue

        recipes.append({
            'title': title,
            'ingredients': '\n'.join(ingredients).strip(),
            'steps': '\n'.join(steps).strip(),
            'source': os.path.basename(epub_path),
            'file_path': epub_path,
            'image': '',
            'serves': serves,
        })

    return recipes


def _extract_jamie_veg_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Extract recipes from Jamie Oliver's 'Veg'.

    A recipe section has <h2 class="rec_head1"> title, an optional
    <h2 class="rec_subhead"> subtitle, <h5 class="serves">, a
    <section class="sidebar_wrapper"> with <ul class="ingredient_items">
    ingredient lines, and a <section class="maincontent_wrapper"> with
    method paragraphs.
    """
    recipes = []
    for title_tag in soup.find_all('h2', class_='rec_head1'):
        title = _normalize_whitespace(title_tag.get_text(' ', strip=True))
        if not title:
            continue
        subhead = title_tag.find_next_sibling('h2', class_='rec_subhead')
        if subhead:
            title += ' – ' + _normalize_whitespace(subhead.get_text(' ', strip=True))

        serves_tag = title_tag.find_next_sibling('h5', class_='serves')
        serves = _normalize_whitespace(serves_tag.get_text(' ', strip=True)) if serves_tag else ''

        sidebar = title_tag.find_next_sibling('section', class_='sidebar_wrapper')
        maincontent = title_tag.find_next_sibling('section', class_='maincontent_wrapper')
        if not (sidebar and maincontent):
            continue

        ingredients = []
        for li in sidebar.find_all('li'):
            ing = _normalize_whitespace(li.get_text(' ', strip=True))
            if ing:
                ingredients.append(ing)

        steps = []
        for p in maincontent.find_all('p'):
            step = _normalize_whitespace(p.get_text(' ', strip=True))
            if step:
                steps.append(step)

        if len(ingredients) < 2 or len(steps) < 1:
            continue

        recipes.append({
            'title': title,
            'ingredients': '\n'.join(ingredients).strip(),
            'steps': '\n'.join(steps).strip(),
            'source': os.path.basename(epub_path),
            'file_path': epub_path,
            'image': '',
            'serves': serves,
        })

    return recipes


def _extract_seven_fires_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Extract recipes from Francis Mallmann's 'Seven Fires'.

    Recipe pages use <p class="RH"> for the title, <p class="RHN"> for the
    subtitle/serves line, <p class="RI-M">/<p class="RI-L"> for ingredients,
    and <p class="TX">/<p class="TX1"> for method steps.
    """
    recipes = []
    for title_tag in soup.find_all('p', class_='RH'):
        title = _normalize_whitespace(title_tag.get_text(' ', strip=True))
        if not title or len(title) < 4 or len(title) > 120:
            continue

        serves = ''
        sub_tag = title_tag.find_next_sibling('p', class_='RHN')
        if sub_tag:
            serves = _normalize_whitespace(sub_tag.get_text(' ', strip=True))

        ingredients = []
        steps = []
        for sib in title_tag.find_next_siblings():
            classes = sib.get('class', [])
            cls_str = ' '.join(classes)
            # Stop at the next recipe title (exact class 'RH', not 'RHN').
            if sib.name == 'p' and 'RH' in classes:
                break
            text = _normalize_whitespace(sib.get_text(' ', strip=True))
            if not text:
                continue
            if any(c.startswith('RI-') for c in classes):
                ingredients.append(text)
            elif any(c.startswith('TX') for c in classes):
                steps.append(text)
            elif 'RHN' in classes:
                serves += ' ' + text

        if len(ingredients) < 2 or len(steps) < 1:
            continue

        recipes.append({
            'title': title,
            'ingredients': '\n'.join(ingredients).strip(),
            'steps': '\n'.join(steps).strip(),
            'source': os.path.basename(epub_path),
            'file_path': epub_path,
            'image': '',
            'serves': serves.strip(),
        })

    return recipes


def _extract_cocolat_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Extract recipes from Alice Medrich's 'Cocolat'.

    Recipe titles are <h1 class="h1"> (chapter titles use <h1 class="h1p">).
    They are followed by a serves line in <p class="bkauthor">, an optional
    note in <p class="extract">, an "Ingredients:" block, numbered method
    steps, and optional "Special Equipment" lines.
    """
    recipes = []
    for title_tag in soup.find_all('h1', class_='h1'):
        classes = title_tag.get('class', [])
        if 'h1p' in classes:
            continue
        title = _normalize_whitespace(title_tag.get_text(' ', strip=True))
        if not title or len(title) < 4 or len(title) > 100:
            continue

        serves = ''
        note = ''
        ingredients = []
        steps = []
        in_ingredients = False
        in_steps = False

        for sib in title_tag.find_next_siblings():
            if sib.name == 'h1' and 'h1' in sib.get('class', []):
                break
            classes = sib.get('class', [])
            cls_set = set(classes)
            text = _normalize_whitespace(sib.get_text(' ', strip=True))
            if not text:
                continue

            if sib.name == 'h3':
                # Notes/variations stop the recipe once we have started the method.
                if in_steps:
                    break
                continue

            if 'bkauthor' in classes:
                serves = text
                continue
            if 'extract' in cls_set:
                note = text
                continue

            # The "Ingredients:" marker itself is not useful text.
            if re.match(r'^ingredients:?\s*$', text, re.I):
                in_ingredients = True
                in_steps = False
                continue

            # Numbered steps mark the start of the method.
            if re.match(r'^\d+[\.\)]\s+', text):
                in_ingredients = False
                in_steps = True
                steps.append(text)
                continue

            if in_steps:
                steps.append(text)
            elif in_ingredients or _is_ingredient_line(text) or text.endswith(':'):
                in_ingredients = True
                # Keep subheadings like "Special Equipment:" or "For the ...:"
                if text.endswith(':') and not _is_ingredient_line(text):
                    ingredients.append('--- ' + text)
                else:
                    ingredients.append(text)
            else:
                # Prose that appears before numbered steps; include as note/step.
                if note:
                    note += ' ' + text
                else:
                    note = text

        if note and not steps:
            # Some recipes have entirely prose methods; keep them as steps.
            steps.append(note)

        if len(ingredients) < 2 or len(steps) < 1:
            continue

        recipes.append({
            'title': title,
            'ingredients': '\n'.join(ingredients).strip(),
            'steps': '\n'.join(steps).strip(),
            'source': os.path.basename(epub_path),
            'file_path': epub_path,
            'image': '',
            'serves': serves,
        })

    return recipes


def _extract_kitchen_diaries_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Extract recipes from Nigel Slater's 'The Kitchen Diaries'.

    Recipe titles are <h2 class="subhead2"> headings inside the diary pages.
    Ingredients live in <div class="recp"> (which may contain <p class="recp_txt">),
    method in the following <p class="none"> and <p class="normal"> paragraphs.
    <h2 class="subhead1"> marks diary date entries and stops a recipe.
    """
    recipes = []
    for title_tag in soup.find_all('h2'):
        cls = ' '.join(title_tag.get('class', []))
        if 'subhead2' not in cls:
            continue
        title = _normalize_whitespace(title_tag.get_text(' ', strip=True))
        if not title or len(title) < 4 or len(title) > 120:
            continue

        ingredients = []
        steps = []
        serves = ''

        for sib in title_tag.find_next_siblings():
            if sib.name == 'h2':
                break
            sib_cls = ' '.join(sib.get('class', []))
            text = _normalize_whitespace(sib.get_text(' ', strip=True))
            if not text:
                continue

            if 'recp' in sib_cls:
                # The div may already be split into recp_txt paragraphs, or it
                # may be a single block of text.
                txt_paras = sib.find_all('p', class_='recp_txt')
                if txt_paras:
                    for p in txt_paras:
                        line = _normalize_whitespace(p.get_text(' ', strip=True))
                        if line:
                            ingredients.append(line)
                else:
                    # split on the bullet character used in the book
                    for line in re.split(r'\s*[•·]\s+', text):
                        line = line.strip()
                        if line:
                            ingredients.append(line)
                continue

            if sib.name == 'p':
                low = text.lower()
                if low.startswith('enough for') or low.startswith('serves'):
                    serves += ' ' + text
                    continue
                # First paragraph after ingredients is usually the first step.
                if _is_step_line(text) or len(text) > 50:
                    steps.append(text)
                elif _is_ingredient_line(text):
                    ingredients.append(text)
                else:
                    steps.append(text)

        if len(ingredients) < 2 or len(steps) < 1:
            continue

        recipes.append({
            'title': title,
            'ingredients': '\n'.join(ingredients).strip(),
            'steps': '\n'.join(steps).strip(),
            'source': os.path.basename(epub_path),
            'file_path': epub_path,
            'image': '',
            'serves': serves.strip(),
        })

    return recipes


def _extract_nigella_how_to_eat_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Extract recipes from Nigella Lawson's 'How to Eat'.

    Recipes are marked by <h2 class="recipes-head"> titles. Ingredients follow
    in <p class="recipes-para"> / <p class="recipes-para1"> / <p class="recipes-paraa">
    paragraphs; method steps are in <p class="flush-lefts"> / <p class="indenteds">
    paragraphs. Chapter/section headings use <h2 class="chapter-head">.
    """
    recipes = []
    for title_tag in soup.find_all('h2', class_='recipes-head'):
        title = _normalize_whitespace(title_tag.get_text(' ', strip=True))
        if not title or len(title) < 4 or len(title) > 120:
            continue

        ingredients = []
        steps = []
        serves = ''

        for sib in title_tag.find_next_siblings():
            if sib.name == 'h2':
                cls = ' '.join(sib.get('class', []))
                if 'recipes-head' in cls or 'chapter-head' in cls:
                    break
            if sib.name != 'p':
                continue
            cls = ' '.join(sib.get('class', []))
            text = _normalize_whitespace(sib.get_text(' ', strip=True))
            if not text:
                continue

            if any(c in cls for c in ('recipes-para', 'recipes-para1', 'recipes-paraa')):
                # Some ingredient paragraphs contain section subheadings like
                # "CHICKEN WITH MORELS" that are not ingredients.
                if text.isupper() and len(text) < 60:
                    ingredients.append('--- ' + text)
                else:
                    ingredients.append(text)
            elif any(c in cls for c in ('flush-lefts', 'indenteds', 'indented', 'flush-left')):
                if _is_serves_line(text):
                    serves += ' ' + text
                else:
                    steps.append(text)
            else:
                # Ambiguous trailing paragraphs; keep if they look like steps.
                if _is_step_line(text):
                    steps.append(text)

        if len(ingredients) < 2 or len(steps) < 1:
            continue

        recipes.append({
            'title': title,
            'ingredients': '\n'.join(ingredients).strip(),
            'steps': '\n'.join(steps).strip(),
            'source': os.path.basename(epub_path),
            'file_path': epub_path,
            'image': '',
            'serves': serves.strip(),
        })

    return recipes


def _extract_nigella_domestic_goddess_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Extract recipes from Nigella Lawson's 'How to be a Domestic Goddess'.

    Recipes are in individual small HTML files with the title in
    <h3 class="h3a">, optional intro paragraphs, ingredients in a
    <div class="tbspace"> with paragraphs, and method paragraphs outside it.
    """
    recipes = []
    for title_tag in soup.find_all('h3', class_='h3a'):
        title = _normalize_whitespace(title_tag.get_text(' ', strip=True))
        if not title or len(title) < 4 or len(title) > 120:
            continue

        tbspace = title_tag.find_next_sibling('div', class_='tbspace')
        if not tbspace:
            continue

        ingredients = []
        for p in tbspace.find_all('p'):
            line = _normalize_whitespace(p.get_text(' ', strip=True))
            if line:
                if line.lower().startswith('for the') or line.endswith(':'):
                    ingredients.append('--- ' + line)
                else:
                    ingredients.append(line)

        steps = []
        serves = ''
        for sib in tbspace.find_next_siblings():
            if sib.name in ('h3', 'h2', 'h1'):
                break
            if sib.name != 'p':
                continue
            text = _normalize_whitespace(sib.get_text(' ', strip=True))
            if not text:
                continue
            if _is_serves_line(text):
                serves += ' ' + text
            else:
                steps.append(text)

        if len(ingredients) < 2 or len(steps) < 1:
            continue

        recipes.append({
            'title': title,
            'ingredients': '\n'.join(ingredients).strip(),
            'steps': '\n'.join(steps).strip(),
            'source': os.path.basename(epub_path),
            'file_path': epub_path,
            'image': '',
            'serves': serves.strip(),
        })

    return recipes


def _extract_heading_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Original heading-based extraction for EPUBs that use proper headings."""
    recipes = []
    for header in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
        txt = header.get_text(strip=True)
        if not re.search(r"\b(" + "|".join(RECIPE_KEYS) + r")\b", txt, re.I):
            continue
        # find title (previous heading)
        title = None
        for ph in header.find_all_previous(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
            cand = ph.get_text(strip=True)
            if _is_title_like(cand):
                title = cand
                break
        if not title:
            prev = header.find_previous(['p', 'strong'])
            if prev and _is_title_like(prev.get_text(strip=True)):
                title = prev.get_text(strip=True)
        final_title = title or os.path.splitext(os.path.basename(epub_path))[0]
        final_title = re.sub(r"^[0-9]+[\).\s]+", "", final_title).strip()

        ingredients = []
        sib = header.find_next_sibling()
        while sib and (sib.name in ['p', 'ul', 'ol']):
            if sib.name in ['ul', 'ol']:
                for li in sib.find_all('li'):
                    ingredients.append(li.get_text(strip=True))
            elif sib.name == 'p':
                ingredients.append(sib.get_text(strip=True))
            if sib.name and re.match(r'h[1-6]', sib.name):
                break
            sib = sib.find_next_sibling()

        steps = []
        dir_tag = None
        for h in header.find_all_next(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
            if re.search(r"\b(directions|method|instructions|preparation|steps)\b", h.get_text(strip=True), re.I):
                dir_tag = h
                break
        if dir_tag:
            sib = dir_tag.find_next_sibling()
            while sib:
                if sib.name in ['ul', 'ol']:
                    for li in sib.find_all('li'):
                        steps.append(li.get_text(strip=True))
                elif sib.name == 'p':
                    steps.append(sib.get_text(strip=True))
                if sib.name and re.match(r'h[1-6]', sib.name):
                    break
                sib = sib.find_next_sibling()

        if not ingredients and not steps:
            continue

        body_text = '\n'.join(ingredients + steps)
        serves = _extract_serves_from_text(body_text)

        recipes.append({
            'title': final_title,
            'ingredients': '\n'.join(ingredients).strip(),
            'steps': '\n'.join(steps).strip(),
            'source': os.path.basename(epub_path),
            'file_path': epub_path,
            'image': '',
            'serves': serves,
        })
    return recipes


def _extract_artful_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Extract recipes from The Artful Way to Plant-Based Cooking and My Mediterranean Life.

    Recipes are wrapped in <section> with h2rec/h3rec title, p.serve lines,
    p.recintro headnote, ul.itemlist ingredients, and p.steptxt1/steptxt or
    ol.steplist steps.
    """
    recipes = []
    for section in soup.find_all('section'):
        title_tag = section.find(['h2', 'h3'], class_=['h2rec', 'h3rec'])
        if not title_tag:
            continue
        title = _normalize_whitespace(title_tag.get_text(' ', strip=True))
        # strip any leading non-word decoration left by pagebreak spans
        title = re.sub(r'^[^\w]+', '', title).strip()
        if not title or len(title) < 3:
            continue

        serves = ''
        for p in section.find_all('p', class_='serve'):
            serves += ' ' + _normalize_whitespace(p.get_text(' ', strip=True))

        ingredients = []
        itemlist = section.find('ul', class_='itemlist')
        if itemlist:
            for li in itemlist.find_all('li', class_='item'):
                ing = _normalize_whitespace(li.get_text(' ', strip=True))
                if ing:
                    ingredients.append(ing)

        steps = []
        for p in section.find_all('p', class_=['steptxt1', 'steptxt']):
            step = _normalize_whitespace(p.get_text(' ', strip=True))
            if step:
                steps.append(step)

        if not steps:
            steplist = section.find('ol', class_='steplist')
            if steplist:
                for li in steplist.find_all('li', class_='step'):
                    step = _normalize_whitespace(li.get_text(' ', strip=True))
                    if step:
                        steps.append(step)

        for p in section.find_all('p', class_='recnote'):
            note = _normalize_whitespace(p.get_text(' ', strip=True))
            if note:
                steps.append(note)

        if len(ingredients) < 2 or len(steps) < 1:
            continue

        recipes.append({
            'title': title,
            'ingredients': '\n'.join(ingredients).strip(),
            'steps': '\n'.join(steps).strip(),
            'source': os.path.basename(epub_path),
            'file_path': epub_path,
            'image': '',
            'serves': serves.strip(),
        })
    return recipes


def _extract_flexible_pescatarian_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Extract recipes from Jo Pratt's The Flexible Pescatarian.

    Each recipe is a section with epub:type="chapter", h3r title, optional
    subhead, serv/servb lines, intro headnote, item/item1 ingredients, and
    noindentt steps. Flexible sidebar notes are appended as steps.
    """
    recipes = []
    for section in soup.find_all('section', attrs={'epub:type': 'chapter'}):
        title_tag = section.find('h3', class_='h3r')
        if not title_tag:
            continue
        title = _normalize_whitespace(title_tag.get_text(' ', strip=True))
        title = re.sub(r'[◁▷]', '', title).strip()
        if not title or len(title) < 3:
            continue

        subhead = section.find('p', class_='subhead')
        if subhead:
            sub = _normalize_whitespace(subhead.get_text(' ', strip=True))
            sub = re.sub(r'[◁▷]', '', sub).strip()
            if sub:
                title += ' – ' + sub

        serves = ''
        for p in section.find_all(['p', 'div'], class_=['serv', 'servb']):
            serves += ' ' + _normalize_whitespace(p.get_text(' ', strip=True))

        ingredients = []
        for p in section.find_all('p', class_=['item', 'item1']):
            ing = _normalize_whitespace(p.get_text(' ', strip=True))
            if ing:
                ingredients.append(ing)

        steps = []
        for p in section.find_all('p', class_='noindentt'):
            step = _normalize_whitespace(p.get_text(' ', strip=True))
            if step:
                steps.append(step)

        for aside in section.find_all('aside', class_='sidebar'):
            for p in aside.find_all('p'):
                note = _normalize_whitespace(p.get_text(' ', strip=True))
                if note:
                    steps.append(note)

        if len(ingredients) < 2 or len(steps) < 1:
            continue

        recipes.append({
            'title': title,
            'ingredients': '\n'.join(ingredients).strip(),
            'steps': '\n'.join(steps).strip(),
            'source': os.path.basename(epub_path),
            'file_path': epub_path,
            'image': '',
            'serves': serves.strip(),
        })
    return recipes


def _extract_food_of_spain_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Extract recipes from Claudia Roden's The Food of Spain.

    Recipes live in div.chapter with chapterTitle1 title (inside an anchor),
    optional chapterSubtitle, p.serve, p.qun/qun1 ingredients, and p.recsum steps.
    Non-recipe pages that have chapterTitle1 but no qun/recsum are skipped.
    """
    recipes = []
    for chapter in soup.find_all('div', class_='chapter'):
        title_tag = chapter.find('p', class_='chapterTitle1')
        if not title_tag:
            continue
        title = _normalize_whitespace(title_tag.get_text(' ', strip=True))
        if not title or len(title) < 3:
            continue

        subtitle = chapter.find('p', class_='chapterSubtitle')
        if subtitle:
            sub = _normalize_whitespace(subtitle.get_text(' ', strip=True))
            if sub:
                title += ' ' + sub

        if not (chapter.find('p', class_='qun') or chapter.find('p', class_='recsum')):
            continue

        serves = ''
        serve_tag = chapter.find('p', class_='serve')
        if serve_tag:
            serves = _normalize_whitespace(serve_tag.get_text(' ', strip=True))

        ingredients = []
        for p in chapter.find_all('p', class_=['qun', 'qun1']):
            ing = _normalize_whitespace(p.get_text(' ', strip=True))
            if ing:
                ingredients.append(ing)

        steps = []
        for p in chapter.find_all('p', class_='recsum'):
            step = _normalize_whitespace(p.get_text(' ', strip=True))
            if step:
                steps.append(step)

        if len(ingredients) < 2 or len(steps) < 1:
            continue

        recipes.append({
            'title': title,
            'ingredients': '\n'.join(ingredients).strip(),
            'steps': '\n'.join(steps).strip(),
            'source': os.path.basename(epub_path),
            'file_path': epub_path,
            'image': '',
            'serves': serves,
        })
    return recipes


def _extract_half_baked_harvest_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Extract recipes from Half Baked Harvest Super Simple and The Mediterranean Dish.

    Main recipes use classes: rt (title), ry (serves), prep (timing),
    rhnf/rhn (headnote), rilf/ril (ingredients), rpf/rp (steps). Sub-recipes
    use srt/sry/srilf/sril/srpf/srp/srps.
    """
    recipes = []
    body = soup.body or soup
    if not body:
        return recipes
    if not body.find('p', class_='rt'):
        return recipes

    def _collect(tags):
        cur = None
        result = []
        for tag in tags:
            if not tag.name:
                continue
            classes = tag.get('class', []) or []
            text = _normalize_whitespace(tag.get_text(' ', strip=True))
            if not text:
                continue

            if 'rt' in classes:
                if cur:
                    result.append(cur)
                cur = {'title': text, 'serves': '', 'ingredients': [], 'steps': []}
                continue

            if 'srt' in classes:
                if cur:
                    result.append(cur)
                cur = {'title': text, 'serves': '', 'ingredients': [], 'steps': []}
                continue

            if cur is None:
                continue

            if any(c in classes for c in ('ry', 'sry', 'prep')):
                cur['serves'] += ' ' + text
                continue
            if any(c in classes for c in ('rhnf', 'rhnf-alt', 'rhn')):
                continue
            if any(c in classes for c in ('rilf', 'ril', 'srilf', 'sril')):
                cur['ingredients'].append(text)
                continue
            if any(c in classes for c in ('rpf', 'rpf-alt', 'rp', 'rp2', 'srpf', 'srp', 'srps')):
                cur['steps'].append(text)
                continue

        if cur:
            result.append(cur)
        return result

    for r in _collect(body.find_all(['p', 'div'])):
        if len(r['ingredients']) < 2 or len(r['steps']) < 1:
            continue
        recipes.append({
            'title': r['title'],
            'ingredients': '\n'.join(r['ingredients']).strip(),
            'steps': '\n'.join(r['steps']).strip(),
            'source': os.path.basename(epub_path),
            'file_path': epub_path,
            'image': '',
            'serves': r['serves'].strip(),
        })
    return recipes


def _extract_complete_plant_based_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Extract recipes from The Complete Plant-Based Cookbook.

    Multiple recipes per HTML page. Each recipe starts at h2/p.r_title and ends
    at the next r_title. Classes: r_title, yield, headnote, ing, step, step2, step3.
    """
    recipes = []
    body = soup.body or soup
    if not body:
        return recipes

    titles = body.find_all(lambda tag: tag.name in ('h2', 'p') and tag.get('class') and any('r_title' in c for c in tag.get('class')))
    n = len(titles)
    if not n:
        return recipes

    for i, title_tag in enumerate(titles):
        title = _normalize_whitespace(title_tag.get_text(' ', strip=True))
        title = re.sub(r'^[0-9]+[\).\s]+', '', title).strip()
        if not title or len(title) < 3:
            continue

        next_title = titles[i + 1] if i + 1 < n else None
        ingredients = []
        steps = []
        serves = ''

        for sib in title_tag.find_next_siblings():
            if next_title and sib == next_title:
                break
            if sib in titles:
                break
            if not sib.name:
                continue
            cls = sib.get('class', []) or []
            text = _normalize_whitespace(sib.get_text(' ', strip=True))
            if not text:
                continue

            if 'yield' in cls:
                serves += ' ' + text
                continue
            if 'headnote' in cls:
                continue
            if 'why' in cls:
                continue
            if any(c in cls for c in ('ing', 'ing1')):
                # Ingredients are inside p.IL_item tags within the div.
                for ing in sib.find_all('p', class_=re.compile(r'^IL_item')):
                    ing_text = _normalize_whitespace(ing.get_text(' ', strip=True))
                    if ing_text:
                        ingredients.append(ing_text)
                continue
            if any(c in cls for c in ('step', 'step2', 'step3')):
                steps.append(text)
                continue

        if len(ingredients) < 2 or len(steps) < 1:
            continue

        recipes.append({
            'title': title,
            'ingredients': '\n'.join(ingredients).strip(),
            'steps': '\n'.join(steps).strip(),
            'source': os.path.basename(epub_path),
            'file_path': epub_path,
            'image': '',
            'serves': serves.strip(),
        })
    return recipes


def _extract_secret_to_delicious_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Extract recipes from The Secret to Delicious Vegan Cooking from the Mediterranean and Beyond.

    Each recipe starts at p.C305 and runs until the next C305. A candidate is
    validated by requiring a following C304 or C301 and a C312 step.
    """
    recipes = []
    body = soup.body or soup
    if not body:
        return recipes

    starts = body.find_all('p', class_='C305')
    n = len(starts)
    for i, title_tag in enumerate(starts):
        title = _normalize_whitespace(title_tag.get_text(' ', strip=True))
        if not title or len(title) < 3:
            continue

        next_start = starts[i + 1] if i + 1 < n else None
        has_ingredient_or_serves = False
        has_step = False
        ingredients = []
        steps = []
        serves = ''

        for sib in title_tag.find_next_siblings():
            if next_start and sib == next_start:
                break
            if not sib.name:
                continue
            cls = sib.get('class', []) or []
            text = _normalize_whitespace(sib.get_text(' ', strip=True))
            if not text:
                continue

            if 'C296' in cls:
                continue
            if 'C304' in cls:
                serves += ' ' + text
                has_ingredient_or_serves = True
                continue
            if 'C301' in cls:
                ingredients.append(text)
                has_ingredient_or_serves = True
                continue
            if 'C312' in cls:
                steps.append(text)
                has_step = True
                continue

        if not has_ingredient_or_serves or not has_step:
            continue
        if len(ingredients) < 2 or len(steps) < 1:
            continue

        recipes.append({
            'title': title,
            'ingredients': '\n'.join(ingredients).strip(),
            'steps': '\n'.join(steps).strip(),
            'source': os.path.basename(epub_path),
            'file_path': epub_path,
            'image': '',
            'serves': serves.strip(),
        })
    return recipes


def _extract_vegan_richa_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Extract recipes from Vegan Richa's Everyday Kitchen.

    InDesign export: iterate paragraphs in document order. A recipe starts at
    VREK-Recipe-Title-Blue and stops at the next one.
    """
    recipes = []
    body = soup.body or soup
    if not body:
        return recipes

    paras = body.find_all('p')
    starts = [i for i, p in enumerate(paras) if 'VREK-Recipe-Title-Blue' in (p.get('class') or [])]
    n = len(starts)
    for idx, start_i in enumerate(starts):
        title = _normalize_whitespace(paras[start_i].get_text(' ', strip=True))
        if not title or len(title) < 3:
            continue

        end_i = starts[idx + 1] if idx + 1 < n else len(paras)
        ingredients = []
        steps = []
        serves = ''

        for i in range(start_i + 1, end_i):
            p = paras[i]
            cls = p.get('class', []) or []
            text = _normalize_whitespace(p.get_text(' ', strip=True))
            if not text:
                continue

            if 'VREK-Headnote' in cls:
                continue
            if any(c in cls for c in ('VREK-Time-Yield-Block', 'VREK-Time-Yield-Block-LAST', 'VREK-Time-SFNFSO')):
                serves += ' ' + text
                continue
            if 'VREK-Ingredients-Head' in cls:
                ingredients.append('--- ' + text)
                continue
            if 'VREK-Ingredients-Lft-Col' in cls:
                ingredients.append(text)
                continue
            if any(c in cls for c in ('VREK-Instructions-UnNum', 'VREK-Instructions-Num')):
                steps.append(text)
                continue
            if 'VREK-Basic-Nutritional' in cls:
                continue

        if len(ingredients) < 2 or len(steps) < 1:
            continue

        recipes.append({
            'title': title,
            'ingredients': '\n'.join(ingredients).strip(),
            'steps': '\n'.join(steps).strip(),
            'source': os.path.basename(epub_path),
            'file_path': epub_path,
            'image': '',
            'serves': serves.strip(),
        })
    return recipes


_EXTRACTORS: List[Dict] = []


def register_extractor(predicate, extract, name=None, is_fallback=False):
    """Register an extractor that may be called on a parsed EPUB document.

    predicate(soup, epub_path) -> bool
    extract(soup, epub_path, image_path) -> List[Dict]
    """
    _EXTRACTORS.append({
        'name': name or (getattr(extract, '__name__', None) or 'anonymous'),
        'predicate': predicate,
        'extract': extract,
        'is_fallback': is_fallback,
    })


def _extract_schema_org_recipes(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    """Extract every recipe from schema.org/Recipe microdata blocks in a page."""
    recipes = []
    # Match both http:// and https:// schema.org itemtypes.
    recipe_blocks = soup.select('[itemtype*="schema.org/Recipe"]')
    # Fallback to a page-level scrape if loose recipe itemprops exist without an
    # explicit Recipe wrapper.
    if not recipe_blocks and (soup.select_one('[itemprop="recipeIngredient"]') or
                              soup.select_one('[itemprop="recipeInstructions"]')):
        recipe_blocks = [soup]

    for block in recipe_blocks:
        title_tag = block.select_one('[itemprop="name"]')
        title = title_tag.get_text(strip=True) if title_tag else ''
        if not title:
            # Some pages put the recipe title in the first heading.
            h_tag = block.find('h1') or block.find('h2')
            if h_tag:
                title = h_tag.get_text(strip=True)
        if not title:
            title = os.path.splitext(os.path.basename(epub_path))[0]

        ingredients = [e.get_text('\n', strip=True)
                       for e in block.find_all(attrs={'itemprop': 'recipeIngredient'})]

        steps = []
        for instr in block.find_all(attrs={'itemprop': 'recipeInstructions'}):
            # Nested list items inside the instruction container.
            for li in instr.find_all('li'):
                steps.append(li.get_text(strip=True))
            # Otherwise a flat instruction paragraph.
            txt = instr.get_text('\n', strip=True)
            if txt and not steps:
                steps.append(txt)

        body_text = '\n'.join([title] + ingredients + steps)
        serves = _extract_serves_from_text(body_text)
        # schema.org recipeYield if present
        if not serves:
            yield_tag = block.select_one('[itemprop="recipeYield"]')
            if yield_tag:
                serves = _normalize_whitespace(yield_tag.get_text(strip=True))

        recipes.append({
            'title': title,
            'ingredients': '\n'.join(ingredients).strip(),
            'steps': '\n'.join(steps).strip(),
            'source': os.path.basename(epub_path),
            'file_path': epub_path,
            'image': image_path,
            'serves': serves,
        })

    return recipes


def _source_predicate(substring: str):
    def predicate(soup: BeautifulSoup, epub_path: str) -> bool:
        return substring in os.path.basename(epub_path).lower()
    predicate.is_book_specific = True  # type: ignore[attr-defined]
    return predicate


def _always_true_predicate(soup: BeautifulSoup, epub_path: str) -> bool:
    return True


def _schema_org_predicate(soup: BeautifulSoup, epub_path: str) -> bool:
    return bool(soup.select_one('[itemtype*="schema.org/Recipe"]') or
                soup.select_one('[itemprop="recipeIngredient"], [itemprop="recipeInstructions"]'))


def _extract_paragraph_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_paragraph_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_heading_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_heading_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_fallback_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_fallback(soup, epub_path, image_path)
    # Apply the same quality gate as the old inline fallback to avoid garbage.
    good = []
    for r in recipes:
        real_ingredients = [l for l in r['ingredients'].split('\n') if re.match(r'^[\d\s¼½¾⅓⅔⅛⅜⅝⅞]', l) or
                            re.match(r'^(' + '|'.join(re.escape(w) for w in _QUANTITY_WORDS) + r')\b', l.lower())]
        numbered_steps = [l for l in r['steps'].split('\n') if re.match(r'^\d+[\.\)]', l)]
        if len(real_ingredients) >= 2 and len(numbered_steps) >= 2:
            good.append(r)
    return good


def _extract_30min_meals_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_30min_meals(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_superfood_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_superfood_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_northern_italy_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_northern_italy_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_every_grain_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_every_grain_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_one_pan_wonders_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_one_pan_wonders_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_gordon_ramsay_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_gordon_ramsay_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_simply_japanese_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_simply_japanese_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_plenty_more_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_plenty_more_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_flavour_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_flavour_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_plenty_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_plenty_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_veganomicon_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_veganomicon_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_french_provincial_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_french_provincial_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_delias_cakes_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_delias_cakes_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_good_things_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_good_things_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_everyday_super_food_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_everyday_super_food_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_jamie_veg_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_jamie_veg_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_seven_fires_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_seven_fires_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_cocolat_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_cocolat_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_kitchen_diaries_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_kitchen_diaries_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_nigella_how_to_eat_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_nigella_how_to_eat_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_nigella_domestic_goddess_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_nigella_domestic_goddess_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_artful_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_artful_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_flexible_pescatarian_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_flexible_pescatarian_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_food_of_spain_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_food_of_spain_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_half_baked_harvest_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_half_baked_harvest_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_complete_plant_based_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_complete_plant_based_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_secret_to_delicious_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_secret_to_delicious_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_vegan_richa_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_vegan_richa_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_green_burgers_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Extract recipes from 'Green Burgers: Creative Vegetarian Recipes for Burgers and Sides'.

    Recipes are marked by <p class="rep-ttl"> (burgers) or <p class="h2"> (sides,
    sauces, buns). The first <p class="h3"> after the title is the yield/serves line.
    Ingredients follow in <p class="hang"> / <p class="hang top"> with optional
    <p class="txt1"> subheadings. Method steps start after an <p class="h3">
    containing 'Instructions' and are in <p class="txi"> or numbered <p class="hang">.
    <p class="note"> paragraphs are appended as notes.
    """
    recipes: List[Dict] = []
    body = soup.body or soup
    if not body:
        return recipes

    def _classes(elem) -> set:
        c = elem.get('class', [])
        if isinstance(c, str):
            return {c}
        return set(c)

    def _elem_text(elem) -> str:
        return _normalize_whitespace(elem.get_text(' ', strip=True))

    def _is_title(elem) -> bool:
        if elem.name != 'p':
            return False
        c = _classes(elem)
        return 'rep-ttl' in c or 'h2' in c

    cur: Dict = None
    in_instructions = False

    for elem in body.find_all(['p', 'div', 'h1', 'h2', 'h3', 'hr']):
        c = _classes(elem)
        if _is_title(elem):
            if cur and len(cur.get('ingredients', [])) >= 2 and len(cur.get('steps', [])) >= 1:
                recipes.append(cur)
            cur = {
                'title': _elem_text(elem),
                'serves': '',
                'ingredients': [],
                'steps': [],
                'source': os.path.basename(epub_path),
                'file_path': epub_path,
                'image': '',
            }
            in_instructions = False
            continue

        if cur is None:
            continue

        # Skip image containers, horizontal rules, and h1 chapter markers.
        if elem.name == 'div' and 'ser' in c:
            continue
        if elem.name == 'hr':
            continue
        if elem.name in ('h1', 'h2') or (elem.name == 'p' and 'h1' in c):
            continue

        text = _elem_text(elem)
        if not text:
            continue

        if elem.name == 'p' and 'h3' in c:
            low = text.lower()
            if 'instruction' in low or 'day 1:' in low or 'day 2:' in low:
                in_instructions = True
                continue
            if not in_instructions and not cur['serves']:
                cur['serves'] = text
                continue
            continue

        if elem.name == 'p' and 'txt1' in c:
            cur['ingredients'].append('--- ' + text)
            continue

        if elem.name == 'p' and ('hang' in c or 'hang top' in ' '.join(c)):
            if in_instructions:
                if re.match(r'^\d+\.', text) or text.strip().lower().startswith('instructions'):
                    cur['steps'].append(text)
                continue
            cur['ingredients'].append(text)
            continue

        if elem.name == 'p' and 'txi' in c:
            if in_instructions:
                cur['steps'].append(text)
            continue

        if elem.name == 'p' and 'note' in c:
            cur['steps'].append('Note: ' + text)
            continue

    if cur and len(cur.get('ingredients', [])) >= 2 and len(cur.get('steps', [])) >= 1:
        recipes.append(cur)

    for r in recipes:
        r['ingredients'] = '\n'.join(r['ingredients']).strip()
        r['steps'] = '\n'.join(r['steps']).strip()

    return recipes


def _extract_green_burgers_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_green_burgers_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_appetites_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Extract recipes from Anthony Bourdain's 'Appetites: A Cookbook'.

    Uses the book's specific classes: rec_title/rec_titleb for titles,
    noindenth for serves, hang for ingredients, noindentl1/noindentl for steps.
    """
    recipes = []
    for title_tag in soup.find_all('p', class_=re.compile(r'\b(rec_title|rec_titleb)\b')):
        title = _normalize_whitespace(title_tag.get_text(' ', strip=True))
        if not title:
            continue

        serves = ''
        ingredients = []
        steps = []

        # The title lives inside <header>; the recipe body follows in the same
        # <section>. Use find_all_next and stop at the next recipe title.
        title_classes = re.compile(r'\b(rec_title|rec_titleb)\b')
        for elem in title_tag.find_all_next():
            if elem.name == 'p' and elem.get('class') and title_classes.search(' '.join(elem.get('class', []))):
                break
            cls = ' '.join(elem.get('class', []))
            text = _normalize_whitespace(elem.get_text(' ', strip=True))
            if not text:
                continue

            if 'noindenth' in cls:
                serves = text
            elif 'hang' in cls:
                if text.endswith(':') or (text.isupper() and len(text) < 60):
                    ingredients.append('--- ' + text)
                else:
                    ingredients.append(text)
            elif 'noindentl1' in cls or 'noindentl' in cls:
                steps.append(text)
            elif 'noindentc' in cls:
                # Subheadings like 'SPECIAL EQUIPMENT:'
                ingredients.append('--- ' + text)

        if len(ingredients) >= 2 and len(steps) >= 1:
            recipes.append({
                'title': title,
                'ingredients': '\n'.join(ingredients).strip(),
                'steps': '\n'.join(steps).strip(),
                'source': os.path.basename(epub_path),
                'file_path': epub_path,
                'image': '',
                'serves': serves,
            })
    return recipes


def _extract_appetites_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_appetites_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_australian_food_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Extract recipes from Bill Granger's 'Australian Food'.

    Uses classes: recipeTitle for title, serves for yield, ingredients div with
    ingredient/ingredient-Head/ingredient-top paragraphs, and method paragraphs.
    """
    recipes = []
    for title_tag in soup.find_all('p', class_='recipeTitle'):
        title = _normalize_whitespace(title_tag.get_text(' ', strip=True))
        if not title:
            continue

        serves = ''
        ingredients = []
        steps = []

        for elem in title_tag.find_next_siblings():
            if elem.name == 'p' and 'recipeTitle' in elem.get('class', []):
                break
            if elem.name == 'p' and 'serves' in elem.get('class', []):
                serves = _normalize_whitespace(elem.get_text(' ', strip=True))
                continue
            if elem.name == 'p' and 'recipeIntro' in ' '.join(elem.get('class', [])):
                continue

            # Handle siblings that are method paragraphs directly, and walk into
            # container divs (e.g. <div class="ingredients">) for ingredient lines.
            if elem.name == 'p' and 'method' in ' '.join(elem.get('class', [])):
                steps.append(_normalize_whitespace(elem.get_text(' ', strip=True)))
                continue
            for p in elem.find_all('p'):
                cls = ' '.join(p.get('class', []))
                text = _normalize_whitespace(p.get_text(' ', strip=True))
                if not text:
                    continue
                if 'ingredient' in cls and 'ingredients' not in cls:
                    if text.isupper() and len(text) < 60:
                        ingredients.append('--- ' + text)
                    else:
                        ingredients.append(text)
                elif 'method' in cls:
                    steps.append(text)

        if len(ingredients) >= 2 and len(steps) >= 1:
            recipes.append({
                'title': title,
                'ingredients': '\n'.join(ingredients).strip(),
                'steps': '\n'.join(steps).strip(),
                'source': os.path.basename(epub_path),
                'file_path': epub_path,
                'image': '',
                'serves': serves,
            })
    return recipes


def _extract_australian_food_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_australian_food_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_community_salad_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Extract recipes from 'Community: Salad Recipes from Arthur Street Kitchen'.

    This EPUB is exported as fixed-layout pages with inline-styled spans and no
    semantic classes. Recipe titles are in the largest font (fs2), serves/subheadings
    in fs8, ingredient lines in fs3, headnotes in fs9, and method steps in fs0.
    """
    recipes = []

    def _spans_with_class(soup, cls_substring: str):
        return soup.find_all('span', class_=lambda x: x and cls_substring in x)

    title_spans = _spans_with_class(soup, 'fs2')
    if not title_spans:
        return recipes

    title = _normalize_whitespace(' '.join(s.get_text(' ', strip=True) for s in title_spans))
    title = title.strip()
    if not title or len(title) < 3:
        return recipes

    # Section dividers also use fs2 but have no ingredient lines (fs3).
    ingredient_spans = [s for s in _spans_with_class(soup, 'fs3')
                        if not re.match(r'^\d+$', _normalize_whitespace(s.get_text(' ', strip=True)))]
    if len(ingredient_spans) < 2:
        return recipes

    serves = ''
    ingredients = []
    steps = []

    for s in _spans_with_class(soup, 'fs8'):
        text = _normalize_whitespace(s.get_text(' ', strip=True))
        if not text:
            continue
        if _is_serves_line(text):
            serves = text
        elif text.isupper() and len(text) < 80:
            ingredients.append('--- ' + text)

    for s in ingredient_spans:
        text = _normalize_whitespace(s.get_text(' ', strip=True))
        if not text or text.endswith(':'):
            continue
        # Page number was already filtered out.
        ingredients.append(text)

    for s in _spans_with_class(soup, 'fs0'):
        text = _normalize_whitespace(s.get_text(' ', strip=True))
        if text:
            steps.append(text)

    if len(ingredients) >= 2 and len(steps) >= 1:
        recipes.append({
            'title': title,
            'ingredients': '\n'.join(ingredients).strip(),
            'steps': '\n'.join(steps).strip(),
            'source': os.path.basename(epub_path),
            'file_path': epub_path,
            'image': '',
            'serves': serves,
        })
    return recipes


def _extract_community_salad_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_community_salad_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_cool_beans_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Extract recipes from Joe Yonan's 'Cool Beans'.

    Recipes are embedded in chapter files using InDesign classes:
    rt/rtno/rstno for titles, ry for yield, ril* for ingredients, rp* for steps,
    and sbh1/sb for sidebar sub-recipes.
    """
    recipes = []
    cur: Dict = None

    def _new_recipe(title: str) -> Dict:
        return {
            'title': title,
            'serves': '',
            'ingredients': [],
            'steps': [],
            'source': os.path.basename(epub_path),
            'file_path': epub_path,
            'image': '',
        }

    def _finalize():
        nonlocal cur
        if cur and len(cur.get('ingredients', [])) >= 2 and len(cur.get('steps', [])) >= 1:
            recipes.append(cur)
        cur = None

    title_classes = {'rt', 'rtno'}
    subtitle_classes = {'rst', 'rstno'}

    for elem in soup.find_all(['p', 'div']):
        cls = elem.get('class', []) or []
        cls_set = set(cls)
        text = _normalize_whitespace(elem.get_text(' ', strip=True))
        if not text:
            continue

        # Main recipe title
        if cls_set & title_classes:
            _finalize()
            cur = _new_recipe(text)
            continue

        # Subtitle appended to current main title if we haven't started collecting.
        if cls_set & subtitle_classes:
            if cur and not cur['ingredients'] and not cur['steps']:
                cur['title'] += ' ' + text
            continue

        # Sidebar sub-recipe title
        if 'sbh1' in cls_set:
            _finalize()
            cur = _new_recipe(text)
            continue

        if cur is None:
            continue

        # Skip headnotes and non-recipe blocks
        if cls_set & {'rhnf', 'rhn'}:
            continue
        if 'ry' in cls_set or 'ry2' in cls_set:
            cur['serves'] += ' ' + text
            continue
        if 'rilh' in cls_set:
            cur['ingredients'].append('--- ' + text)
            continue
        if any(c in cls_set for c in ('rilf', 'ril', 'rill', 'ril_spacebreak')):
            cur['ingredients'].append(text)
            continue
        if any(c in cls_set for c in ('rpf', 'rp', 'rp2')):
            cur['steps'].append(text)
            continue
        if 'sb' in cls_set and cur['steps']:
            # Sidebar method continuation
            cur['steps'].append(text)
            continue

    _finalize()
    for r in recipes:
        r['ingredients'] = '\n'.join(r['ingredients']).strip()
        r['steps'] = '\n'.join(r['steps']).strip()
        r['serves'] = r['serves'].strip()
    return recipes


def _extract_cool_beans_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_cool_beans_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_first_generation_recipes(soup: BeautifulSoup, epub_path: str,
                                      doc_name: str = '', saved_images: Dict[str, str] = None,
                                      images_dir: str = '') -> List[Dict]:
    """Extract recipes from Frankie Gaw's 'First Generation'.

    Recipes are marked by h3 tags with an 'rt' class (rt or rt-alt). Story
    essays use plain h3 headings and are ignored. Uses classes: rhnf (headnote),
    ry (yield/serves), rilh (ingredient subhead), ril (ingredient),
    rpf/rp (steps), and rst/rst2/rst-alt (Chinese titles) which we skip.
    """
    recipes = []
    cur = None

    def _new(title: str, title_elem):
        return {
            'title': title,
            'title_elem': title_elem,
            'ingredients': [],
            'steps': [],
            'serves': '',
            'source': os.path.basename(epub_path),
            'file_path': epub_path,
            'image': '',
        }

    body = soup.body or soup
    title_tags = []
    for elem in body.find_all(['h3', 'p', 'h4', 'h5']):
        cls = elem.get('class', []) or []
        cls_set = set(cls)
        text = _normalize_whitespace(elem.get_text(' ', strip=True))
        if not text:
            continue

        # Recipe titles live in h3.rt / h3.rt-alt. Plain h3.h3 are essays.
        if elem.name == 'h3' and cls_set & {'rt', 'rt-alt'}:
            title_tags.append(elem)

    title_images = {}
    if saved_images and title_tags:
        images = [img for img in body.find_all('img') if not _is_decorative_image(img)]
        title_images = {
            id(t): img for t, img in _map_images_to_titles(
                title_tags, images, direction='after',
                doc_name=doc_name, saved_images=saved_images,
                images_dir=images_dir).items()
        }

    def _finalize():
        nonlocal cur
        if cur and len(cur['ingredients']) >= 2 and len(cur['steps']) >= 1:
            title_elem = cur.pop('title_elem', None)
            # Recipe photos appear immediately after the title.
            if saved_images and title_elem is not None and not cur.get('image'):
                img = title_images.get(id(title_elem))
                if img:
                    cur['image'] = _resolve_image_path(
                        img.get('src') or '', doc_name, saved_images, images_dir)
            cur['ingredients'] = '\n'.join(cur['ingredients']).strip()
            cur['steps'] = '\n'.join(cur['steps']).strip()
            cur['serves'] = cur['serves'].strip()
            recipes.append(cur)
        cur = None

    cur = None
    for elem in body.find_all(['h3', 'p', 'h4', 'h5']):
        cls = elem.get('class', []) or []
        cls_set = set(cls)
        text = _normalize_whitespace(elem.get_text(' ', strip=True))
        if not text:
            continue

        if elem.name == 'h3' and cls_set & {'rt', 'rt-alt'}:
            _finalize()
            cur = _new(text, elem)
            continue

        if cur is None:
            continue

        if cls_set & {'rhnf', 'rhn'}:
            continue
        if 'ry' in cls_set:
            cur['serves'] += ' ' + text
            continue
        if 'rilh' in cls_set:
            cur['ingredients'].append('--- ' + text)
            continue
        if 'ril' in cls_set:
            cur['ingredients'].append(text)
            continue
        if cls_set & {'rpf', 'rp'}:
            cur['steps'].append(text)
            continue
        if cls_set & {'rnh', 'rn'}:
            continue
        if cls_set & {'rst', 'rst2', 'rst-alt', 'rst2-alt'}:
            continue

    _finalize()
    return recipes


def _extract_first_generation_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '',
                                                  doc_name: str = '', saved_images: Dict[str, str] = None,
                                                  images_dir: str = '') -> List[Dict]:
    # Per-recipe image mapping is done in _extract_first_generation_recipes.
    # Leave recipes without a matching photo blank rather than reusing the
    # chapter/document image.
    return _extract_first_generation_recipes(soup, epub_path, doc_name, saved_images, images_dir)


def _extract_into_vietnamese_recipes(soup: BeautifulSoup, epub_path: str,
                                     doc_name: str = '', saved_images: Dict[str, str] = None,
                                     images_dir: str = '') -> List[Dict]:
    """Extract recipes from Andrea Nguyen's 'Into the Vietnamese Kitchen'.

    Each recipe has an English title in h2.Header_RecipeA and a Vietnamese
    title in the following h2.Header_RecipeB. Ingredients use p.hanging (often
    wrapped in div.hanging), steps use p.extract, and the yield line is
    p.nonindent1.

    Recipe photos are grouped in captioned figures; the caption usually names
    the dish, so we match captions to titles before falling back to proximity.
    """
    recipes = []
    body = soup.body or soup

    title_tags = body.find_all('h2', class_='Header_RecipeA')
    n = len(title_tags)
    for i, title_tag in enumerate(title_tags):
        title = _normalize_whitespace(title_tag.get_text(' ', strip=True))
        if not title:
            continue

        # Append Vietnamese title if it immediately follows.
        next_sib = title_tag.find_next_sibling()
        if next_sib and next_sib.name == 'h2' and 'Header_RecipeB' in (next_sib.get('class') or []):
            viet = _normalize_whitespace(next_sib.get_text(' ', strip=True))
            if viet and viet != title:
                title = f"{title} - {viet}"

        ingredients = []
        steps = []
        serves = ''

        for sib in title_tag.find_next_siblings():
            if sib.name == 'h2' and 'Header_RecipeA' in (sib.get('class') or []):
                break
            if not sib.name:
                continue
            cls = sib.get('class', []) or []
            cls_set = set(cls)
            text = _normalize_whitespace(sib.get_text(' ', strip=True))
            if not text:
                continue

            # Yield line
            if 'nonindent1' in cls_set:
                serves += ' ' + text
                continue
            # Ingredient lines, often wrapped in div.hanging
            if 'hanging' in cls_set:
                # Prefer structured paragraphs inside the wrapper.
                inner = sib.find_all('p', class_='hanging')
                if inner:
                    for p in inner:
                        pt = _normalize_whitespace(p.get_text(' ', strip=True))
                        if not pt:
                            continue
                        if pt.isupper() and len(pt) < 80:
                            ingredients.append('--- ' + pt)
                        else:
                            ingredients.append(pt)
                else:
                    if text.isupper() and len(text) < 80:
                        ingredients.append('--- ' + text)
                    else:
                        ingredients.append(text)
                continue
            # Numbered steps
            if 'extract' in cls_set:
                steps.append(text)
                continue
            # Notes and asides
            if cls_set & {'nonindent', 'indent', 'center'}:
                continue
            if sib.name in ('h4', 'h5'):
                continue

        if len(ingredients) >= 2 and len(steps) >= 1:
            recipes.append({
                'title': title,
                'ingredients': '\n'.join(ingredients).strip(),
                'steps': '\n'.join(steps).strip(),
                'source': os.path.basename(epub_path),
                'file_path': epub_path,
                'image': '',
                'serves': serves.strip(),
            })

    if not saved_images:
        return recipes

    # Build a lookup from the English portion of the title to the recipe.
    title_to_recipe: Dict[str, Dict] = {}
    for r in recipes:
        english_title = r['title'].split(' - ')[0].strip()
        title_to_recipe[english_title.lower()] = r
        title_to_recipe[r['title'].lower()] = r

    # Match captioned images to recipes.  Use both exact substring and token
    # overlap so captions like "Tet Sticky Rice Cake" still match
    # "TET STICKY RICE CAKES".
    def _caption_score(caption: str, title: str) -> int:
        if title in caption:
            return 1000 + len(title)
        caption_tokens = set(re.findall(r'\w+', caption))
        title_tokens = set(re.findall(r'\w+', title))
        if not title_tokens:
            return -1
        common = caption_tokens & title_tokens
        # Require at least two content words in common.
        if len(common) < 2:
            return -1
        # Score by fraction of title tokens found in the caption.
        return int(100 * len(common) / len(title_tokens))

    for img in body.find_all('img'):
        if _is_decorative_image(img):
            continue
        src = img.get('src') or ''
        resolved = _resolve_image_path(src, doc_name, saved_images, images_dir)
        if not resolved:
            continue
        # Caption is usually the next figure paragraph.
        caption_elem = img.find_next_sibling('p', class_='figure')
        if not caption_elem:
            caption_elem = img.find_parent().find_next_sibling('p', class_='figure')
        if not caption_elem:
            caption_elem = img.find_next('p', class_='figure')
        if not caption_elem:
            continue
        caption = _normalize_whitespace(caption_elem.get_text(' ', strip=True)).lower()
        # Find the recipe whose title matches the caption best.
        best_match = None
        best_score = -1
        for key, r in title_to_recipe.items():
            if not key:
                continue
            score = _caption_score(caption, key)
            if score > best_score:
                best_score = score
                best_match = r
        # Require a meaningful match (exact hit or >=70% token overlap).
        if best_score >= 70 and best_match and not best_match.get('image'):
            best_match['image'] = resolved

    return recipes


def _extract_into_vietnamese_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '',
                                                doc_name: str = '', saved_images: Dict[str, str] = None,
                                                images_dir: str = '') -> List[Dict]:
    recipes = _extract_into_vietnamese_recipes(soup, epub_path, doc_name, saved_images, images_dir)
    # We intentionally leave recipes without a caption match blank so they do not
    # inherit an unrelated chapter photo.
    for r in recipes:
        if not r.get('image'):
            r['image'] = ''
    return recipes


def _extract_street_food_asia_recipes(soup: BeautifulSoup, epub_path: str,
                                       doc_name: str = '', saved_images: Dict[str, str] = None,
                                       images_dir: str = '') -> List[Dict]:
    """Extract recipes from Luke Nguyen's 'Street Food Asia'.

    Each recipe lives in its own HTML document. Titles use <p class='rec-ttl'>,
    subtitles use <p class='rec-sttl'>, yields use <p class='txt'>, ingredients
    use <p class='hang'>, and steps use <p class='txi'> / <p class='txii'>.
    The recipe photo appears after the title.
    """
    recipes = []
    body = soup.body or soup

    title_tag = body.find('p', class_='rec-ttl')
    if not title_tag:
        return recipes

    title = _normalize_whitespace(title_tag.get_text(' ', strip=True))
    if not title:
        return recipes

    ingredients = []
    steps = []
    serves = ''
    in_steps = False

    for elem in title_tag.find_all_next():
        if not elem.name:
            continue
        cls = elem.get('class', []) or []
        cls_set = set(cls)
        text = _normalize_whitespace(elem.get_text(' ', strip=True))
        if not text:
            continue

        if 'rec-ttl' in cls_set:
            break
        if 'rec-sttl' in cls_set:
            continue
        if 'txm' in cls_set:
            continue
        if 'txt' in cls_set and not in_steps:
            # Yield line or subheading before steps begin.
            low = text.lower()
            if _is_serves_line(text) or low.startswith(('serves', 'makes')):
                serves += ' ' + text
            continue
        if 'hang' in cls_set and not in_steps:
            ingredients.append(text)
            continue
        if cls_set & {'txi', 'txii'}:
            in_steps = True
            steps.append(text)
            continue

    if len(ingredients) >= 2 and len(steps) >= 1:
        image_path = ''
        if saved_images:
            images = [img for img in body.find_all('img') if not _is_decorative_image(img)]
            title_images = _map_images_to_titles(
                [title_tag], images, direction='after',
                doc_name=doc_name, saved_images=saved_images,
                images_dir=images_dir)
            img = title_images.get(id(title_tag))
            if img:
                image_path = _resolve_image_path(
                    img.get('src') or '', doc_name, saved_images, images_dir)
        recipes.append({
            'title': title,
            'ingredients': '\n'.join(ingredients).strip(),
            'steps': '\n'.join(steps).strip(),
            'source': os.path.basename(epub_path),
            'file_path': epub_path,
            'image': image_path,
            'serves': serves.strip(),
        })
    return recipes


def _extract_street_food_asia_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '',
                                                  doc_name: str = '', saved_images: Dict[str, str] = None,
                                                  images_dir: str = '') -> List[Dict]:
    recipes = _extract_street_food_asia_recipes(soup, epub_path, doc_name, saved_images, images_dir)
    for r in recipes:
        if not r.get('image'):
            r['image'] = image_path
    return recipes


def _extract_good_bite_recipes(soup: BeautifulSoup, epub_path: str,
                               doc_name: str = '', saved_images: Dict[str, str] = None,
                               images_dir: str = '') -> List[Dict]:
    """Extract recipes from 'The Good Bite's High Protein Meal Prep'.

    Recipes use h2.rec_head for titles, li.ingred for ingredients,
    h5.ingredient_header for ingredient subheadings, p.method/p.method1 for
    steps, and p.prot_txt1 for the yield/serving line. Ingredients and steps
    are nested inside section.sidebar_wrapper and section.maincontent_wrapper,
    so we walk all descendants between consecutive recipe titles.

    The main recipe photo appears right after the title, before the yield line.
    """
    recipes = []
    body = soup.body or soup

    title_tags = body.find_all('h2', class_='rec_head')
    title_images = {}
    if saved_images and title_tags:
        images = [img for img in body.find_all('img') if not _is_decorative_image(img)]
        title_images = {
            id(t): img for t, img in _map_images_to_titles(
                title_tags, images, direction='after',
                doc_name=doc_name, saved_images=saved_images,
                images_dir=images_dir).items()
        }

    n = len(title_tags)
    for i, title_tag in enumerate(title_tags):
        title = _normalize_whitespace(title_tag.get_text(' ', strip=True))
        if not title:
            continue

        ingredients = []
        steps = []
        serves = ''

        for elem in title_tag.find_all_next():
            if elem.name == 'h2' and 'rec_head' in (elem.get('class') or []):
                break
            if not elem.name:
                continue
            text = _normalize_whitespace(elem.get_text(' ', strip=True))
            if not text:
                continue
            cls = elem.get('class', []) or []
            cls_set = set(cls)

            if 'prot_txt1' in cls_set:
                serves += ' ' + text
                continue
            if 'rec_intro' in cls_set or 'prot_txt' in cls_set:
                continue
            if 'ingredient_header' in cls_set:
                ingredients.append('--- ' + text)
                continue
            if 'ingred' in cls_set:
                ingredients.append(text)
                continue
            if elem.name == 'li' and 'ingred' in cls_set:
                ingredients.append(text)
                continue
            if cls_set & {'method', 'method1'}:
                steps.append(text)
                continue

        if len(ingredients) >= 2 and len(steps) >= 1:
            image_path = ''
            if saved_images:
                img = title_images.get(id(title_tag))
                if img:
                    image_path = _resolve_image_path(
                        img.get('src') or '', doc_name, saved_images, images_dir)
            recipes.append({
                'title': title,
                'ingredients': '\n'.join(ingredients).strip(),
                'steps': '\n'.join(steps).strip(),
                'source': os.path.basename(epub_path),
                'file_path': epub_path,
                'image': image_path,
                'serves': serves.strip(),
            })
    return recipes


def _extract_good_bite_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '',
                                          doc_name: str = '', saved_images: Dict[str, str] = None,
                                          images_dir: str = '') -> List[Dict]:
    # Per-recipe image mapping is done in _extract_good_bite_recipes.  Leave
    # recipes without a matching photo blank.
    return _extract_good_bite_recipes(soup, epub_path, doc_name, saved_images, images_dir)


def _extract_estela_recipes(soup: BeautifulSoup, epub_path: str,
                            doc_name: str = '', saved_images: Dict[str, str] = None,
                            images_dir: str = '') -> List[Dict]:
    """Extract recipes from Estela (Restaurant) by Ignacio Mattos.

    Recipes live in chapter XHTML files. Titles are <p class='RH'>, yields are
    <p class='RY'>, ingredients use <p class='RI'> / <p class='RIH1'>, and
    steps use <p class='RPH'> / <p class='RP1'> / <p class='RP'>.  Recipe photos
    appear before each title in document order; map them by position so each
    photo is tied to the correct recipe.
    """
    recipes = []
    body = soup.body or soup

    title_tags = body.find_all('p', class_='RH')
    title_images = {}
    if saved_images and title_tags:
        images = [img for img in body.find_all('img') if not _is_decorative_image(img)]
        title_images = {
            id(t): img for t, img in _map_images_to_titles(
                title_tags, images, direction='before',
                doc_name=doc_name, saved_images=saved_images,
                images_dir=images_dir).items()
        }

    for title_tag in title_tags:
        title = _normalize_whitespace(title_tag.get_text(' ', strip=True))
        if not title:
            continue

        ingredients = []
        steps = []
        serves = ''

        for elem in title_tag.find_all_next():
            if elem.name == 'p' and 'RH' in (elem.get('class') or []):
                break
            if not elem.name:
                continue
            text = _normalize_whitespace(elem.get_text(' ', strip=True))
            if not text:
                continue
            cls = elem.get('class', []) or []
            cls_set = set(cls)

            if 'RY' in cls_set:
                serves += ' ' + text
                continue
            if cls_set & {'RN', 'RNI'}:
                continue
            if 'RIH1' in cls_set or 'RIH' in cls_set:
                ingredients.append('--- ' + text)
                continue
            if 'RI' in cls_set:
                ingredients.append(text)
                continue
            if 'RPH' in cls_set:
                steps.append('--- ' + text)
                continue
            if cls_set & {'RP1', 'RP'}:
                steps.append(text)
                continue

        if len(ingredients) >= 2 and len(steps) >= 1:
            image_path = ''
            if saved_images:
                img = title_images.get(id(title_tag))
                if img:
                    image_path = _resolve_image_path(
                        img.get('src') or '', doc_name, saved_images, images_dir)
            recipes.append({
                'title': title,
                'ingredients': '\n'.join(ingredients).strip(),
                'steps': '\n'.join(steps).strip(),
                'source': os.path.basename(epub_path),
                'file_path': epub_path,
                'image': image_path,
                'serves': serves.strip(),
            })
    return recipes


def _extract_estela_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '',
                                       doc_name: str = '', saved_images: Dict[str, str] = None,
                                       images_dir: str = '') -> List[Dict]:
    # Per-recipe image mapping is done in _extract_estela_recipes.  Do not fall
    # back to the chapter/document image, because that assigns the same photo to
    # many unrelated recipes.
    return _extract_estela_recipes(soup, epub_path, doc_name, saved_images, images_dir)


def _extract_thomas_keller_recipes(soup: BeautifulSoup, epub_path: str,
                                   doc_name: str = '', saved_images: Dict[str, str] = None,
                                   images_dir: str = '') -> List[Dict]:
    """Extract recipes from Thomas Keller's 'The French Laundry, Per Se'.

    Recipes are headed by <h3 class='h3_rec'>, with yields in <p class='rec_serve'>,
    ingredient sections marked by <h4 class='rec_ing-h4'> / <h4 class='rec_ing-h4b'>,
    ingredients in <p class='rec_ing'>, step sections in <h4 class='rec_step-h4'>,
    and steps in <p class='rec_stept'> / <p class='rec_step'>.  Recipe photos sit
    before each title in document order.
    """
    recipes = []
    body = soup.body or soup

    title_tags = body.find_all('h3', class_='h3_rec')
    title_images = {}
    if saved_images and title_tags:
        images = [img for img in body.find_all('img') if not _is_decorative_image(img)]
        title_images = {
            id(t): img for t, img in _map_images_to_titles(
                title_tags, images, direction='before',
                doc_name=doc_name, saved_images=saved_images,
                images_dir=images_dir).items()
        }

    for title_tag in title_tags:
        title = _normalize_whitespace(title_tag.get_text(' ', strip=True))
        if not title:
            continue

        ingredients = []
        steps = []
        serves = ''

        for elem in title_tag.find_all_next():
            if elem.name == 'h3' and 'h3_rec' in (elem.get('class') or []):
                break
            if not elem.name:
                continue
            text = _normalize_whitespace(elem.get_text(' ', strip=True))
            if not text:
                continue
            cls = elem.get('class', []) or []
            cls_set = set(cls)

            if 'rec_serve' in cls_set:
                serves += ' ' + text
                continue
            if 'rec_by' in cls_set:
                continue
            if cls_set & {'rec_ing-h4', 'rec_ing-h4b'}:
                ingredients.append('--- ' + text)
                continue
            if 'rec_step-h4' in cls_set:
                steps.append('--- ' + text)
                continue
            if 'rec_ing' in cls_set:
                ingredients.append(text)
                continue
            if cls_set & {'rec_stept', 'rec_step'}:
                steps.append(text)
                continue

        if len(ingredients) >= 2 and len(steps) >= 1:
            image_path = ''
            if saved_images:
                img = title_images.get(id(title_tag))
                if img:
                    image_path = _resolve_image_path(
                        img.get('src') or '', doc_name, saved_images, images_dir)
            recipes.append({
                'title': title,
                'ingredients': '\n'.join(ingredients).strip(),
                'steps': '\n'.join(steps).strip(),
                'source': os.path.basename(epub_path),
                'file_path': epub_path,
                'image': image_path,
                'serves': serves.strip(),
            })
    return recipes


def _extract_thomas_keller_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '',
                                              doc_name: str = '', saved_images: Dict[str, str] = None,
                                              images_dir: str = '') -> List[Dict]:
    # Per-recipe image mapping is done in _extract_thomas_keller_recipes.  Do not
    # blanket-fill with the chapter/document image.
    return _extract_thomas_keller_recipes(soup, epub_path, doc_name, saved_images, images_dir)


def _extract_image_only_book_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Placeholder for books that are page-scanned images with no extractable text.

    Returning an empty list prevents generic extractors from inventing bogus
    recipes out of the document outline or caption text.
    """
    return []


def _extract_image_only_book_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    return []


def _extract_jerusalem_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Extract recipes from Yotam Ottolenghi's 'Jerusalem'.

    Uses classes: recipe-head for title, hang/hanging* for ingredients,
    noindent-ts for steps, and noindent2 for headnotes.
    """
    recipes = []
    for title_tag in soup.find_all('p', class_='recipe-head'):
        title = _normalize_whitespace(title_tag.get_text(' ', strip=True))
        if not title:
            continue

        serves = ''
        ingredients = []
        steps = []

        for sib in title_tag.find_next_siblings():
            if sib.name == 'p' and 'recipe-head' in sib.get('class', []):
                break
            cls = ' '.join(sib.get('class', []))
            text = _normalize_whitespace(sib.get_text(' ', strip=True))
            if not text:
                continue

            if 'hang' in cls:
                for p in sib.find_all('p'):
                    p_cls = ' '.join(p.get('class', []))
                    p_text = _normalize_whitespace(p.get_text(' ', strip=True))
                    if not p_text:
                        continue
                    if _is_serves_line(p_text):
                        serves = p_text
                    elif p_text.isupper() and len(p_text) < 80:
                        ingredients.append('--- ' + p_text)
                    else:
                        ingredients.append(p_text)
                continue

            if 'noindent-ts' in cls:
                steps.append(text)
                continue

            # Headnotes/intro text (noindent2) are skipped.

        if len(ingredients) >= 2 and len(steps) >= 1:
            recipes.append({
                'title': title,
                'ingredients': '\n'.join(ingredients).strip(),
                'steps': '\n'.join(steps).strip(),
                'source': os.path.basename(epub_path),
                'file_path': epub_path,
                'image': '',
                'serves': serves,
            })
    return recipes


def _extract_jerusalem_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_jerusalem_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


# Nopalito recipes are spread across split HTML files, so they need book-level
# streaming extraction. The per-document wrappers below are registered only so
# the book-level extractor can reuse the same image assignment pipeline.

_NOPALITO_SECTION_HEADINGS = {
    'IN THE NOPALITO KITCHEN', 'IN YOUR KITCHEN',
    'BEYOND MASA: MEXICAN PANTRY ESSENTIALS',
    'MORE WAYS TO USE YOUR MASA',
    'STORING AND USING YOUR HOMEMADE TORTILLAS',
}


def _extract_nopalito_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Per-document Nopalito extractor (used only as a fallback for single-page recipes)."""
    recipes = []
    for title_tag in soup.find_all('p', class_=['rt', 'rt1', 'r1t']):
        title = _normalize_whitespace(title_tag.get_text(' ', strip=True))
        if not title:
            continue

        serves = ''
        ingredients = []
        steps = []

        for sib in title_tag.find_next_siblings():
            cls = ' '.join(sib.get('class', []))
            text = _normalize_whitespace(sib.get_text(' ', strip=True))
            if not text:
                continue
            if 'rh' in cls.split():
                serves = text
            elif 'rhn' in cls or 'rhnf' in cls:
                continue
            elif 'rilh' in cls:
                ingredients.append('--- ' + text)
            elif any(c in cls for c in ('ril', 'rill')):
                ingredients.append(text)
            elif any(c in cls for c in ('rpf', 'rp')):
                steps.append(text)

        if len(ingredients) >= 2 and len(steps) >= 1:
            recipes.append({
                'title': title,
                'ingredients': '\n'.join(ingredients).strip(),
                'steps': '\n'.join(steps).strip(),
                'source': os.path.basename(epub_path),
                'file_path': epub_path,
                'image': '',
                'serves': serves,
            })
    return recipes


def _extract_nopalito_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_nopalito_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_nopalito_from_book(book: epub.EpubBook, epub_path: str, items: List,
                                saved_images: Dict[str, str], images_dir: str) -> List[Dict]:
    """Stream all Nopalito HTML docs together, carrying recipe state across splits."""
    recipes = []
    cur: Dict = None

    def _finalize_current():
        nonlocal cur
        if cur and len(cur.get('ingredients', [])) >= 2 and len(cur.get('steps', [])) >= 1:
            recipes.append({
                'title': cur['title'],
                'ingredients': '\n'.join(cur['ingredients']).strip(),
                'steps': '\n'.join(cur['steps']).strip(),
                'source': os.path.basename(epub_path),
                'file_path': epub_path,
                'image': cur.get('image', ''),
                'serves': cur.get('serves', '').strip(),
            })
        cur = None

    def _start_recipe(title: str, image_path: str):
        nonlocal cur
        cur = {
            'title': title,
            'serves': '',
            'ingredients': [],
            'steps': [],
            'image': image_path,
        }

    for doc_idx, item in enumerate(items):
        try:
            if item.get_type() != ebooklib.ITEM_DOCUMENT:
                continue
        except Exception:
            continue
        try:
            html = item.get_content().decode('utf-8', errors='ignore')
        except Exception:
            continue
        soup = BeautifulSoup(html, 'lxml')
        doc_name = item.get_name()
        image_path = _find_image_for_doc(items, doc_idx, soup, doc_name, saved_images, images_dir)

        paragraphs = list(soup.find_all(['p', 'div']))

        # Precompute title-bearing paragraph indices.
        title_indices = set()
        for i, p in enumerate(paragraphs):
            cls_set = set(p.get('class', []))
            text = _normalize_whitespace(p.get_text(' ', strip=True))
            if not text:
                continue
            if cls_set & {'rt', 'rt1', 'r1t'}:
                title_indices.add(i)
                continue
            if 'h1' in cls_set and text.upper() not in _NOPALITO_SECTION_HEADINGS:
                # Only treat h1 as a recipe title if recipe markers follow soon.
                for j in range(i + 1, min(len(paragraphs), i + 5)):
                    ncls = set(paragraphs[j].get('class', []))
                    if ncls & {'rh', 'ril', 'rill', 'rpf', 'rp'}:
                        title_indices.add(i)
                        break

        for i, p in enumerate(paragraphs):
            cls_set = set(p.get('class', []))
            text = _normalize_whitespace(p.get_text(' ', strip=True))
            if not text:
                continue

            if i in title_indices:
                is_sub = bool(cls_set & {'r1t'})
                _finalize_current()
                if is_sub and recipes:
                    # Sub-recipes reuse the parent recipe's image.
                    _start_recipe(text, recipes[-1].get('image', image_path))
                else:
                    _start_recipe(text, image_path)
                continue

            if cur is None:
                continue

            if 'rh' in cls_set:
                cur['serves'] += ' ' + text
            elif cls_set & {'rhn', 'rhnf'}:
                continue
            elif 'rilh' in cls_set:
                cur['ingredients'].append('--- ' + text)
            elif any(c in cls_set for c in ('ril', 'rill')):
                cur['ingredients'].append(text)
            elif any(c in cls_set for c in ('rpf', 'rp')):
                cur['steps'].append(text)

    _finalize_current()
    return recipes


# ---------------------------------------------------------------------------
# Book-specific extractors: pasta and baking batch
# ---------------------------------------------------------------------------

_FRACTION_MAP = {
    ('1', '2'): '½', ('1', '3'): '⅓', ('2', '3'): '⅔',
    ('1', '4'): '¼', ('3', '4'): '¾', ('1', '5'): '⅕',
    ('2', '5'): '⅖', ('3', '5'): '⅗', ('4', '5'): '⅘',
    ('1', '6'): '⅙', ('5', '6'): '⅚', ('1', '8'): '⅛',
    ('3', '8'): '⅜', ('5', '8'): '⅝', ('7', '8'): '⅞',
}


def _merge_fraction_spans(soup: BeautifulSoup):
    """Collapse sup/sub fraction markup into single unicode fractions so
    quantities read '2¼' instead of '2 1 / 4'. Handles '<span class="sup">1</
    span>/<span class="sub">4</span>', '<sup>1</sup>/<sub>4</sub>' and nested
    '<sup><span>1</span></sup>/<sub><span>4</span></sub>' variants."""
    from bs4.element import NavigableString, Tag
    candidates = list(soup.find_all('sup'))
    candidates += [s for s in soup.find_all('span', class_='sup') if s.find_parent('sup') is None]
    for sup in candidates:
        num = sup.get_text(strip=True)
        slash = sup.next_sibling
        if not (isinstance(slash, NavigableString) and str(slash).strip() in ('/', '⁄')):
            continue
        sub = slash.next_sibling
        if not (isinstance(sub, Tag) and (sub.name == 'sub' or 'sub' in (sub.get('class') or []))):
            continue
        frac = _FRACTION_MAP.get((num, sub.get_text(strip=True)))
        if not frac:
            continue
        sup.replace_with(NavigableString(frac))
        slash.replace_with(NavigableString(''))
        sub.replace_with(NavigableString(''))


def _new_recipe_dict(epub_path: str) -> Dict:
    return {
        'title': '',
        'ingredients': [],
        'steps': [],
        'serves': '',
        'source': os.path.basename(epub_path),
        'file_path': epub_path,
        'image': '',
    }


def _finish_recipe_dict(recipes: List[Dict], cur: Dict, min_ingredients: int = 2, min_steps: int = 1):
    """Quality-gate and normalise a partially built recipe dict, then append it."""
    if cur and len(cur.get('ingredients', [])) >= min_ingredients and len(cur.get('steps', [])) >= min_steps:
        recipes.append({
            'title': cur['title'],
            'ingredients': '\n'.join(cur['ingredients']).strip(),
            'steps': '\n'.join(cur['steps']).strip(),
            'source': cur['source'],
            'file_path': cur['file_path'],
            'image': cur.get('image', ''),
            'serves': cur.get('serves', '').strip(),
        })


def _extract_sfoglino_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '',
                                         doc_name: str = '', saved_images: Dict[str, str] = None,
                                         images_dir: str = '') -> List[Dict]:
    """Extract recipes from Evan Funke's 'American Sfoglino'.

    A recipe starts at an h2/h3/h4.section_hd (Italian name), optionally
    followed by a same-level .chapter_title heading (English name). The yield
    is p.yield, ingredients are p.ingred_sublist(t) and steps p.method_txt.
    Essay chapters also use h3.section_hd but have no ingredient/method
    paragraphs, so the quality gate drops them.
    """
    _merge_fraction_spans(soup)
    recipes = []
    body = soup.body or soup
    title_tags = [el for el in body.find_all(['h2', 'h3', 'h4'])
                  if 'section_hd' in (el.get('class') or [])]
    if not title_tags:
        return recipes

    title_images = {}
    if saved_images:
        images = [img for img in body.find_all('img') if not _is_decorative_image(img)]
        title_images = _map_images_to_titles(
            title_tags, images, direction='after',
            doc_name=doc_name, saved_images=saved_images, images_dir=images_dir)

    for i, tag in enumerate(title_tags):
        italian = _normalize_whitespace(tag.get_text(' ', strip=True))
        if not italian:
            continue
        english = ''
        nxt = tag.find_next(['h2', 'h3', 'h4'])
        if nxt is not None and nxt.name == tag.name and 'chapter_title' in (nxt.get('class') or []):
            english = _normalize_whitespace(nxt.get_text(' ', strip=True))
        title = italian
        if english and english.lower() != italian.lower():
            title = f'{italian} ({english})'

        boundary = title_tags[i + 1] if i + 1 < len(title_tags) else None
        cur = _new_recipe_dict(epub_path)
        cur['title'] = title
        for el in tag.find_all_next(['p', 'h2', 'h3', 'h4']):
            if el is boundary:
                break
            if el.name != 'p':
                continue
            cls = set(el.get('class') or [])
            text = _normalize_whitespace(el.get_text(' ', strip=True))
            if not text:
                continue
            if 'yield' in cls:
                cur['serves'] = text
            elif cls & {'ingred_sublistt', 'ingred_sublist'}:
                cur['ingredients'].append(text)
            elif 'method_txt' in cls:
                cur['steps'].append(text)
        cur['image'] = _resolve_assigned_image(title_images.get(tag), doc_name, saved_images, images_dir)
        _finish_recipe_dict(recipes, cur, min_ingredients=1)
    return recipes


def _extract_flour_water_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '',
                                            doc_name: str = '', saved_images: Dict[str, str] = None,
                                            images_dir: str = '') -> List[Dict]:
    """Extract recipes from Thomas McNaughton's 'Flour + Water: Pasta'.

    Recipes live in div.recipe blocks (plus sidebar recipes in
    div.recipe_background): h1.recipe_title / h1.recipe_title_background
    title, div.yield, h3.IL_subheader component headings, div.IL_item
    ingredients and div.method_step steps. The recipe photo sits in a
    div.recipe_image right before the recipe block.
    """
    recipes = []
    body = soup.body or soup
    blocks = body.find_all('div', class_=['recipe', 'recipe_background'])
    if not blocks:
        return recipes

    title_tags = []
    for block in blocks:
        h = block.find('h1', class_=['recipe_title', 'recipe_title_background'])
        if h:
            title_tags.append(h)
    title_images = {}
    if saved_images and title_tags:
        images = [img for img in body.find_all('img') if not _is_decorative_image(img)]
        title_images = _map_images_to_titles(
            title_tags, images, direction='before',
            doc_name=doc_name, saved_images=saved_images, images_dir=images_dir)

    for block in blocks:
        h = block.find('h1', class_=['recipe_title', 'recipe_title_background'])
        if not h:
            continue
        title = _normalize_whitespace(h.get_text(' ', strip=True))
        if not title:
            continue
        # recipe_background sidebars with long all-caps titles are essays that
        # merely mention a technique (e.g. 'BIGOLI, THE TORCHIO, AND ...');
        # genuine sidebar recipes have short title-case names.
        if 'recipe_background' in (block.get('class') or []):
            letters = re.findall(r'[A-Za-z]', title)
            if len(title) > 40 and letters and all(c.isupper() for c in letters):
                continue
        cur = _new_recipe_dict(epub_path)
        cur['title'] = title
        for el in block.find_all(['h3', 'div']):
            cls = set(el.get('class') or [])
            text = _normalize_whitespace(el.get_text(' ', strip=True))
            if not text:
                continue
            if el.name == 'h3':
                if 'IL_subheader' in cls:
                    cur['ingredients'].append('--- ' + text)
                continue
            if 'IL_item' in cls:
                cur['ingredients'].append(text)
            elif 'method_step' in cls:
                cur['steps'].append(text)
            elif 'yield' in cls and not cur['serves']:
                cur['serves'] = text
        cur['image'] = _resolve_assigned_image(title_images.get(h), doc_name, saved_images, images_dir)
        _finish_recipe_dict(recipes, cur)
    return recipes


def _extract_mastering_pasta_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '',
                                                doc_name: str = '', saved_images: Dict[str, str] = None,
                                                images_dir: str = '') -> List[Dict]:
    """Extract recipes from Marc Vetri's 'Mastering Pasta'.

    Each div.recipe_title starts a recipe; until the next one, div.yield is
    the yield, div.IL_item are ingredients and div.method_step are steps.
    Recipe photos (div.recipe_image / div.med_img) appear inside the recipe
    flow after the steps.
    """
    recipes = []
    body = soup.body or soup
    title_tags = body.find_all('div', class_='recipe_title')
    if not title_tags:
        return recipes

    title_images = {}
    if saved_images:
        images = [img for img in body.find_all('img') if not _is_decorative_image(img)]
        title_images = _map_images_to_titles(
            title_tags, images, direction='after',
            doc_name=doc_name, saved_images=saved_images, images_dir=images_dir)

    cur = None
    for el in body.find_all('div'):
        cls = set(el.get('class') or [])
        text = _normalize_whitespace(el.get_text(' ', strip=True))
        if 'recipe_title' in cls:
            _finish_recipe_dict(recipes, cur)
            cur = _new_recipe_dict(epub_path)
            cur['title'] = text
            cur['image'] = _resolve_assigned_image(title_images.get(el), doc_name, saved_images, images_dir)
            continue
        if cur is None or not text:
            continue
        if 'ingredients' in cls:
            continue  # container div holding the IL_item children
        if 'yield' in cls:
            if not cur['serves']:
                cur['serves'] = text
        elif 'IL_item' in cls:
            cur['ingredients'].append(text)
        elif 'method_step' in cls:
            cur['steps'].append(text)
        elif 'B_head' in cls:
            cur['steps'].append('--- ' + text)
    _finish_recipe_dict(recipes, cur)
    return recipes


def _extract_pasta_by_hand_recipes(soup: BeautifulSoup, epub_path: str) -> List[Dict]:
    """Extract recipes from Jenn Louis's 'Pasta by Hand'.

    One recipe per chapter document: h1.chapter_title title, p.yield,
    p.ingredients items, and steps in p.method_txt with p.text_indent
    continuations. p.center lines are sauce-pairing notes and are skipped.
    """
    _merge_fraction_spans(soup)
    title_tag = soup.find('h1', class_='chapter_title')
    if not title_tag:
        return []
    title = _normalize_whitespace(title_tag.get_text(' ', strip=True))
    if not title:
        return []
    cur = _new_recipe_dict(epub_path)
    cur['title'] = title
    for p in soup.find_all('p'):
        cls = set(p.get('class') or [])
        text = _normalize_whitespace(p.get_text(' ', strip=True))
        if not text:
            continue
        if 'yield' in cls:
            cur['serves'] = text
        elif 'ingredients' in cls:
            cur['ingredients'].append(text)
        elif cls & {'method_txt', 'text_indent'}:
            cur['steps'].append(text)
    recipes = []
    _finish_recipe_dict(recipes, cur)
    return recipes


def _extract_pasta_by_hand_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = _extract_pasta_by_hand_recipes(soup, epub_path)
    for r in recipes:
        r['image'] = image_path
    return recipes


def _extract_heavenly_cakes_from_book(book: epub.EpubBook, epub_path: str, items: List,
                                      saved_images: Dict[str, str], images_dir: str) -> List[Dict]:
    """Stream the split HTML docs of Wiley's 'Rose's Heavenly Cakes' into recipes.

    Every split page carries a div.recipeCulinaryTitle. A page that also has a
    div.calibre1 headnote or a div.recipeTime starts a new main recipe; other
    titled pages (Batter, Topping, ...) are components of the current recipe.
    Ingredients live in table.dummies-table rows (name + volume columns) and
    method steps in p.unnumbered paragraphs. Sidebar content is ignored.
    """
    recipes = []
    cur = None
    cur_promoted = False

    def _finalize():
        nonlocal cur, cur_promoted
        _finish_recipe_dict(recipes, cur)
        cur = None
        cur_promoted = False

    def _has_ingredient_table(soup) -> bool:
        for tbl in soup.find_all('table', class_='dummies-table'):
            header_row = tbl.find('tr')
            header_texts = ([td.get_text(' ', strip=True).lower()
                             for td in header_row.find_all('td')] if header_row else [])
            if 'volume' in header_texts:
                return True
        return False

    for item in items:
        try:
            if item.get_type() != ebooklib.ITEM_DOCUMENT:
                continue
        except Exception:
            continue
        try:
            html = item.get_content().decode('utf-8', errors='ignore')
        except Exception:
            continue
        soup = BeautifulSoup(html, 'lxml')
        doc_name = item.get_name()

        if soup.find('p', class_='chap-title'):
            _finalize()
            continue

        title_tag = soup.find('div', class_='recipeCulinaryTitle')
        if title_tag is not None:
            title = _normalize_whitespace(title_tag.get_text(' ', strip=True))
            is_main = bool(soup.find('div', class_='calibre1') or soup.find('div', class_='recipeTime'))
            promoted = False
            if not is_main and (cur is None or cur_promoted) and _has_ingredient_table(soup):
                # A titled page with its own ingredient table that is not part
                # of a full recipe (e.g. the back-matter basics chapter, where
                # every page is an independent small recipe) stands alone.
                is_main = True
                promoted = True
            if is_main:
                _finalize()
                cur = _new_recipe_dict(epub_path)
                cur['title'] = title
                cur_promoted = promoted
                yield_tag = soup.find('div', class_='recipeYield')
                if yield_tag:
                    cur['serves'] = _normalize_whitespace(yield_tag.get_text(' ', strip=True))
            elif cur is not None and title:
                cur['ingredients'].append('--- ' + title)
                cur['steps'].append('--- ' + title)

        if cur is None:
            continue

        if not cur.get('image'):
            for img in soup.find_all('img'):
                if _is_decorative_image(img):
                    continue
                candidate = _resolve_image_path(img.get('src') or '', doc_name, saved_images, images_dir)
                if candidate:
                    cur['image'] = candidate
                    break

        # Content belonging to the recipe starts after its title (when the doc
        # has one). An essay/sidebar heading (p.heading, p.heading1, p.sb-head)
        # marks the end of the recipe within the doc.
        if title_tag is not None:
            content_elems = title_tag.find_all_next(['table', 'p', 'div'])
        else:
            content_elems = soup.find_all(['table', 'p', 'div'])

        for el in content_elems:
            if el.find_parent('div', class_='sidebar'):
                continue
            if el.name == 'p' and set(el.get('class') or []) & {'heading', 'heading1', 'heading2', 'sb-head'}:
                break
            if el.name == 'table' and 'dummies-table' in (el.get('class') or []):
                # Only genuine ingredient tables have a Volume column header;
                # altitude charts and reference tables share the same styling.
                header_row = el.find('tr')
                header_texts = []
                if header_row:
                    header_texts = [td.get_text(' ', strip=True).lower() for td in header_row.find_all('td')]
                if 'volume' not in header_texts:
                    continue
                for tr in el.find_all('tr'):
                    tds = tr.find_all('td')
                    if not tds:
                        continue
                    if 'tb-col-head' in (tds[0].get('class') or []):
                        continue
                    name = _normalize_whitespace(tds[0].get_text(' ', strip=True))
                    vol = _normalize_whitespace(tds[1].get_text(' ', strip=True)) if len(tds) > 1 else ''
                    if not name:
                        continue
                    if vol and vol not in ('.', '•', '-'):
                        cur['ingredients'].append(f'{name} — {vol}')
                    else:
                        cur['ingredients'].append(name)
                continue
            if el.name == 'div':
                if 'recipeVariationRecipeTitle' in (el.get('class') or []):
                    text = _normalize_whitespace(el.get_text(' ', strip=True))
                    if text:
                        cur['steps'].append('--- Variation: ' + text)
                continue
            # p.unnumbered paragraphs are the method steps; p.para inside a
            # variation describes the variation and reads like a step.
            cls = set(el.get('class') or [])
            if 'unnumbered' not in cls and not ('para' in cls and el.find_parent('div', class_='recipeVariation')):
                continue
            text = _normalize_whitespace(el.get_text(' ', strip=True))
            if text:
                cur['steps'].append(text)

    _finalize()
    return recipes


def _extract_heavenly_cakes_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    # Rose's Heavenly Cakes recipes span many small split files, so extraction
    # happens book-wide in _extract_heavenly_cakes_from_book (called from
    # extract_recipes_from_file). This per-document extractor intentionally
    # returns nothing; its registration only stops the generic extractors.
    return []


_CAKE_BIBLE_META_LABEL = re.compile(
    r'^(PAN TYPE|FINISHED HEIGHT|STORE|COMPLEMENTARY ADORNMENT|SERVE|'
    r'POINTERS FOR SUCCESS|SPECIAL EQUIPMENT|OPTIONAL|NOTE|TIMING)\b', re.I)
_CAKE_BIBLE_STOP_LABEL = re.compile(r'^(UNDERSTANDING|VARIATIONS)\b', re.I)


def _cake_bible_food_image(soup: BeautifulSoup, title: str, doc_name: str,
                           saved_images: Dict[str, str], images_dir: str) -> str:
    """Find the recipe photo in a Cake Bible document.

    Food photos are JPEGs, usually with a caption naming the recipe; pan
    diagrams and technique figures are PNGs and are never used.
    """
    title_key = re.sub(r'[^a-z0-9]+', ' ', title.lower()).strip()
    for cap in soup.find_all('p', class_=['caption', 'subcaption']):
        cap_text = re.sub(r'[^a-z0-9]+', ' ', cap.get_text(' ', strip=True).lower())
        if title_key and title_key in cap_text:
            img = cap.find_previous('img')
            if img:
                resolved = _resolve_image_path(img.get('src') or '', doc_name, saved_images, images_dir)
                if resolved:
                    return resolved
    for img in soup.find_all('img'):
        src = img.get('src') or ''
        if not src.lower().endswith(('.jpg', '.jpeg')):
            continue
        resolved = _resolve_image_path(src, doc_name, saved_images, images_dir)
        if resolved:
            return resolved
    return ''


def _extract_cake_bible_recipes(soup: BeautifulSoup, epub_path: str,
                                doc_name: str = '', saved_images: Dict[str, str] = None,
                                images_dir: str = '') -> List[Dict]:
    """Extract recipes from Rose Levy Beranbaum's 'The Cake Bible'.

    One recipe per document: h1.chaptertitle title, a 'SERVES n' p.center, and
    a 4-column ingredient table (tr.ING / tr.ING-background rows of
    ingredient | volume | ounces | grams). Method steps are the p.left/p.follow
    paragraphs after the table; pan/store/serve/pointers meta blocks, the
    'UNDERSTANDING' essay and 'VARIATIONS' are excluded.

    Showcase cakes have no ingredient table: their 'CAKE COMPONENTS' list is
    used as ingredients and the 'METHOD FOR ASSEMBLING CAKE' paragraphs as
    steps.
    """
    title_tag = soup.find('h1', class_='chaptertitle')
    if not title_tag:
        return []
    title = _normalize_whitespace(title_tag.get_text(' ', strip=True))
    if not title:
        return []

    cur = _new_recipe_dict(epub_path)
    cur['title'] = title
    for p in soup.find_all('p', class_='center'):
        text = _normalize_whitespace(p.get_text(' ', strip=True))
        if re.match(r'^(SERVES|MAKES|YIELD)\b', text, re.I):
            cur['serves'] = text
            break

    table = soup.find('table')
    # Only genuine recipe tables start with an INGREDIENTS header row; chapter
    # introductions and reference chapters (ingredients, equipment, formulas)
    # carry informational tables with different headers.
    if table is not None:
        first_row = table.find('tr')
        first_cell = first_row.find('td') if first_row else None
        header = _normalize_whitespace(first_cell.get_text(' ', strip=True)) if first_cell else ''
        if header.upper() != 'INGREDIENTS':
            table = None
    if table is not None:
        for tr in table.find_all('tr'):
            cells = [_normalize_whitespace(td.get_text(' ', strip=True)) for td in tr.find_all('td')]
            if not cells or not cells[0]:
                continue
            if cells[0].upper() == 'INGREDIENTS' or cells[0].lower() == 'room temperature':
                continue
            if len(cells) > 1 and cells[1].upper() in ('MEASURE', 'VOLUME'):
                continue
            if len(cells) < 2:
                cur['ingredients'].append('--- ' + cells[0])
                continue
            measure = cells[1] if cells[1] not in ('•', '.', '-') else ''
            cur['ingredients'].append(f'{cells[0]} — {measure}' if measure else cells[0])

        # Method steps follow the table; meta blocks (pan type, store, serve,
        # pointers) are skipped, including the description line that follows a
        # label-only paragraph.
        skip_next = False
        for p in table.find_all_next(['p', 'h1']):
            if p.name == 'h1':
                break
            if not (set(p.get('class') or []) & {'left', 'follow'}):
                continue
            text = _normalize_whitespace(p.get_text(' ', strip=True))
            if not text:
                continue
            if _CAKE_BIBLE_STOP_LABEL.match(text):
                break
            if _CAKE_BIBLE_META_LABEL.match(text):
                skip_next = len(text) < 45 or text.endswith(':')
                continue
            if skip_next:
                skip_next = False
                continue
            cur['steps'].append(text)
    else:
        # Showcase cake: component list + assembly method.
        mode = 'skip'
        for p in title_tag.find_all_next(['p', 'h1']):
            if p.name == 'h1':
                break
            if not (set(p.get('class') or []) & {'left', 'follow'}):
                continue
            text = _normalize_whitespace(p.get_text(' ', strip=True))
            if not text:
                continue
            if re.match(r'^CAKE COMPONENTS\b', text, re.I):
                mode = 'ing'
                continue
            if re.match(r'^METHOD FOR ASSEMBLING\b', text, re.I):
                mode = 'steps'
                continue
            if _CAKE_BIBLE_META_LABEL.match(text) or _CAKE_BIBLE_STOP_LABEL.match(text):
                mode = 'skip'
                continue
            if mode == 'ing':
                cur['ingredients'].append(text)
            elif mode == 'steps':
                cur['steps'].append(text)

    if saved_images:
        cur['image'] = _cake_bible_food_image(soup, title, doc_name, saved_images, images_dir)
    recipes = []
    _finish_recipe_dict(recipes, cur)
    return recipes


def _extract_cake_bible_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '',
                                           doc_name: str = '', saved_images: Dict[str, str] = None,
                                           images_dir: str = '') -> List[Dict]:
    # Per-recipe food photos are picked inside _extract_cake_bible_recipes;
    # pan diagrams (the per-document fallback image) are never forced on.
    return _extract_cake_bible_recipes(soup, epub_path, doc_name, saved_images, images_dir)


def _extract_baked_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '',
                                      doc_name: str = '', saved_images: Dict[str, str] = None,
                                      images_dir: str = '') -> List[Dict]:
    """Extract recipes from 'Baked: New Frontiers in Baking'.

    Recipes start at h3.h3cap / h3.h3a titles. p.recipe1 is the yield,
    p.recipe2 a component heading, p.recipea/p.recipe ingredients,
    p.noindentmake a method heading and p.noindent1(b/c) the steps.
    'BAKED NOTE' boxes and sidebars are skipped. Food photos (fNNNN-NN.jpg)
    sit right before the title.
    """
    recipes = []
    body = soup.body or soup
    title_tags = [el for el in body.find_all('h3') if set(el.get('class') or []) & {'h3cap', 'h3a'}]
    if not title_tags:
        return recipes

    title_images = {}
    if saved_images:
        images = [img for img in body.find_all('img')
                  if re.search(r'/f\d{4}-\d+\.jpe?g', img.get('src') or '', re.I)]
        title_images = _map_images_to_titles(
            title_tags, images, direction='before',
            doc_name=doc_name, saved_images=saved_images, images_dir=images_dir)

    cur = None
    for el in body.find_all(['h3', 'p']):
        cls = set(el.get('class') or [])
        text = _normalize_whitespace(el.get_text(' ', strip=True))
        if el.name == 'h3':
            if cls & {'h3cap', 'h3a'}:
                _finish_recipe_dict(recipes, cur)
                cur = _new_recipe_dict(epub_path)
                cur['title'] = text
                cur['image'] = _resolve_assigned_image(title_images.get(el), doc_name, saved_images, images_dir)
            continue
        if cur is None or not text:
            continue
        if 'recipe1' in cls:
            cur['serves'] = text
        elif 'recipe2' in cls:
            cur['ingredients'].append('--- ' + text)
        elif cls & {'recipea', 'recipe'}:
            cur['ingredients'].append(text)
        elif 'noindentmake' in cls:
            cur['steps'].append(text)
        elif cls & {'noindent1', 'noindent1b', 'noindent1c'}:
            cur['steps'].append(text)
    _finish_recipe_dict(recipes, cur)
    return recipes


def _extract_sallys_cookies_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '',
                                               doc_name: str = '', saved_images: Dict[str, str] = None,
                                               images_dir: str = '') -> List[Dict]:
    """Extract recipes from 'Sally's Cookie Addiction'.

    Recipes start at h3.h3a-h3f/h3r/h3ra titles (letter-spaced small-cap
    markup, read without separators). p.prep* holds the prep/yield line,
    p.item/p.itemb are ingredients, p.item-head a component heading, and
    p.nlisti/p.nlist1i the numbered steps. MAKE-AHEAD TIP and SALLY SAYS boxes
    are skipped. Quantities use sup/sub fraction spans which are merged into
    unicode fractions first.
    """
    _merge_fraction_spans(soup)
    recipes = []
    body = soup.body or soup
    title_tags = [el for el in body.find_all('h3')
                  if any(re.fullmatch(r'h3[a-z]+', c) for c in (el.get('class') or []))]
    if not title_tags:
        return recipes

    title_images = {}
    if saved_images:
        images = [img for img in body.find_all('img')
                  if re.search(r'/f\d{4}-\d+\.jpe?g', img.get('src') or '', re.I)]
        title_images = _map_images_to_titles(
            title_tags, images, direction='before',
            doc_name=doc_name, saved_images=saved_images, images_dir=images_dir)

    cur = None
    for el in body.find_all(['h3', 'p']):
        text = _normalize_whitespace(el.get_text('', strip=False) if el.name == 'h3'
                                     else el.get_text(' ', strip=True))
        if el.name == 'h3':
            if any(re.fullmatch(r'h3[a-z]+', c) for c in (el.get('class') or [])):
                _finish_recipe_dict(recipes, cur)
                cur = _new_recipe_dict(epub_path)
                title = re.sub(r'\s+([,.;:!?])', r'\1', text).lstrip('◁ ').strip()
                cur['title'] = title
                cur['image'] = _resolve_assigned_image(title_images.get(el), doc_name, saved_images, images_dir)
            continue
        if cur is None or not text:
            continue
        cls = set(el.get('class') or [])
        if any(re.fullmatch(r'prep[a-z]?', c) for c in cls):
            m = re.search(r'YIELD:\s*(.+)$', text, re.I)
            cur['serves'] = m.group(1).strip() if m else text
        elif 'item-head' in cls:
            cur['ingredients'].append('--- ' + text)
        elif cls & {'item', 'itemb'}:
            cur['ingredients'].append(text)
        elif cls & {'nlisti', 'nlist1i'}:
            cur['steps'].append(text)
        elif 'noindent' in cls and cur['ingredients']:
            cur['steps'].append(text)
    _finish_recipe_dict(recipes, cur)
    return recipes


def _extract_good_to_grain_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '',
                                              doc_name: str = '', saved_images: Dict[str, str] = None,
                                              images_dir: str = '') -> List[Dict]:
    """Extract recipes from Kim Boyce's 'Good to the Grain'.

    Recipes start at h3.h3a titles. p.center holds 'SERVES n'/'MAKES n',
    p.cook1 is an ingredient or a component heading (text ending in ':'),
    p.cook are ingredients and p.numberlist(t) the numbered steps. Notes and
    tip boxes are skipped. Food photos (fNNNN-NN.jpg) precede the title.
    """
    recipes = []
    body = soup.body or soup
    title_tags = body.find_all('h3', class_='h3a')
    if not title_tags:
        return recipes

    title_images = {}
    if saved_images:
        images = [img for img in body.find_all('img')
                  if re.search(r'/f\d{4}-\d+\.jpe?g', img.get('src') or '', re.I)]
        title_images = _map_images_to_titles(
            title_tags, images, direction='before',
            doc_name=doc_name, saved_images=saved_images, images_dir=images_dir)

    cur = None
    for el in body.find_all(['h3', 'p']):
        cls = set(el.get('class') or [])
        text = _normalize_whitespace(el.get_text(' ', strip=True))
        if el.name == 'h3':
            if 'h3a' in cls:
                _finish_recipe_dict(recipes, cur)
                cur = _new_recipe_dict(epub_path)
                cur['title'] = text
                cur['image'] = _resolve_assigned_image(title_images.get(el), doc_name, saved_images, images_dir)
            continue
        if cur is None or not text:
            continue
        if 'center' in cls and re.match(r'^(SERVES|MAKES|YIELD)\b', text, re.I):
            cur['serves'] = text
        elif 'cook1' in cls:
            if text.endswith(':'):
                cur['ingredients'].append('--- ' + text)
            else:
                cur['ingredients'].append(text)
        elif 'cook' in cls:
            cur['ingredients'].append(text)
        elif cls & {'numberlistt', 'numberlist'}:
            cur['steps'].append(text)
    _finish_recipe_dict(recipes, cur)
    return recipes


def _extract_fwsy_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '',
                                     doc_name: str = '', saved_images: Dict[str, str] = None,
                                     images_dir: str = '') -> List[Dict]:
    """Extract recipes from Ken Forkish's 'Flour Water Salt Yeast'.

    Each div.recipe_title starts a recipe. div.yield is the yield line,
    ingredients come from the recipe table (ingredient | weight | volume |
    baker's % rows), and steps are div.step / div.step_extract /
    div.step_indent. Fermentation schedules (div.yield1), headnotes and
    sidebars are skipped. The recipe photo and its caption sit right before
    the title.
    """
    recipes = []
    body = soup.body or soup
    title_tags = body.find_all('div', class_='recipe_title')
    if not title_tags:
        return recipes

    title_images = {}
    if saved_images:
        images = [img for img in body.find_all('img') if not _is_decorative_image(img)]
        title_images = _map_images_to_titles(
            title_tags, images, direction='before',
            doc_name=doc_name, saved_images=saved_images, images_dir=images_dir)

    cur = None
    for el in body.find_all(['div', 'table']):
        cls = set(el.get('class') or [])
        if 'recipe_title' in cls:
            _finish_recipe_dict(recipes, cur)
            cur = _new_recipe_dict(epub_path)
            cur['title'] = _normalize_whitespace(el.get_text(' ', strip=True))
            cur['image'] = _resolve_assigned_image(title_images.get(el), doc_name, saved_images, images_dir)
            continue
        if cur is None:
            continue
        if el.name == 'table':
            for tr in el.find_all('tr'):
                cells = [_normalize_whitespace(td.get_text(' ', strip=True))
                         for td in tr.find_all(['td', 'th'])]
                if not cells or not cells[0]:
                    continue
                if cells[0].upper() == 'INGREDIENT':
                    continue
                if len(cells) < 3:
                    cur['ingredients'].append('--- ' + cells[0])
                    continue
                qty = cells[1]
                vol = cells[2]
                line = cells[0]
                if qty:
                    line += f' — {qty}'
                if vol:
                    line += f' ({vol})'
                cur['ingredients'].append(line)
            continue
        text = _normalize_whitespace(el.get_text(' ', strip=True))
        if not text:
            continue
        if 'yield' in cls:
            if not cur['serves']:
                cur['serves'] = text
        elif 'ingredients_list' in cls:
            continue  # container div holding the IL_item children
        elif 'IL_item' in cls:
            cur['ingredients'].append(text)
        elif any(c.startswith('step') for c in cls):
            cur['steps'].append(text)
    _finish_recipe_dict(recipes, cur)
    return recipes


def _extract_vegan_cupcakes_recipes_with_image(soup: BeautifulSoup, epub_path: str, image_path: str = '',
                                               doc_name: str = '', saved_images: Dict[str, str] = None,
                                               images_dir: str = '') -> List[Dict]:
    """Extract recipes from 'Vegan Cupcakes Take Over the World'.

    Recipes start at h1.h1 titles with an optional h2.h1 subtitle ('with ...')
    or 'MAKES 12 CUPCAKES' yield line. 'INGREDIENTS' / 'DIRECTIONS' marker
    divs (div.tx1.sgc-1) switch sections; ingredients are <br>-separated lines
    in div.atx1.tx1, steps are div.lsl1. 'Variations' blocks and decorating
    sidebars are skipped. Non-recipe chapters (ingredient guides, equipment)
    have no INGREDIENTS/DIRECTIONS markers and fall out through the gate.
    """
    from bs4.element import NavigableString
    recipes = []
    body = soup.body or soup
    title_tags = [el for el in body.find_all('h1') if 'h1' in (el.get('class') or [])]
    if not title_tags:
        return recipes

    title_images = {}
    if saved_images:
        images = [img for img in body.find_all('img') if not _is_decorative_image(img)]
        title_images = _map_images_to_titles(
            title_tags, images, direction='after',
            doc_name=doc_name, saved_images=saved_images, images_dir=images_dir)

    cur = None
    mode = 'off'

    def _add_atx_ingredients(div):
        for br in div.find_all('br'):
            br.replace_with(NavigableString('\n'))
        for line in div.get_text().split('\n'):
            line = _normalize_whitespace(line)
            if not line:
                continue
            qty_start = re.match(r'^[\d¼½¾⅓⅔⅛⅜⅝⅞⅕⅖⅗⅘⅙⅚]', line)
            paren_open = (cur['ingredients'] and
                          cur['ingredients'][-1].count('(') > cur['ingredients'][-1].count(')'))
            if cur['ingredients'] and (paren_open or (not qty_start and line[0].islower())):
                # Continuation of a wrapped ingredient line.
                cur['ingredients'][-1] += ' ' + line
            else:
                cur['ingredients'].append(line)

    for el in body.find_all(['h1', 'h2', 'div']):
        cls = set(el.get('class') or [])
        text = _normalize_whitespace(el.get_text(' ', strip=True))
        if el.name == 'h1' and 'h1' in cls:
            _finish_recipe_dict(recipes, cur)
            cur = _new_recipe_dict(epub_path)
            title = _normalize_whitespace(el.get_text('', strip=False))
            cur['title'] = re.sub(r'\s+([,.;:!?])', r'\1', title)
            cur['image'] = _resolve_assigned_image(title_images.get(el), doc_name, saved_images, images_dir)
            mode = 'head'
            continue
        if cur is None or not text:
            continue
        if el.name == 'h2' and 'h1' in cls:
            if re.match(r'^MAKES\b', text, re.I):
                cur['serves'] = text
            elif not cur['ingredients'] and not cur['steps']:
                sub = _normalize_whitespace(el.get_text('', strip=False))
                cur['title'] += ' ' + re.sub(r'\s+([,.;:!?])', r'\1', sub)
            continue
        if el.name != 'div':
            continue
        if 'ctag1' in cls and re.match(r'^MAKES\b', text, re.I):
            cur['serves'] = text
            continue
        if 'tx1' in cls:
            upper = text.upper()
            if upper == 'INGREDIENTS':
                mode = 'ing'
                continue
            if upper == 'DIRECTIONS':
                mode = 'steps'
                continue
            if upper.startswith(('TO MAKE', 'TO ASSEMBLE')) and mode == 'steps' and len(text) < 60:
                cur['steps'].append(text)
                continue
            if text.lower().startswith('variation'):
                mode = 'off'
                continue
            if mode == 'ing' and text.lower().startswith('for the') and len(text) < 60:
                cur['ingredients'].append('--- ' + text)
                continue
            if 'atx1' in cls:
                if mode in ('ing', 'steps'):
                    _add_atx_ingredients(el)
                continue
            if mode == 'ing' and re.match(r'^[\d¼½¾⅓⅔⅛⅜⅝⅞⅕⅖⅗⅘⅙⅚]', text):
                # Some recipes list their ingredients in a plain div.tx1 block.
                _add_atx_ingredients(el)
                continue
        if 'lsl1' in cls and mode == 'steps':
            cur['steps'].append(text)
    _finish_recipe_dict(recipes, cur)
    return recipes


# Register extractors in priority order: schema.org first, then known book
# extractors, then generic paragraph/heading/fallback extractors.
register_extractor(_schema_org_predicate, _extract_schema_org_recipes, 'schema.org')
register_extractor(_source_predicate('30-minute meals'), _extract_30min_meals_with_image, '30-minute meals')
register_extractor(_source_predicate('super food family classics'), _extract_superfood_recipes_with_image, 'super food family classics')
register_extractor(_source_predicate('northern italy'), _extract_northern_italy_recipes_with_image, 'northern italy')
register_extractor(_source_predicate('every grain of rice'), _extract_every_grain_recipes_with_image, 'every grain of rice')
register_extractor(_source_predicate('simple one-pan wonders'), _extract_one_pan_wonders_recipes_with_image, 'one-pan wonders')
register_extractor(_source_predicate('gordon ramsay'), _extract_gordon_ramsay_recipes_with_image, 'gordon ramsay')
register_extractor(_source_predicate('simply japanese'), _extract_simply_japanese_recipes_with_image, 'simply japanese')
register_extractor(_source_predicate('plenty more'), _extract_plenty_more_recipes_with_image, 'plenty more')
register_extractor(_source_predicate('flavour'), _extract_flavour_recipes_with_image, 'flavour')
register_extractor(_source_predicate('plenty_'), _extract_plenty_recipes_with_image, 'plenty 2011')
register_extractor(_source_predicate('veganomicon'), _extract_veganomicon_recipes_with_image, 'veganomicon')
register_extractor(_source_predicate('french provincial'), _extract_french_provincial_recipes_with_image, 'french provincial')
register_extractor(_source_predicate('delias cakes'), _extract_delias_cakes_recipes_with_image, "delia's cakes")
register_extractor(_source_predicate('good things'), _extract_good_things_recipes_with_image, 'good things')
register_extractor(_source_predicate('everyday super food'), _extract_everyday_super_food_recipes_with_image, 'everyday super food')
register_extractor(_source_predicate('jamie oliver - veg'), _extract_jamie_veg_recipes_with_image, 'jamie veg')
register_extractor(_source_predicate('seven fires'), _extract_seven_fires_recipes_with_image, 'seven fires')
register_extractor(_source_predicate('cocolat'), _extract_cocolat_recipes_with_image, 'cocolat')
register_extractor(_source_predicate('kitchen diaries'), _extract_kitchen_diaries_recipes_with_image, 'kitchen diaries')
register_extractor(_source_predicate('how to eat'), _extract_nigella_how_to_eat_recipes_with_image, 'nigella how to eat')
register_extractor(_source_predicate('domestic goddess'), _extract_nigella_domestic_goddess_recipes_with_image, 'nigella domestic goddess')
register_extractor(_source_predicate('artful way to plant-based'), _extract_artful_recipes_with_image, 'artful plant-based')
register_extractor(_source_predicate('my mediterranean life'), _extract_artful_recipes_with_image, 'my mediterranean life')
register_extractor(_source_predicate('flexible pescatarian'), _extract_flexible_pescatarian_recipes_with_image, 'flexible pescatarian')
register_extractor(_source_predicate('food of spain'), _extract_food_of_spain_recipes_with_image, 'food of spain')
register_extractor(_source_predicate('half baked harvest'), _extract_half_baked_harvest_recipes_with_image, 'half baked harvest')
register_extractor(_source_predicate('the mediterranean dish'), _extract_half_baked_harvest_recipes_with_image, 'the mediterranean dish')
register_extractor(_source_predicate('complete plant-based'), _extract_complete_plant_based_recipes_with_image, 'complete plant-based')
register_extractor(_source_predicate('secret to delicious vegan cooking'), _extract_secret_to_delicious_recipes_with_image, 'secret to delicious vegan cooking')
register_extractor(_source_predicate('vegan richa'), _extract_vegan_richa_recipes_with_image, 'vegan richa')
register_extractor(_source_predicate('green burgers'), _extract_green_burgers_recipes_with_image, 'green burgers')
register_extractor(_source_predicate('appetites'), _extract_appetites_recipes_with_image, 'appetites')
register_extractor(_source_predicate('australian food'), _extract_australian_food_recipes_with_image, 'australian food')
register_extractor(_source_predicate('community'), _extract_community_salad_recipes_with_image, 'community salad')
register_extractor(_source_predicate('cool beans'), _extract_cool_beans_recipes_with_image, 'cool beans')
register_extractor(_source_predicate('first generation'), _extract_first_generation_recipes_with_image, 'first generation')
register_extractor(_source_predicate('into the vietnamese kitchen'), _extract_into_vietnamese_recipes_with_image, 'into the vietnamese kitchen')
register_extractor(_source_predicate('good bite'), _extract_good_bite_recipes_with_image, 'good bite')
register_extractor(_source_predicate('estela'), _extract_estela_recipes_with_image, 'estela')
register_extractor(_source_predicate('thomas keller'), _extract_thomas_keller_recipes_with_image, 'thomas keller')
register_extractor(_source_predicate('street food asia'), _extract_street_food_asia_recipes_with_image, 'street food asia')
register_extractor(_source_predicate('new kitchen'), _extract_image_only_book_recipes_with_image, 'new kitchen')
register_extractor(_source_predicate('forest feast'), _extract_image_only_book_recipes_with_image, 'forest feast')
register_extractor(_source_predicate('nopalito'), _extract_nopalito_recipes_with_image, 'nopalito')
register_extractor(_source_predicate('jerusalem'), _extract_jerusalem_recipes_with_image, 'jerusalem')
register_extractor(_source_predicate('american sfoglino'), _extract_sfoglino_recipes_with_image, 'american sfoglino')
register_extractor(_source_predicate('flour + water'), _extract_flour_water_recipes_with_image, 'flour + water')
register_extractor(_source_predicate('mastering pasta'), _extract_mastering_pasta_recipes_with_image, 'mastering pasta')
register_extractor(_source_predicate('pasta by hand'), _extract_pasta_by_hand_recipes_with_image, 'pasta by hand')
register_extractor(_source_predicate('heavenly cakes'), _extract_heavenly_cakes_recipes_with_image, "rose's heavenly cakes")
register_extractor(_source_predicate('cake bible'), _extract_cake_bible_recipes_with_image, 'the cake bible')
register_extractor(_source_predicate('baked_'), _extract_baked_recipes_with_image, 'baked new frontiers')
register_extractor(_source_predicate('cookie addiction'), _extract_sallys_cookies_recipes_with_image, "sally's cookie addiction")
register_extractor(_source_predicate('good to the grain'), _extract_good_to_grain_recipes_with_image, 'good to the grain')
register_extractor(_source_predicate('flour water salt yeast'), _extract_fwsy_recipes_with_image, 'flour water salt yeast')
register_extractor(_source_predicate('vegan cupcakes'), _extract_vegan_cupcakes_recipes_with_image, 'vegan cupcakes take over the world')
register_extractor(_always_true_predicate, _extract_paragraph_recipes_with_image, 'paragraph')
register_extractor(_always_true_predicate, _extract_heading_recipes_with_image, 'heading')
register_extractor(_always_true_predicate, _extract_fallback_with_image, 'fallback', is_fallback=True)


def _extract_from_soup(soup: BeautifulSoup, epub_path: str, image_path: str = '',
                        doc_name: str = '', saved_images: Dict[str, str] = None,
                        images_dir: str = '') -> List[Dict]:
    """Run the registered extractors until one returns recipe candidates.

    Once a book-specific extractor (one whose predicate depends on the source
    file name) has matched, stop and return its result even if it is empty. This
    prevents the generic extractors from inserting garbage into books that have
    a dedicated extractor.
    """
    matched_book_specific = False
    for entry in _EXTRACTORS:
        if not entry['predicate'](soup, epub_path):
            continue
        if matched_book_specific:
            # A book-specific extractor already matched; don't run generic/fallback
            # extractors for this file.
            continue
        if getattr(entry['predicate'], 'is_book_specific', False):
            matched_book_specific = True
        # Book-aware extractors can accept per-document image context so they
        # can assign images to individual recipes.  Older extractors only take
        # (soup, epub_path, image_path).
        try:
            recipes = entry['extract'](soup, epub_path, image_path, doc_name, saved_images, images_dir)
        except TypeError:
            recipes = entry['extract'](soup, epub_path, image_path)
        if not recipes:
            continue
        # Ensure every recipe has an image assigned, but only blanket-fill with
        # the per-document fallback image when the extractor did not assign any
        # per-recipe images of its own.  Book-specific extractors that set some
        # images intentionally leave others blank rather than forcing a chapter-
        # opener or unrelated neighbour photo onto every recipe.
        has_extractor_image = any(r.get('image') for r in recipes)

        # For generic extractors, try to map distinct images to individual recipes.
        if matched_book_specific is False and recipes and saved_images:
            _assign_per_recipe_images(soup, recipes, doc_name, saved_images, images_dir)
            # If per-recipe mapping succeeded, don't blanket-fill with the doc image.
            if any(r.get('image') for r in recipes):
                has_extractor_image = True

        for r in recipes:
            if not r.get('image') and not has_extractor_image:
                r['image'] = image_path
        return recipes
    return []


def _extract_fallback(soup: BeautifulSoup, epub_path: str, image_path: str = '') -> List[Dict]:
    recipes = []
    lines = _text_lines_from_html(str(soup))
    for i, line in enumerate(lines):
        if not re.search(r"\b(" + "|".join(RECIPE_KEYS) + r")\b", line, re.I):
            continue
        title = None
        for j in range(i - 1, max(-1, i - 12), -1):
            candidate = lines[j].strip()
            if not candidate or len(candidate) < 4 or len(candidate) > 120:
                continue
            if re.match(r'^[0-9]+[\).\s]+$', candidate) or re.match(r'^\d+$', candidate):
                continue
            if not re.search(r'[A-Za-z]', candidate):
                continue
            if re.match(r'^(preheat|serve|serves|using|mix|add|pour|place|heat|cook|bake|chop|slice)\b', candidate.lower()):
                continue
            if re.search(r'\b(gram|g|kg|ml|l|cup|cups|tbsp|tsp|oz|ounce|ounces)\b', candidate.lower()):
                continue
            if len(candidate) > 60 and '.' in candidate:
                continue
            title = candidate
            break
        ingredients = []
        k = i + 1
        while k < len(lines) and not re.search(r"\b(directions|method|instructions|preparation|steps|servings)\b", lines[k], re.I):
            ingredients.append(lines[k])
            k += 1
        steps = []
        for m in range(k, min(len(lines), k + 300)):
            if re.search(r"\b(directions|method|instructions|preparation|steps)\b", lines[m], re.I):
                n = m + 1
                while n < len(lines) and not re.search(r"\b(ingredients?|recipe|servings)\b", lines[n], re.I):
                    steps.append(lines[n])
                    n += 1
                break
        final_title = title or os.path.splitext(os.path.basename(epub_path))[0]
        final_title = re.sub(r"^[0-9]+[\).\s]+", "", final_title).strip()
        recipes.append({
            'title': final_title,
            'ingredients': '\n'.join(ingredients).strip(),
            'steps': '\n'.join(steps).strip(),
            'source': os.path.basename(epub_path),
            'file_path': epub_path,
            'image': image_path,
        })
    return recipes


def _images_dir_for_epub(epub_path: str) -> str:
    """Return a short, unique directory name under static/epub_images for an EPUB.
    The full basename can be too long for Windows paths, so we use a truncated
    slug plus a hash of the full basename.
    """
    import hashlib
    base = os.path.splitext(os.path.basename(epub_path))[0]
    slug = re.sub(r'[^A-Za-z0-9]+', '_', base).strip('_')[:30]
    h = hashlib.md5(base.encode('utf-8', errors='ignore')).hexdigest()[:8]
    return f"{slug}_{h}"


# Image compression settings --------------------------------------------------
_MAX_IMAGE_DIMENSION = 1200
_JPEG_QUALITY = 82


def _compress_image(content: bytes) -> bytes:
    """Resize and recompress raster image bytes.

    Returns the original bytes if compression fails or does not reduce size.
    GIFs, SVGs, and very small icons are left untouched.
    """
    if not _PIL_AVAILABLE:
        return content
    try:
        from io import BytesIO
        original = BytesIO(content)
        img = PILImage.open(original)
        # Leave GIF animations / tiny icons alone.
        if img.format == 'GIF' or img.width <= 64 or img.height <= 64:
            return content
        img.thumbnail((_MAX_IMAGE_DIMENSION, _MAX_IMAGE_DIMENSION), PILImage.LANCZOS)
        out = BytesIO()
        # Convert RGBA/palette to RGB for JPEG output.
        if img.mode in ('RGBA', 'P', 'LA'):
            background = PILImage.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'RGBA':
                background.paste(img, mask=img.split()[3])
            elif img.mode == 'LA':
                background.paste(img, mask=img.split()[1])
            else:
                background.paste(img)
            img = background
        elif img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        img.save(out, format='JPEG', quality=_JPEG_QUALITY, optimize=True)
        compressed = out.getvalue()
        return compressed if len(compressed) < len(content) else content
    except Exception:
        return content


def _collect_referenced_images(book: epub.EpubBook) -> set:
    """Return the set of normalized image paths referenced in document items.

    Only images actually referenced by an <img> tag in the EPUB text are kept.
    """
    refs = set()
    for item in book.get_items():
        try:
            if item.get_type() != ebooklib.ITEM_DOCUMENT:
                continue
            html = item.get_content().decode('utf-8', errors='ignore')
            soup = BeautifulSoup(html, 'lxml')
            doc_name = item.get_name()
            for img in soup.find_all('img'):
                src = img.get('src') or ''
                if not src:
                    continue
                src = unquote(src).replace('\\', '/').lstrip('/')
                refs.add(src)
                refs.add(os.path.basename(src))
                if doc_name:
                    doc_dir = os.path.dirname(doc_name.replace('\\', '/'))
                    resolved = os.path.normpath(os.path.join(doc_dir, src)).replace('\\', '/')
                    refs.add(resolved)
        except Exception:
            continue
    return refs


def _save_epub_images(book: epub.EpubBook, images_dir: str, referenced: set = None) -> Dict[str, str]:
    """Save (and compress) referenced image items from the EPUB to images_dir.

    Only images whose path or basename appears in ``referenced`` are written.
    Images are resized to fit within ``_MAX_IMAGE_DIMENSION`` and saved as
    quality-82 JPEG when this reduces file size.

    Return a mapping from the normalized image name (relative to EPUB root)
    to the absolute file path where it was saved.
    """
    os.makedirs(images_dir, exist_ok=True)
    saved: Dict[str, str] = {}
    for item in book.get_items():
        try:
            if item.get_type() != ebooklib.ITEM_IMAGE:
                continue
            fname = None
            try:
                fname = item.get_name()
            except Exception:
                fname = getattr(item, 'file_name', None) or getattr(item, 'id', None)
            if not fname:
                continue
            fname = fname.replace('\\', '/').lstrip('/')
            if referenced is not None:
                if fname not in referenced and os.path.basename(fname) not in referenced:
                    continue
            content = item.get_content()
            compressed = _compress_image(content)
            out_path = os.path.join(images_dir, fname)
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, 'wb') as f:
                f.write(compressed)
            # index by basename and by full relative path
            saved[fname] = out_path
            saved[os.path.basename(fname)] = out_path
        except Exception:
            continue
    return saved


def _resolve_image_path(src: str, doc_name: str, saved_images: Dict[str, str], images_dir: str) -> str:
    """Map an <img src=...> reference to the saved image file path."""
    if not src:
        return ''
    src = unquote(src).replace('\\', '/').lstrip('/')
    # try direct lookup
    if src in saved_images:
        return saved_images[src]
    # try basename
    bn = os.path.basename(src)
    if bn in saved_images:
        return saved_images[bn]
    # resolve relative to the document
    if doc_name:
        doc_dir = os.path.dirname(doc_name.replace('\\', '/'))
        resolved = os.path.normpath(os.path.join(doc_dir, src)).replace('\\', '/')
        if resolved in saved_images:
            return saved_images[resolved]
    # last resort: search saved images by basename match
    for k, v in saved_images.items():
        if os.path.basename(k) == bn:
            return v
    return ''


def _image_path_to_relative(path: str) -> str:
    """Convert an absolute or /static image path to a project-relative path.

    Storing relative paths in JSON/DB makes the data portable and lets the API
    decide how to serve it.
    """
    if not path:
        return ''
    norm = os.path.normpath(path).replace('\\', '/')
    if norm.startswith('static/'):
        return norm
    if '/static/epub_images/' in norm:
        return 'epub_images/' + norm.split('/static/epub_images/')[-1]
    if norm.startswith('/static/'):
        return norm[len('/static/'):]
    if norm.startswith('/'):
        return norm.lstrip('/')
    return path


def _image_path_to_url(path: str) -> str:
    """Convert a saved image path (absolute or relative to project root) to a
    URL under /static.
    """
    if not path:
        return ''
    norm = os.path.normpath(path).replace('\\', '/')
    # relative path starting with static/
    if norm.startswith('static/'):
        return '/static/' + norm[len('static/'):]
    # relative path starting with epub_images/ (stored in newer JSON/DB)
    if norm.startswith('epub_images/'):
        return '/static/' + norm
    # absolute path that contains a static/epub_images component
    if '/static/epub_images/' in norm:
        rel = norm.split('/static/')[-1]
        return '/static/' + rel
    # fallback: just the basename
    return '/static/' + os.path.basename(norm)


_DECORATIVE_IMAGE_BASENAMES = {
    'vector.png', 'vector.jpg', 'line.png', 'line.jpg', 'line1.jpg', 'line1.png',
    'line2.jpg', 'line2.png', 'line3.jpg', 'line3.png',
    'decoration.png', 'decoration.jpg', 'decor.png', 'decor.jpg',
    'spacer.png', 'spacer.gif', 'blank.png', 'blank.gif',
}


def _is_decorative_image(img) -> bool:
    """Return True for images that are clearly decorative spacers/rules."""
    src = img.get('src') or ''
    if os.path.basename(src).lower() in _DECORATIVE_IMAGE_BASENAMES:
        return True
    width = img.get('width') or ''
    height = img.get('height') or ''
    try:
        w = int(width) if width else None
        h = int(height) if height else None
    except ValueError:
        w = h = None
    # Very small images are almost always rules/spacers.
    if (w is not None and w <= 5) or (h is not None and h <= 5):
        return True
    return False


def _find_nearest_image(ref_elem, doc_name: str, saved_images: Dict[str, str],
                        images_dir: str, direction: str = 'before',
                        limit: int = 3) -> str:
    """Find the nearest non-decorative image before or after a reference element.

    ``direction`` is 'before' (search backwards through the tree), 'after'
    (search forwards), or 'before_or_after' (prefer before, fall back to after).
    A small ``limit`` keeps the search local to the current recipe block and
    avoids grabbing a previous recipe's photo when the current recipe has none.
    """
    def _first(seq):
        for img in seq:
            if _is_decorative_image(img):
                continue
            src = img.get('src') or ''
            candidate = _resolve_image_path(src, doc_name, saved_images, images_dir)
            if candidate:
                return candidate
        return ''

    if direction in ('before', 'before_or_after'):
        found = _first(ref_elem.find_all_previous('img', limit=limit))
        if found:
            return found
    if direction in ('after', 'before_or_after'):
        found = _first(ref_elem.find_all_next('img', limit=limit))
        if found:
            return found
    return ''


def _map_images_to_titles(title_tags, images, direction: str = 'before',
                          doc_name: str = '', saved_images: Dict[str, str] = None,
                          images_dir: str = '') -> Dict:
    """Map non-decorative images to title elements by document order.

    For ``direction='before'`` the last image that appears before each title
    (and after the previous title) is assigned to that title.  For
    ``direction='after'`` the first image that appears after each title (and
    before the next title) is assigned.  This avoids assigning a neighbouring
    recipe's photo to the current recipe.

    Duplicate references to the same image file are collapsed to the first
    occurrence so one photo is not assigned to multiple recipes.
    """
    def _idx(elem):
        # Document-order index based on how many elements precede ``elem``.
        try:
            return len(elem.find_all_previous())
        except Exception:
            return 0

    # De-duplicate images by resolved path, keeping the first occurrence.
    seen_paths = set()
    unique_images = []
    for img in images:
        path = ''
        if saved_images:
            path = _resolve_image_path(img.get('src') or '', doc_name, saved_images, images_dir)
        else:
            path = os.path.basename(img.get('src') or '')
        if not path:
            continue
        if path in seen_paths:
            continue
        seen_paths.add(path)
        unique_images.append(img)

    title_indices = {t: _idx(t) for t in title_tags}
    image_indices = {img: _idx(img) for img in unique_images}
    sorted_titles = sorted(title_tags, key=lambda t: title_indices[t])
    sorted_images = sorted(unique_images, key=lambda img: image_indices[img])

    assignments: Dict = {}
    for i, title in enumerate(sorted_titles):
        curr_idx = title_indices[title]
        if direction == 'before':
            prev_idx = title_indices[sorted_titles[i - 1]] if i > 0 else -1
            candidates = [img for img in sorted_images
                          if prev_idx < image_indices[img] <= curr_idx]
            if candidates:
                assignments[title] = candidates[-1]
        else:  # after
            next_idx = title_indices[sorted_titles[i + 1]] if i + 1 < len(sorted_titles) else float('inf')
            candidates = [img for img in sorted_images
                          if curr_idx <= image_indices[img] < next_idx]
            if candidates:
                assignments[title] = candidates[0]
    return assignments


def _resolve_assigned_image(img, doc_name: str, saved_images: Dict[str, str], images_dir: str) -> str:
    """Resolve an assigned img tag to a saved image file path."""
    if img is None:
        return ''
    src = img.get('src') or ''
    return _resolve_image_path(src, doc_name, saved_images, images_dir)


def _assign_per_recipe_images(soup: BeautifulSoup, recipes: List[Dict], doc_name: str,
                              saved_images: Dict[str, str], images_dir: str) -> None:
    """Try to assign a distinct image to each recipe based on title elements.

    Finds title-like elements in the soup, maps the nearest non-decorative image
    to each title, then matches recipe titles to those elements.
    """
    if not recipes or not saved_images:
        return

    title_tags = []
    for tag in soup.find_all(['h1', 'h2', 'h3', 'h4']):
        text = _normalize_whitespace(tag.get_text(' ', strip=True))
        if _is_title_like(text):
            title_tags.append(tag)
    # Paragraph titles are common in EPUB cookbooks.
    for tag in soup.find_all('p'):
        text = _normalize_whitespace(tag.get_text(' ', strip=True))
        if _is_title_like(text):
            title_tags.append(tag)
    if not title_tags:
        return

    images = [img for img in soup.find_all('img') if not _is_decorative_image(img)]
    assignments = _map_images_to_titles(title_tags, images, direction='before_or_after',
                                        doc_name=doc_name, saved_images=saved_images,
                                        images_dir=images_dir)
    if not assignments:
        return

    # Map normalized title text -> resolved image path.
    title_to_image: Dict[str, str] = {}
    for tag, img in assignments.items():
        text = _normalize_whitespace(tag.get_text(' ', strip=True)).lower()
        path = _resolve_assigned_image(img, doc_name, saved_images, images_dir)
        if path and text:
            title_to_image[text] = path

    if not title_to_image:
        return

    for recipe in recipes:
        title = (recipe.get('title') or '').strip().lower()
        if not title:
            continue
        # Exact match first.
        if title in title_to_image:
            recipe['image'] = title_to_image[title]
            continue
        # Fuzzy prefix/suffix match.
        best = ''
        for t, path in title_to_image.items():
            if t.startswith(title) or title.startswith(t):
                if len(t) > len(best):
                    best = path
        if best:
            recipe['image'] = best


def _find_image_for_doc(items: List, doc_idx: int, soup: BeautifulSoup, doc_name: str,
                        saved_images: Dict[str, str], images_dir: str) -> str:
    """Find a single representative image associated with a document.

    Used as a fallback for generic extractors.  Book-specific extractors should
    prefer per-recipe image assignment via ``_find_nearest_image``.

    Search order:
    1. The first non-decorative image inside the document itself.
    2. Immediate short image-only neighbour pages (text <= 80 chars) in the
       EPUB reading order, preferring the page that precedes the document.
    """
    def _first_image(soup, name):
        for img in soup.find_all('img'):
            if _is_decorative_image(img):
                continue
            candidate = _resolve_image_path(img.get('src'), name, saved_images, images_dir)
            if candidate:
                return candidate
        return ''

    image_path = _first_image(soup, doc_name)
    if image_path:
        return image_path

    # Only rely on adjacent short (image-only) pages to avoid assigning a
    # chapter opener or unrelated cover to recipes in the current document.
    for offset in range(1, 3):
        for neighbor_idx in (doc_idx - offset, doc_idx + offset):
            if neighbor_idx < 0 or neighbor_idx >= len(items):
                continue
            item = items[neighbor_idx]
            try:
                if item.get_type() != ebooklib.ITEM_DOCUMENT:
                    continue
            except Exception:
                continue
            try:
                html = item.get_content().decode('utf-8', errors='ignore')
            except Exception:
                continue
            neighbor_soup = BeautifulSoup(html, 'lxml')
            neighbor_text = neighbor_soup.get_text(strip=True)
            # Treat short pages as image-only interleaving pages.
            if len(neighbor_text) > 80:
                continue
            image_path = _first_image(neighbor_soup, item.get_name())
            if image_path:
                return image_path
    return ''


def _extract_mobi_recipes(mobi_path: str) -> List[Dict]:
    """Extract recipes from an unencrypted MOBI/AZW ebook.

    Uses the ``mobi`` package to unpack the file to HTML (or occasionally a
    temporary EPUB) and then runs the normal EPUB extractors on the result.
    Images are currently not extracted from MOBI files.
    """
    try:
        import mobi
    except ImportError:
        return []

    tempdir = None
    try:
        tempdir, extracted_path = mobi.extract(mobi_path)
        if not extracted_path or not os.path.exists(extracted_path):
            return []

        # KF8 mobis sometimes unpack to a temporary EPUB; process it like any
        # other EPUB so image extraction works. Use a stable basename derived
        # from the original mobi so image directories are predictable.
        if extracted_path.lower().endswith('.epub'):
            target_path = os.path.join(tempdir, os.path.splitext(os.path.basename(mobi_path))[0] + '.epub')
            shutil.copy2(extracted_path, target_path)
            return extract_recipes_from_file(target_path)

        # Older Mobipocket files unpack to a single HTML file.
        if extracted_path.lower().endswith(('.html', '.htm')):
            with open(extracted_path, 'r', encoding='utf-8', errors='ignore') as f:
                html = f.read()
            soup = BeautifulSoup(html, 'lxml')
            return _extract_from_soup(soup, mobi_path, image_path='')
        return []
    except Exception:
        return []
    finally:
        if tempdir and os.path.isdir(tempdir):
            shutil.rmtree(tempdir, ignore_errors=True)


def extract_recipes_from_file(file_path: str):
    """Extract recipes from an EPUB, PDF or MOBI cookbook file."""
    if file_path.lower().endswith('.pdf'):
        return _extract_pdf_recipes(file_path)
    if file_path.lower().endswith('.mobi'):
        return _extract_mobi_recipes(file_path)

    recipes = []
    try:
        book = epub.read_epub(file_path)
    except Exception:
        return recipes

    file_basename = os.path.splitext(os.path.basename(file_path))[0]
    images_dir = os.path.join(os.path.dirname(__file__), 'static', 'epub_images', _images_dir_for_epub(file_path))
    # Only keep images that are actually referenced in the EPUB documents.
    referenced = _collect_referenced_images(book)
    saved_images = _save_epub_images(book, images_dir, referenced=referenced)

    items = list(book.get_items())
    doc_items = [(i, item) for i, item in enumerate(items) if item.get_type() == ebooklib.ITEM_DOCUMENT]

    # Nopalito recipes are split across many small HTML files; process the whole
    # book as a stream so a title on one page can pair with ingredients/steps on
    # the next page.
    if 'nopalito' in file_basename.lower():
        return _extract_nopalito_from_book(book, file_path, items, saved_images, images_dir)

    # Rose's Heavenly Cakes (Wiley) is likewise split into many small files,
    # with component pages (Batter, Topping, ...) belonging to the main recipe.
    if 'heavenly cakes' in file_basename.lower():
        return _extract_heavenly_cakes_from_book(book, file_path, items, saved_images, images_dir)

    for idx, item in doc_items:
        try:
            content = item.get_content()
        except Exception:
            continue
        try:
            html = content.decode('utf-8', errors='ignore')
        except Exception:
            html = str(content)
        soup = BeautifulSoup(html, 'lxml')
        doc_name = item.get_name()

        image_path = _find_image_for_doc(items, idx, soup, doc_name, saved_images, images_dir)
        recs = _extract_from_soup(soup, file_path, image_path=image_path,
                                    doc_name=doc_name, saved_images=saved_images,
                                    images_dir=images_dir)
        recipes.extend(recs)

    return recipes


# Backwards-compatible alias
extract_recipes_from_epub = extract_recipes_from_file


def _stable_id(recipe: dict) -> str:
    text = '::'.join([
        (recipe.get('title') or '').strip().lower(),
        (recipe.get('source') or '').strip().lower(),
        (recipe.get('ingredients') or '').strip().lower(),
        (recipe.get('steps') or '').strip().lower(),
    ])
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:24]


def insert_recipe(conn: sqlite3.Connection, recipe: dict):
    c = conn.cursor()
    stable_id = _stable_id(recipe)
    # include image/stable_id/serves columns if available
    try:
        c.execute('INSERT INTO recipes (title, ingredients, steps, source, file_path, image, stable_id, serves) VALUES (?,?,?,?,?,?,?,?)',
                  (recipe['title'], recipe['ingredients'], recipe['steps'], recipe['source'], recipe['file_path'], recipe.get('image', ''), stable_id, recipe.get('serves', '')))
    except sqlite3.IntegrityError:
        # duplicate recipe (same title/source/ingredients/steps); skip silently
        return
    except sqlite3.OperationalError:
        # fallback if schema doesn't include newer columns
        try:
            c.execute('INSERT INTO recipes (title, ingredients, steps, source, file_path, image, stable_id) VALUES (?,?,?,?,?,?,?)',
                      (recipe['title'], recipe['ingredients'], recipe['steps'], recipe['source'], recipe['file_path'], recipe.get('image', ''), stable_id))
        except sqlite3.OperationalError:
            c.execute('INSERT INTO recipes (title, ingredients, steps, source, file_path) VALUES (?,?,?,?,?)',
                      (recipe['title'], recipe['ingredients'], recipe['steps'], recipe['source'], recipe['file_path']))
    rowid = c.lastrowid
    try:
        c.execute('INSERT INTO recipes_fts(rowid, title, ingredients, steps) VALUES (?,?,?,?)',
                  (rowid, recipe['title'], recipe['ingredients'], recipe['steps']))
    except sqlite3.OperationalError:
        pass


def index_dir(books_dir: str, db_path: str):
    """Legacy one-shot indexer: extract from EPUBs/PDFs and insert directly into DB."""
    create_db(db_path)
    conn = sqlite3.connect(db_path)
    for path in _unique_books(books_dir):
        print('Indexing', path)
        recs = extract_recipes_from_file(path)
        for r in recs:
            insert_recipe(conn, r)
    conn.commit()
    conn.close()


# Backwards-compatible alias
index_epub_dir = index_dir


def _recipe_to_json(recipe: Dict) -> Dict:
    """Return a JSON-serialisable recipe record with a project-relative image path."""
    return {
        'title': recipe['title'],
        'ingredients': recipe['ingredients'],
        'steps': recipe['steps'],
        'source': recipe['source'],
        'file_path': recipe['file_path'],
        'image': _image_path_to_relative(recipe.get('image', '')),
        'serves': recipe.get('serves', ''),
    }


def _recipe_slug(recipe: Dict) -> str:
    """Stable slug for grouping recipes by source book."""
    base = os.path.splitext(os.path.basename(recipe['file_path']))[0]
    return re.sub(r'[^A-Za-z0-9]+', '_', base).strip('_')[:50]


def _unique_books(books_dir: str):
    """Return a list of unique book paths to process.

    Duplicate files (e.g. 'Book.pdf' and 'Book (1).pdf') are collapsed to the
    largest file. Hidden files and non-book files are ignored.
    """
    candidates = []
    for root, _, files in os.walk(books_dir):
        for fn in files:
            if not fn.lower().endswith(('.epub', '.pdf', '.mobi')):
                continue
            if fn.startswith('~') or fn.startswith('.'):
                continue
            path = os.path.join(root, fn)
            candidates.append(path)

    # group by normalized basename (strip trailing copy suffixes like (1), -1, etc.)
    groups: Dict[str, List[str]] = {}
    for path in candidates:
        base = os.path.basename(path)
        name, _ = os.path.splitext(base)
        norm = re.sub(r'\s*[\(\[](\d+|copy)\s*[\)\]]\s*$', '', name, flags=re.I)
        norm = re.sub(r'\s+-\d+\s*$', '', norm, flags=re.I)
        groups.setdefault(norm.lower(), []).append(path)

    selected = []
    for paths in groups.values():
        # keep the largest file when there are apparent duplicates
        paths = sorted(paths, key=lambda p: os.path.getsize(p), reverse=True)
        selected.append(paths[0])
    return sorted(selected)


def _slug_for_path(path: str) -> str:
    """Return the JSON slug for a given book file path."""
    base = os.path.splitext(os.path.basename(path))[0]
    return re.sub(r'[^A-Za-z0-9]+', '_', base).strip('_')[:50]


def _cleanup_orphan_image_dirs(base_dir: str, current_image_dirs: set):
    """Remove image directories under ``base_dir`` that no longer belong to an indexed book.

    Defensive: never delete everything. If ``current_image_dirs`` is empty, skip
    cleanup so a partial indexing run doesn't wipe other books' images.
    """
    if not os.path.isdir(base_dir):
        return
    if not current_image_dirs:
        print('Skipping orphan cleanup: no current image directories known')
        return
    for name in os.listdir(base_dir):
        path = os.path.join(base_dir, name)
        if not os.path.isdir(path):
            continue
        if name in current_image_dirs:
            continue
        print('Removing orphan image dir', path)
        shutil.rmtree(path, ignore_errors=True)


def preprocess_dir(books_dir: str, recipes_dir: str, force: bool = False,
                  progress_callback=None):
    """Extract recipes and images from EPUBs and PDFs and write one JSON file per book.

    Images are saved under static/epub_images/ for EPUBs. PDFs have no images
    extracted. The JSON files are written to ``recipes_dir`` and can be
    inspected, edited, or re-indexed without re-parsing the source files.

    By default this is incremental: a book is only re-parsed if its JSON does
    not exist or is older than the source file. Set ``force=True`` to re-parse
    every book. JSON files and image directories for books that have been
    removed are deleted.

    ``progress_callback`` receives dicts describing progress.
    """
    os.makedirs(recipes_dir, exist_ok=True)
    books: Dict[str, List[Dict]] = {}
    current_slugs = set()
    current_image_dirs = set()

    book_paths = _unique_books(books_dir)
    if progress_callback:
        progress_callback({'phase': 'preprocess', 'state': 'start', 'total': len(book_paths), 'current': 0, 'message': 'Preprocessing started'})

    for i, path in enumerate(book_paths, start=1):
        slug = _slug_for_path(path)
        current_slugs.add(slug)
        current_image_dirs.add(_images_dir_for_epub(path))
        out_path = os.path.join(recipes_dir, f"{slug}.json")

        if not force and os.path.exists(out_path):
            src_mtime = os.path.getmtime(path)
            json_mtime = os.path.getmtime(out_path)
            if json_mtime >= src_mtime:
                if progress_callback:
                    progress_callback({'phase': 'preprocess', 'state': 'book', 'current': i, 'total': len(book_paths), 'book': os.path.basename(path), 'skipped': True, 'message': f'Skipping up-to-date {path}'})
                continue

        if progress_callback:
            progress_callback({'phase': 'preprocess', 'state': 'book', 'current': i, 'total': len(book_paths), 'book': os.path.basename(path), 'skipped': False, 'message': f'Preprocessing {path}'})
        print('Preprocessing', path)
        recs = extract_recipes_from_file(path)
        for r in recs:
            books.setdefault(slug, []).append(_recipe_to_json(r))

        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(books.get(slug, []), f, ensure_ascii=False, indent=2)
        if books.get(slug):
            print(f'Wrote {len(books[slug])} recipes to {out_path}')
        else:
            print(f'Wrote empty {out_path}')
        if progress_callback:
            progress_callback({'phase': 'preprocess', 'state': 'book', 'current': i, 'total': len(book_paths), 'book': os.path.basename(path), 'skipped': False, 'done': True, 'count': len(books.get(slug, [])), 'message': f'Wrote {len(books.get(slug, []))} recipes to {out_path}'})

    # Remove JSON files for books that no longer exist.
    for fn in os.listdir(recipes_dir):
        if not fn.lower().endswith('.json'):
            continue
        slug = fn[:-5]
        if slug not in current_slugs:
            stale_path = os.path.join(recipes_dir, fn)
            print('Removing stale JSON', stale_path)
            os.remove(stale_path)

    # Remove image directories for books that no longer exist.
    image_base_dir = os.path.join(os.path.dirname(__file__), 'static', 'epub_images')
    _cleanup_orphan_image_dirs(image_base_dir, current_image_dirs)

    if progress_callback:
        progress_callback({'phase': 'preprocess', 'state': 'done', 'total': len(book_paths), 'current': len(book_paths), 'message': 'Preprocessing complete'})


# Backwards-compatible alias
preprocess_epub_dir = preprocess_dir


def index_preprocessed_dir(recipes_dir: str, db_path: str, force: bool = False,
                          progress_callback=None):
    """Load preprocessed JSON recipes into the SQLite database incrementally.

    Only books whose JSON file is new, changed, or missing from the DB are
    processed. Books that no longer have a JSON file are removed. Set
    ``force=True`` to re-load every book.
    """
    create_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        c = conn.cursor()

        # Collect current JSON files and the source they represent.
        json_files: Dict[str, str] = {}  # source -> path
        json_slugs: Dict[str, str] = {}  # source -> slug
        for root, _, files in os.walk(recipes_dir):
            for fn in files:
                if not fn.lower().endswith('.json'):
                    continue
                path = os.path.join(root, fn)
                slug = fn[:-5]
                with open(path, 'r', encoding='utf-8') as f:
                    try:
                        recs = json.load(f)
                    except json.JSONDecodeError:
                        continue
                source = recs[0]['source'] if recs else os.path.basename(path)
                json_files[source] = path
                json_slugs[source] = slug

        current_sources = set(json_files.keys())

        if progress_callback:
            progress_callback({'phase': 'index', 'state': 'start', 'total': len(current_sources), 'current': 0, 'message': 'DB indexing started'})

        # Remove DB entries for books that no longer exist.
        for (source,) in c.execute('SELECT source FROM book_index_log'):
            if source not in current_sources:
                print('Removing deleted book from DB:', source)
                try:
                    c.execute('DELETE FROM recipes_fts WHERE rowid IN (SELECT id FROM recipes WHERE source = ?)', (source,))
                except sqlite3.OperationalError:
                    pass
                c.execute('DELETE FROM recipes WHERE source = ?', (source,))
                c.execute('DELETE FROM book_index_log WHERE source = ?', (source,))
        conn.commit()

        # Index changed or new books.
        for i, source in enumerate(sorted(current_sources), start=1):
            path = json_files[source]
            slug = json_slugs[source]
            json_mtime = os.path.getmtime(path)

            if not force:
                row = c.execute('SELECT json_mtime FROM book_index_log WHERE source = ?', (source,)).fetchone()
                if row and abs(row[0] - json_mtime) < 0.001:
                    if progress_callback:
                        progress_callback({'phase': 'index', 'state': 'book', 'current': i, 'total': len(current_sources), 'book': os.path.basename(path), 'skipped': True, 'message': f'Skipping up-to-date {path}'})
                    continue

            if progress_callback:
                progress_callback({'phase': 'index', 'state': 'book', 'current': i, 'total': len(current_sources), 'book': os.path.basename(path), 'skipped': False, 'message': f'Indexing {path}'})
            print('Indexing', path)
            with open(path, 'r', encoding='utf-8') as f:
                recs = json.load(f)

            # Remove old recipes for this source first so the DB stays in sync.
            try:
                c.execute('DELETE FROM recipes_fts WHERE rowid IN (SELECT id FROM recipes WHERE source = ?)', (source,))
            except sqlite3.OperationalError:
                pass
            c.execute('DELETE FROM recipes WHERE source = ?', (source,))

            for r in recs:
                insert_recipe(conn, r)

            c.execute('''INSERT INTO book_index_log (source, slug, json_mtime, indexed_at)
                         VALUES (?, ?, ?, ?)
                         ON CONFLICT(source) DO UPDATE SET
                             slug=excluded.slug,
                             json_mtime=excluded.json_mtime,
                             indexed_at=excluded.indexed_at''',
                      (source, slug, json_mtime, time.time()))
            conn.commit()
            if progress_callback:
                progress_callback({'phase': 'index', 'state': 'book', 'current': i, 'total': len(current_sources), 'book': os.path.basename(path), 'skipped': False, 'done': True, 'count': len(recs), 'message': f'Indexed {len(recs)} recipes from {path}'})
    finally:
        conn.close()

    if progress_callback:
        progress_callback({'phase': 'index', 'state': 'done', 'total': len(current_sources), 'current': len(current_sources), 'message': 'DB indexing complete'})


def build_index(books_dir: str, recipes_dir: str, db_path: str, force: bool = False,
               progress_callback=None):
    """Preprocess books into JSON (incremental by default), then load JSON into DB.

    ``progress_callback`` receives small dicts: {'phase': 'preprocess'|'index',
    'state': 'start'|'done'|'book', 'current': int, 'total': int, 'book': str,
    'message': str}.
    """
    preprocess_dir(books_dir, recipes_dir, force=force, progress_callback=progress_callback)
    index_preprocessed_dir(recipes_dir, db_path, force=force, progress_callback=progress_callback)
