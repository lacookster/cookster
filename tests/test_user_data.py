import json
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


@pytest.fixture(autouse=True)
def temp_user_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    monkeypatch.setattr(api, '_USER_DB_PATH', path)
    yield path
    api._close_db_pools()
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def test_get_user_data_creates_cookie_and_empty_data(temp_user_db):
    client = TestClient(app)
    resp = client.get('/api/user-data')
    assert resp.status_code == 200
    body = resp.json()
    assert body['data'] == {}
    assert 'cookster_user' in client.cookies


def test_post_and_get_user_data(temp_user_db):
    client = TestClient(app)
    data = {
        'favorites': ['rec1', 'rec2'],
        'lists': [{'id': 'list1', 'name': 'Desserts', 'recipes': ['rec1']}],
        'shopping': {'items': []},
        'mealPlan': {},
        'notes': {'rec1': 'good'},
        'ratings': {'rec1': 5},
        'cooked': {},
        'updatedAt': 123456789
    }
    post_resp = client.post('/api/user-data', json={'data': data})
    assert post_resp.status_code == 200
    posted = post_resp.json()
    # The response carries the merged blob so the client can adopt it.
    assert posted['ok'] is True
    assert posted['data']['favorites'] == data['favorites']

    get_resp = client.get('/api/user-data')
    assert get_resp.status_code == 200
    got = get_resp.json()['data']
    for key, value in data.items():
        if key == 'updatedAt':
            # updatedAt is bumped to at least the client's timestamp.
            assert got['updatedAt'] >= data['updatedAt']
        else:
            assert got[key] == value


def test_user_data_is_isolated_by_token(temp_user_db):
    client_a = TestClient(app)
    client_b = TestClient(app)

    client_a.post('/api/user-data', json={'data': {'favorites': ['a']}})
    client_b.post('/api/user-data', json={'data': {'favorites': ['b']}})

    assert client_a.get('/api/user-data').json()['data']['favorites'] == ['a']
    assert client_b.get('/api/user-data').json()['data']['favorites'] == ['b']


def test_export_returns_token_and_data(temp_user_db):
    client = TestClient(app)
    client.post('/api/user-data', json={'data': {'notes': {'r1': 'delicious'}}})
    resp = client.get('/api/user-data/export')
    assert resp.status_code == 200
    body = resp.json()
    assert body['token']
    assert body['data']['notes'] == {'r1': 'delicious'}


def test_import_with_existing_token(temp_user_db):
    source = TestClient(app)
    source.post('/api/user-data', json={'data': {'favorites': ['shared']}})
    token = source.get('/api/user-data/export').json()['token']

    target = TestClient(app)
    resp = target.post('/api/user-data/import', json={'token': token})
    assert resp.status_code == 200
    assert resp.json()['data']['favorites'] == ['shared']

    # Subsequent requests from target should see the imported data.
    assert target.get('/api/user-data').json()['data']['favorites'] == ['shared']


def test_import_with_provided_data(temp_user_db):
    client = TestClient(app)
    token = client.get('/api/user-data/export').json()['token']
    resp = client.post('/api/user-data/import', json={
        'token': token,
        'data': {'favorites': ['forced']}
    })
    assert resp.status_code == 200
    assert resp.json()['data']['favorites'] == ['forced']


def test_reset_deletes_data_and_clears_cookie(temp_user_db):
    client = TestClient(app)
    client.post('/api/user-data', json={'data': {'favorites': ['gone']}})
    assert client.get('/api/user-data').json()['data'] != {}

    resp = client.post('/api/user-data/reset')
    assert resp.status_code == 200
    assert resp.json() == {'ok': True}

    # Cookie should be cleared.
    assert 'cookster_user' not in resp.cookies or not resp.cookies['cookster_user']

    # A fresh request with the same client gets empty data again.
    assert client.get('/api/user-data').json()['data'] == {}


def test_unauthenticated_user_data_is_rejected(temp_user_db, monkeypatch):
    monkeypatch.setattr(api, '_is_authenticated', lambda request: False)
    client = TestClient(app)
    resp = client.get('/api/user-data')
    assert resp.status_code == 401


def test_user_data_written_to_sqlite(temp_user_db):
    client = TestClient(app)
    client.post('/api/user-data', json={'data': {'ratings': {'r1': 4}}})

    conn = sqlite3.connect(temp_user_db)
    conn.row_factory = sqlite3.Row
    row = conn.execute('SELECT token, data FROM user_data').fetchone()
    assert row is not None
    assert json.loads(row['data'])['ratings'] == {'r1': 4}
    conn.close()


# Pairing codes ----------------------------------------------------------------


def _client_with_token(token):
    """A TestClient that presents the given user-data cookie (a linked device)."""
    client = TestClient(app)
    client.cookies.set('cookster_user', token)
    return client


def test_pairing_code_generate_and_claim(temp_user_db):
    client_a = TestClient(app)
    client_a.post('/api/user-data', json={'data': {'favorites': ['shared']}})

    resp = client_a.post('/api/pairing-code')
    assert resp.status_code == 200
    code = resp.json()['code']
    assert len(code) == 6
    assert all(ch in 'ABCDEFGHJKMNPQRSTUVWXYZ23456789' for ch in code)

    client_b = TestClient(app)
    claim = client_b.post('/api/pairing-code/claim', json={'code': code})
    assert claim.status_code == 200
    assert claim.json()['data']['favorites'] == ['shared']

    # The claiming device now syncs with the same token.
    assert client_b.get('/api/user-data').json()['data']['favorites'] == ['shared']


def test_pairing_code_is_single_use(temp_user_db):
    client_a = TestClient(app)
    code = client_a.post('/api/pairing-code').json()['code']

    client_b = TestClient(app)
    assert client_b.post('/api/pairing-code/claim', json={'code': code}).status_code == 200

    client_c = TestClient(app)
    assert client_c.post('/api/pairing-code/claim', json={'code': code}).status_code == 404


def test_pairing_code_invalid(temp_user_db):
    client = TestClient(app)
    resp = client.post('/api/pairing-code/claim', json={'code': 'NOPE99'})
    assert resp.status_code == 404


def test_pairing_code_expired(temp_user_db):
    client_a = TestClient(app)
    client_a.post('/api/user-data', json={'data': {'favorites': ['x']}})
    token = client_a.get('/api/user-data/export').json()['token']

    conn = sqlite3.connect(temp_user_db)
    conn.execute('INSERT INTO pairing_codes (code, token, expires_at, created_at) VALUES (?, ?, ?, ?)',
                 ('ABC234', token, time.time() - 10, time.time() - 1000))
    conn.commit()
    conn.close()

    client_b = TestClient(app)
    resp = client_b.post('/api/pairing-code/claim', json={'code': 'ABC234'})
    assert resp.status_code == 410


def test_pairing_code_regeneration_invalidates_previous(temp_user_db):
    client_a = TestClient(app)
    first = client_a.post('/api/pairing-code').json()['code']
    second = client_a.post('/api/pairing-code').json()['code']
    assert first != second

    client_b = TestClient(app)
    assert client_b.post('/api/pairing-code/claim', json={'code': first}).status_code == 404

    client_c = TestClient(app)
    assert client_c.post('/api/pairing-code/claim', json={'code': second}).status_code == 200


# Field-level merge ------------------------------------------------------------


def test_merge_unions_arrays_server_first(temp_user_db):
    client_a = TestClient(app)
    client_a.post('/api/user-data', json={'data': {'favorites': ['a1', 'a2'], 'pantry': ['salt'], 'updatedAt': 1000}})
    token = client_a.get('/api/user-data/export').json()['token']

    client_b = _client_with_token(token)
    resp = client_b.post('/api/user-data', json={'data': {'favorites': ['a2', 'b1'], 'pantry': ['pepper'], 'updatedAt': 2000}})
    merged = resp.json()['data']
    assert merged['favorites'] == ['a1', 'a2', 'b1']
    assert merged['pantry'] == ['salt', 'pepper']
    assert merged['updatedAt'] >= 2000


def test_merge_lists_by_id(temp_user_db):
    client_a = TestClient(app)
    client_a.post('/api/user-data', json={'data': {
        'lists': [{'id': 'l1', 'name': 'Short', 'recipes': ['r1']}],
    }})
    token = client_a.get('/api/user-data/export').json()['token']

    client_b = _client_with_token(token)
    resp = client_b.post('/api/user-data', json={'data': {
        'lists': [
            {'id': 'l1', 'name': 'A much longer name', 'recipes': ['r2']},
            {'id': 'l2', 'name': 'New list', 'recipes': ['r3']},
        ],
    }})
    merged = resp.json()['data']['lists']
    assert merged == [
        {'id': 'l1', 'name': 'A much longer name', 'recipes': ['r1', 'r2']},
        {'id': 'l2', 'name': 'New list', 'recipes': ['r3']},
    ]


def test_merge_saved_searches_by_id(temp_user_db):
    client_a = TestClient(app)
    client_a.post('/api/user-data', json={'data': {
        'savedSearches': [{'id': 's1', 'label': 'Chicken', 'q': 'chicken'}],
    }})
    token = client_a.get('/api/user-data/export').json()['token']

    client_b = _client_with_token(token)
    resp = client_b.post('/api/user-data', json={'data': {
        'savedSearches': [
            {'id': 's1', 'label': 'Chicken', 'q': 'chicken'},
            {'id': 's2', 'label': 'Pasta', 'q': 'pasta'},
        ],
    }})
    merged = resp.json()['data']['savedSearches']
    assert [s['id'] for s in merged] == ['s1', 's2']


def test_merge_shopping_checked_if_either(temp_user_db):
    client_a = TestClient(app)
    client_a.post('/api/user-data', json={'data': {
        'shopping': {'items': [{'id': 'i1', 'text': 'milk', 'checked': True}]},
    }})
    token = client_a.get('/api/user-data/export').json()['token']

    client_b = _client_with_token(token)
    resp = client_b.post('/api/user-data', json={'data': {
        'shopping': {'items': [
            {'id': 'i1', 'text': 'milk', 'checked': False},
            {'id': 'i2', 'text': 'eggs', 'checked': False},
        ]},
    }})
    items = resp.json()['data']['shopping']['items']
    assert [(i['id'], i['checked']) for i in items] == [('i1', True), ('i2', False)]


def test_merge_meal_plan_by_date(temp_user_db):
    client_a = TestClient(app)
    client_a.post('/api/user-data', json={'data': {'mealPlan': {'2026-01-01': ['r1']}}})
    token = client_a.get('/api/user-data/export').json()['token']

    client_b = _client_with_token(token)
    resp = client_b.post('/api/user-data', json={'data': {
        'mealPlan': {'2026-01-01': ['r1', 'r2'], '2026-01-02': ['r3']},
    }})
    assert resp.json()['data']['mealPlan'] == {'2026-01-01': ['r1', 'r2'], '2026-01-02': ['r3']}


def test_merge_notes_newer_client_wins(temp_user_db):
    client_a = TestClient(app)
    client_a.post('/api/user-data', json={'data': {'notes': {'r1': 'old'}}})
    token = client_a.get('/api/user-data/export').json()['token']

    client_b = _client_with_token(token)
    future_ms = int(time.time() * 1000) + 60000
    resp = client_b.post('/api/user-data', json={'data': {'notes': {'r1': 'new'}, 'updatedAt': future_ms}})
    assert resp.json()['data']['notes'] == {'r1': 'new'}


def test_merge_notes_stale_client_keeps_server(temp_user_db):
    client_a = TestClient(app)
    client_a.post('/api/user-data', json={'data': {'notes': {'r1': 'fresh'}}})
    token = client_a.get('/api/user-data/export').json()['token']

    client_b = _client_with_token(token)
    # updatedAt=1000ms is far older than the server's stored timestamp.
    resp = client_b.post('/api/user-data', json={'data': {'notes': {'r1': 'stale'}, 'updatedAt': 1000}})
    assert resp.json()['data']['notes'] == {'r1': 'fresh'}


def test_merge_adds_new_keys_from_either_side(temp_user_db):
    client_a = TestClient(app)
    client_a.post('/api/user-data', json={'data': {'notes': {'r1': 'from-a'}, 'ratings': {'r1': 4}}})
    token = client_a.get('/api/user-data/export').json()['token']

    client_b = _client_with_token(token)
    resp = client_b.post('/api/user-data', json={'data': {'notes': {'r2': 'from-b'}, 'cooked': {'r3': '2026-01-01'}}})
    merged = resp.json()['data']
    assert merged['notes'] == {'r1': 'from-a', 'r2': 'from-b'}
    assert merged['ratings'] == {'r1': 4}
    assert merged['cooked'] == {'r3': '2026-01-01'}


# Device tracking and revocation ------------------------------------------------


def test_devices_tracked_and_revoked(temp_user_db):
    client_a = TestClient(app)
    ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0'
    client_a.post('/api/user-data', json={'data': {'favorites': ['x']}}, headers={'user-agent': ua})
    token_a = client_a.get('/api/user-data/export').json()['token']

    client_b = TestClient(app)
    client_b.post('/api/user-data', json={'data': {'favorites': ['y']}})

    resp = client_b.get('/api/devices')
    assert resp.status_code == 200
    devices = resp.json()['devices']
    assert len(devices) == 2
    assert sum(1 for d in devices if d['current']) == 1
    dev_a = next(d for d in devices if d['token'] == token_a)
    assert dev_a['name'] == 'Chrome on Windows'
    assert dev_a['last_seen'] > 0
    assert dev_a['revoked'] is False

    # Revoke device A.
    resp = client_b.post('/api/devices/revoke', json={'token': token_a})
    assert resp.status_code == 200
    assert resp.json() == {'ok': True}

    # A's data is gone and A can no longer sync.
    assert client_a.get('/api/user-data').status_code == 403
    assert client_a.post('/api/user-data', json={'data': {'favorites': ['z']}}).status_code == 403

    # The revoked calls must not recreate the data row.
    conn = sqlite3.connect(temp_user_db)
    count = conn.execute('SELECT COUNT(*) FROM user_data WHERE token = ?', (token_a,)).fetchone()[0]
    revoked = conn.execute('SELECT revoked FROM devices WHERE token = ?', (token_a,)).fetchone()[0]
    conn.close()
    assert count == 0
    assert revoked == 1

    # Device B is unaffected.
    assert client_b.get('/api/user-data').json()['data']['favorites'] == ['y']


def test_revoke_requires_token(temp_user_db):
    client = TestClient(app)
    resp = client.post('/api/devices/revoke', json={})
    assert resp.status_code == 400
