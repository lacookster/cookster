import pytest
from fastapi.testclient import TestClient
from api import app, _verify_password


PASSWORD = 'C))kstERn@p5t3r'


def test_verify_password():
    assert _verify_password(PASSWORD) is True
    assert _verify_password('wrong') is False


def test_login_page_is_public():
    client = TestClient(app)
    resp = client.get('/login')
    assert resp.status_code == 200
    assert 'password' in resp.text.lower()


def test_login_rejects_wrong_password():
    client = TestClient(app)
    resp = client.post('/login', data={'password': 'wrong'}, follow_redirects=False)
    assert resp.status_code == 302
    assert '/login?error=1' in resp.headers['location']
    assert 'cookster_session' not in resp.cookies


def test_login_accepts_correct_password():
    client = TestClient(app)
    resp = client.post('/login', data={'password': PASSWORD}, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers['location'] == '/'
    assert 'cookster_session' in client.cookies


def test_html_route_redirects_when_anonymous():
    client = TestClient(app)
    resp = client.get('/', follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers['location'] == '/login'


def test_api_route_returns_401_when_anonymous():
    client = TestClient(app)
    resp = client.get('/api/stats')
    assert resp.status_code == 401


def test_authenticated_html_route_is_accessible():
    client = TestClient(app)
    client.post('/login', data={'password': PASSWORD}, follow_redirects=False)
    resp = client.get('/')
    assert resp.status_code == 200


def test_logout_clears_session():
    client = TestClient(app)
    client.post('/login', data={'password': PASSWORD}, follow_redirects=False)
    assert 'cookster_session' in client.cookies
    resp = client.get('/logout', follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers['location'] == '/login'
    home = client.get('/', follow_redirects=False)
    assert home.status_code == 302
    assert home.headers['location'] == '/login'
