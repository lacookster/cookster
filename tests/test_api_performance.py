import os
import sqlite3
import tempfile
import time

import pytest
from fastapi.testclient import TestClient

import api
from api import app


@pytest.fixture(autouse=True)
def bypass_auth(monkeypatch):
    monkeypatch.setattr(api, '_is_authenticated', lambda request: True)


def _set_db_dir(path):
    api.DB_DIR = os.path.dirname(path) if path else os.path.dirname(os.path.abspath(__file__))


def _restore_db_dir():
    api.DB_DIR = os.path.dirname(os.path.abspath(__file__))
    api._close_db_pools()


def _make_big_db(path, count=500):
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute('''CREATE TABLE recipes (
        id INTEGER PRIMARY KEY, title TEXT, ingredients TEXT, steps TEXT,
        source TEXT, file_path TEXT, image TEXT, stable_id TEXT, serves TEXT
    )''')
    c.execute('CREATE UNIQUE INDEX idx_recipes_stable_id ON recipes(stable_id)')
    try:
        c.execute("CREATE VIRTUAL TABLE recipes_fts USING fts5(title, ingredients, steps, content='recipes', content_rowid='id')")
    except sqlite3.OperationalError:
        pass
    base_ingredients = 'chicken, salt, pepper, olive oil, garlic, lemon'
    base_steps = '1. Prep. 2. Cook. 3. Serve.'
    rows = []
    for i in range(count):
        title = f'Recipe {i} {["Chicken","Beef","Vegetable","Pasta","Salad"][i % 5]} Bowl'
        source = f'book{i % 10}'
        stable_id = f'stable_{i}'
        rows.append((title, base_ingredients, base_steps, source, '', stable_id))
    c.executemany('INSERT INTO recipes (title, ingredients, steps, source, file_path, stable_id) VALUES (?,?,?,?,?,?)', rows)
    c.executemany('INSERT INTO recipes_fts(rowid, title, ingredients, steps) VALUES ((SELECT id FROM recipes WHERE stable_id = ?), ?, ?, ?)',
                  [(s, t, ing, st) for (t, ing, st, _, _, s) in rows])
    conn.commit()
    conn.close()


def test_search_on_large_db_is_fast():
    fd, dbpath = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    _set_db_dir(dbpath)
    _make_big_db(dbpath, count=500)
    client = TestClient(app)

    start = time.time()
    resp = client.get('/search', params={'q': 'chicken', 'db': os.path.basename(dbpath), 'limit': 10})
    elapsed = time.time() - start
    assert resp.status_code == 200
    j = resp.json()
    assert j['total'] > 0
    assert elapsed < 3.0, f'search took {elapsed:.2f}s'

    _restore_db_dir()
    os.remove(dbpath)


def test_related_on_large_db_is_fast():
    fd, dbpath = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    _set_db_dir(dbpath)
    _make_big_db(dbpath, count=500)
    client = TestClient(app)

    stable_id = 'stable_0'
    start = time.time()
    resp = client.get(f'/api/related/{stable_id}', params={'db': os.path.basename(dbpath)})
    elapsed = time.time() - start
    assert resp.status_code == 200
    j = resp.json()
    assert 'same_book' in j and 'similar' in j
    assert elapsed < 3.0, f'related took {elapsed:.2f}s'

    _restore_db_dir()
    os.remove(dbpath)
