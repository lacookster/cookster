import json
import os
import sqlite3
import tempfile

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
    assert post_resp.json() == {'ok': True}

    get_resp = client.get('/api/user-data')
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body['data'] == data


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
