import sqlite3
import tempfile
import os
import pytest
from fastapi.testclient import TestClient
import api
from api import app, _clean_source


@pytest.fixture(autouse=True)
def bypass_auth(monkeypatch):
    """Existing endpoint tests run as an authenticated user."""
    monkeypatch.setattr(api, '_is_authenticated', lambda request: True)


def _set_db_dir(path):
    api.DB_DIR = os.path.dirname(path) if path else os.path.dirname(os.path.abspath(__file__))


def _restore_db_dir():
    api.DB_DIR = os.path.dirname(os.path.abspath(__file__))


def make_db(path, epubs_dir=None):
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute('CREATE TABLE recipes (id INTEGER PRIMARY KEY, title TEXT, ingredients TEXT, steps TEXT, source TEXT, file_path TEXT)')
    samples = [
        ('Grilled Chicken','chicken, salt, pepper','grill the chicken','book1',''),
        ('Apple Pie','apples, sugar, flour','bake the pie','book2',''),
        ('Chicken Soup','chicken, water, carrots','boil the chicken','book3',''),
    ]
    for t, ing, steps, src, fp in samples:
        c.execute('INSERT INTO recipes (title, ingredients, steps, source, file_path) VALUES (?,?,?,?,?)',(t,ing,steps,src,fp))
    conn.commit()
    conn.close()


def test_clean_source():
    assert _clean_source("Gordon Ramsay's Ultimate Cookery Course -- Ramsay, Gordon -- London, 2012 -- Anna’s Archive.epub") == "Gordon Ramsay's Ultimate Cookery Course"
    assert _clean_source("Afro-Vegan_ Farm-Fresh African ( PDFDrive.com ).pdf") == "Afro-Vegan_ Farm-Fresh African"
    assert _clean_source("Book.pdf") == "Book"


def test_search_endpoint():
    fd, dbpath = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    _set_db_dir(dbpath)
    make_db(dbpath)
    client = TestClient(app)
    resp = client.get('/search', params={'q':'chicken', 'db': os.path.basename(dbpath), 'limit': 2})
    assert resp.status_code == 200
    j = resp.json()
    assert 'results' in j
    assert len(j['results']) >= 1
    for r in j['results']:
        assert 'stable_id' in r
    _restore_db_dir()
    os.remove(dbpath)


def test_search_pagination():
    fd, dbpath = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    _set_db_dir(dbpath)
    make_db(dbpath)
    conn = sqlite3.connect(dbpath)
    c = conn.cursor()
    for i in range(25):
        c.execute('INSERT INTO recipes (title, ingredients, steps, source, file_path) VALUES (?,?,?,?,?)',
                  (f'Item {i}', 'chicken, salt', 'cook', 'src', ''))
    conn.commit()
    conn.close()

    client = TestClient(app)
    r1 = client.get('/search', params={'q': 'chicken', 'db': os.path.basename(dbpath), 'limit': 10, 'page': 1})
    assert r1.status_code == 200
    j1 = r1.json()
    assert j1['total'] > 10
    assert len(j1['results']) == 10

    r2 = client.get('/search', params={'q': 'chicken', 'db': os.path.basename(dbpath), 'limit': 10, 'page': 2})
    assert r2.status_code == 200
    j2 = r2.json()
    assert len(j2['results']) > 0
    ids1 = {r['id'] for r in j1['results']}
    ids2 = {r['id'] for r in j2['results']}
    assert not ids1 & ids2
    _restore_db_dir()
    os.remove(dbpath)


def test_batch_recipes_endpoint():
    fd, dbpath = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    _set_db_dir(dbpath)
    make_db(dbpath)
    client = TestClient(app)

    resp = client.get('/api/recipes', params={'ids': '1,2,99', 'db': os.path.basename(dbpath)})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    ids = {r['id'] for r in data}
    assert ids == {1, 2}
    for r in data:
        assert 'title' in r
        assert 'source' in r
        assert 'source_raw' in r
        assert 'image_url' in r
        assert 'stable_id' in r
        assert 'ingredients' in r

    # Empty/malformed ids should return empty list
    resp2 = client.get('/api/recipes', params={'ids': '', 'db': os.path.basename(dbpath)})
    assert resp2.status_code == 200
    assert resp2.json() == []
    _restore_db_dir()
    os.remove(dbpath)


def test_recipe_and_download():
    fd, dbpath = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    _set_db_dir(dbpath)
    conn = sqlite3.connect(dbpath)
    c = conn.cursor()
    c.execute('CREATE TABLE recipes (id INTEGER PRIMARY KEY, title TEXT, ingredients TEXT, steps TEXT, source TEXT, file_path TEXT)')
    c.execute('INSERT INTO recipes (title, ingredients, steps, source, file_path) VALUES (?,?,?,?,?)', ('T','i1\ni2','s1\ns2','src', 'books/file.epub'))
    conn.commit()
    conn.close()

    client = TestClient(app)
    # recipe view
    resp = client.get('/recipe/1', params={'db': os.path.basename(dbpath)})
    assert resp.status_code == 200
    assert 'T' in resp.text
    # download recipe as markdown
    resp2 = client.get('/download/1', params={'db': os.path.basename(dbpath)})
    assert resp2.status_code == 200
    assert 'text/markdown' in resp2.headers.get('content-type', '')
    body = resp2.text
    assert '# T' in body
    assert 'From: src' in body
    assert '- i1' in body
    assert '1. s1' in body
    _restore_db_dir()
    os.remove(dbpath)


def test_db_path_sandboxing():
    client = TestClient(app)
    # Absolute path outside DB_DIR should be rejected
    resp = client.get('/search', params={'q': 'chicken', 'db': '/etc/passwd'})
    assert resp.status_code == 400
    # Path traversal should be rejected
    resp2 = client.get('/search', params={'q': 'chicken', 'db': '../secret.db'})
    assert resp2.status_code == 400


def test_empty_search_results():
    fd, dbpath = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    _set_db_dir(dbpath)
    make_db(dbpath)
    client = TestClient(app)
    resp = client.get('/search', params={'q': 'xyznotfound', 'db': os.path.basename(dbpath)})
    assert resp.status_code == 200
    j = resp.json()
    assert j['total'] == 0
    assert j['results'] == []
    _restore_db_dir()
    os.remove(dbpath)


def test_negative_search_filter():
    fd, dbpath = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    _set_db_dir(dbpath)
    make_db(dbpath)
    client = TestClient(app)
    resp = client.get('/search', params={'q': 'chicken -soup', 'db': os.path.basename(dbpath)})
    assert resp.status_code == 200
    j = resp.json()
    assert 'soup' not in ' '.join(r['title'].lower() for r in j['results'])
    _restore_db_dir()
    os.remove(dbpath)


def test_recipes_by_source_endpoint():
    fd, dbpath = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    _set_db_dir(dbpath)
    make_db(dbpath)
    client = TestClient(app)
    resp = client.get('/api/recipes-by-source', params={'source': 'book1', 'db': os.path.basename(dbpath)})
    assert resp.status_code == 200
    j = resp.json()
    assert j['total'] >= 1
    assert all(r['source_raw'] == 'book1' for r in j['results'])
    assert 'source' in j
    _restore_db_dir()
    os.remove(dbpath)


def test_source_filter():
    fd, dbpath = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    _set_db_dir(dbpath)
    make_db(dbpath)
    client = TestClient(app)
    resp = client.get('/search', params={'q': 'chicken', 'db': os.path.basename(dbpath), 'source': 'book1'})
    assert resp.status_code == 200
    j = resp.json()
    assert len(j['results']) >= 1
    for r in j['results']:
        assert r['source'] == 'book1'

    resp2 = client.get('/api/sources', params={'db': os.path.basename(dbpath)})
    assert resp2.status_code == 200
    sources = resp2.json()['sources']
    assert {s['raw'] for s in sources} == {'book1', 'book2', 'book3'}
    assert {s['clean'] for s in sources} == {'book1', 'book2', 'book3'}
    _restore_db_dir()
    os.remove(dbpath)


def test_stable_id_lookup():
    fd, dbpath = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    _set_db_dir(dbpath)
    make_db(dbpath)
    client = TestClient(app)

    # Fetch a recipe by integer id, then look it up by stable_id.
    resp = client.get('/api/recipes', params={'ids': '1', 'db': os.path.basename(dbpath)})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    stable_id = data[0]['stable_id']

    resp2 = client.get(f'/recipe/{stable_id}', params={'db': os.path.basename(dbpath)})
    assert resp2.status_code == 200
    assert 'Grilled Chicken' in resp2.text
    _restore_db_dir()
    os.remove(dbpath)


def test_download_path_sandboxing():
    fd, dbpath = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    _set_db_dir(dbpath)
    # create a file outside the allowed books dir
    fd2, outside = tempfile.mkstemp(suffix='.epub')
    os.close(fd2)
    conn = sqlite3.connect(dbpath)
    c = conn.cursor()
    c.execute('CREATE TABLE recipes (id INTEGER PRIMARY KEY, title TEXT, ingredients TEXT, steps TEXT, source TEXT, file_path TEXT)')
    c.execute('INSERT INTO recipes (title, ingredients, steps, source, file_path) VALUES (?,?,?,?,?)', ('T','i','s','src', outside))
    conn.commit()
    conn.close()

    client = TestClient(app)
    # download endpoint should not serve the source file, only a Markdown export of the recipe
    resp = client.get('/download/1', params={'db': os.path.basename(dbpath)})
    assert resp.status_code == 200
    assert 'text/markdown' in resp.headers.get('content-type', '')
    assert '# T' in resp.text
    _restore_db_dir()
    os.remove(dbpath)
    os.remove(outside)


def test_suggest_endpoint():
    fd, dbpath = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    _set_db_dir(dbpath)
    make_db(dbpath)
    client = TestClient(app)
    resp = client.get('/api/suggest', params={'q': 'chick', 'db': os.path.basename(dbpath)})
    assert resp.status_code == 200
    j = resp.json()
    assert 'suggestions' in j
    assert any('chicken' in s.lower() for s in j['suggestions'])
    _restore_db_dir()
    os.remove(dbpath)


def test_random_recipe_endpoint():
    fd, dbpath = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    _set_db_dir(dbpath)
    make_db(dbpath)
    client = TestClient(app)
    resp = client.get('/api/random', params={'db': os.path.basename(dbpath)})
    assert resp.status_code == 200
    j = resp.json()
    assert 'stable_id' in j
    assert 'title' in j
    _restore_db_dir()
    os.remove(dbpath)


def test_related_recipes_endpoint():
    fd, dbpath = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    _set_db_dir(dbpath)
    make_db(dbpath)
    client = TestClient(app)

    # Fetch a recipe stable_id
    resp = client.get('/api/recipes', params={'ids': '1', 'db': os.path.basename(dbpath)})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    stable_id = data[0]['stable_id']

    resp2 = client.get(f'/api/related/{stable_id}', params={'db': os.path.basename(dbpath)})
    assert resp2.status_code == 200
    j = resp2.json()
    assert 'same_book' in j
    assert 'similar' in j
    _restore_db_dir()
    os.remove(dbpath)


def test_stats_endpoint():
    fd, dbpath = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    _set_db_dir(dbpath)
    make_db(dbpath)
    client = TestClient(app)
    resp = client.get('/api/stats', params={'db': os.path.basename(dbpath)})
    assert resp.status_code == 200
    j = resp.json()
    assert j['total_recipes'] == 3
    assert j['total_books'] == 3
    _restore_db_dir()
    os.remove(dbpath)


def test_index_status_endpoint_idle():
    client = TestClient(app)
    resp = client.get('/api/index/status')
    assert resp.status_code == 200
    j = resp.json()
    assert j['state'] == 'idle'
    assert j['running'] is False


def test_index_start_endpoint():
    old_build_index = api.build_index
    calls = []

    def fake_build_index(*args, **kwargs):
        calls.append((args, kwargs))

    api.build_index = fake_build_index
    try:
        client = TestClient(app)
        resp = client.post('/api/index/start')
        assert resp.status_code == 200
        j = resp.json()
        assert j['state'] == 'running'
        assert j['running'] is True

        # Wait for the background thread to finish the fake indexer.
        for _ in range(50):
            resp2 = client.get('/api/index/status')
            if resp2.json()['state'] == 'complete':
                break
            import time
            time.sleep(0.05)

        status = client.get('/api/index/status').json()
        assert status['state'] == 'complete'
        assert status['running'] is False
        assert len(calls) == 1
    finally:
        api.build_index = old_build_index
