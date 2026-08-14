import pytest
from fastapi.testclient import TestClient
import api
from api import app


@pytest.fixture(autouse=True)
def bypass_auth(monkeypatch):
    monkeypatch.setattr(api, '_is_authenticated', lambda request: True)


def test_index_contains_static_links():
    client = TestClient(app)
    r = client.get('/')
    assert r.status_code == 200
    text = r.text
    assert '/static/style.css' in text
    assert '/static/app.js' in text


def test_static_files_served():
    client = TestClient(app)
    r1 = client.get('/static/style.css')
    assert r1.status_code == 200
    assert 'text/css' in r1.headers.get('content-type', '')
    r2 = client.get('/static/app.js')
    assert r2.status_code == 200
    assert 'application/javascript' in r2.headers.get('content-type', '')
