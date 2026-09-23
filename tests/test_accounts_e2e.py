"""Exercise account lifecycle through the complete app with disposable SQLite data."""
from http.cookies import SimpleCookie

import pytest
from fastapi.testclient import TestClient

from app.platform import AUTH_COOKIE, SESSION_SECONDS
from app.router import DemoRouter


PASSWORD = "  correct horse battery  "
EMAIL = "manager@example.org"


@pytest.fixture
def app_factory(tmp_path, monkeypatch):
    # main.py constructs its exported ASGI app on import. Keep that side effect,
    # and all test applications, away from the delivered application's database.
    monkeypatch.setenv("EKT_DATABASE_PATH", ":memory:")
    monkeypatch.setenv("EKT_SEED_DEMO", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("PUBLIC_ORIGIN", "")
    from app.main import create_app

    opened = []

    def make(path=None):
        app = create_app(db_path=path or tmp_path / "accounts.sqlite3", router=DemoRouter())
        opened.append(app.state.platform)
        return app

    yield make
    for store in opened:
        store.close()


def register(client, **changes):
    return client.post("/api/auth/register", json={
        "name": "  Тестовый менеджер  ",
        "email": "  MANAGER@Example.org  ",
        "password": PASSWORD,
        "company": "Тестовая компания",
        **changes,
    })


def login(client, **changes):
    return client.post("/api/auth/login", json={
        "email": "  MANAGER@Example.org  ", "password": PASSWORD, **changes,
    })


def add_cart_item(client):
    proposed = client.post("/api/cart/propose", json={
        "sku": "DEMO-LED12", "quantity": 2, "locale": "ru",
    })
    assert proposed.status_code == 200, proposed.text
    pending = proposed.json()["pending"]
    assert pending is not None
    confirmed = client.post("/api/cart/confirm", json={
        "pending_id": pending["id"], "locale": "ru",
    })
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["cart"][0]["quantity"] == 2
    return confirmed.json()["cart"]


def test_registration_normalization_duplicate_and_exact_password(app_factory):
    app = app_factory()
    with TestClient(app) as client:
        response = register(client)
        assert response.status_code == 201, response.text
        user = response.json()["user"]
        assert user["name"] == "Тестовый менеджер"
        assert user["email"] == EMAIL
        assert set(user) == {"id", "name", "email", "role"}
        assert client.get("/api/auth/me").json()["user"] == user

        duplicate = register(client, email=" Manager@EXAMPLE.ORG ")
        assert duplicate.status_code == 409
        assert client.get("/api/auth/me").json()["user"] == user
        assert client.post("/api/auth/logout").status_code == 200
        for wrong in ("wrong password", PASSWORD.strip()):
            response = login(client, password=wrong)
            assert response.status_code == 401
            assert client.get("/api/auth/me").json()["user"] is None
        unknown = login(client, email="unknown@example.org")
        assert unknown.status_code == 401
        assert unknown.json()["detail"] == response.json()["detail"]
        assert login(client).json()["user"] == user

        row = app.state.platform.db.execute("SELECT password_hash FROM users").fetchone()
        assert row[0].startswith("pbkdf2_sha256$")
        assert PASSWORD not in row[0]


@pytest.mark.parametrize("password,status", [
    ("a" * 7, 422), ("a" * 8, 201), ("a" * 128, 201), ("a" * 129, 422),
])
def test_password_length_boundaries(app_factory, password, status):
    with TestClient(app_factory()) as client:
        response = register(client, password=password)
        assert response.status_code == status, response.text
        if status == 201:
            assert client.post("/api/auth/logout").status_code == 200
            assert login(client, password=password).status_code == 200
        else:
            assert client.get("/api/auth/me").json()["user"] is None


def test_login_session_survives_new_client_and_database_reopen(app_factory):
    app = app_factory()
    with TestClient(app) as client:
        user = register(client).json()["user"]
        token = client.cookies.get(AUTH_COOKIE)
        assert client.get("/api/bootstrap").status_code == 200

    # A new app has a fresh in-memory chat registry; account sessions remain in SQLite.
    restarted = app_factory()
    with TestClient(restarted) as client:
        client.cookies.set(AUTH_COOKIE, token, domain="testserver.local", path="/")
        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["user"] == user
        assert me.headers["cache-control"] == "no-store"
        assert client.post("/api/auth/logout").status_code == 200
        assert client.get("/api/auth/me").json()["user"] is None

    # Logout must revoke the token at the server, including for an old browser copy.
    with TestClient(app) as stale:
        stale.cookies.set(AUTH_COOKIE, token, domain="testserver.local", path="/")
        assert stale.get("/api/auth/me").json()["user"] is None
        assert stale.get("/api/offers?mine=true").status_code == 401
        assert login(stale).json()["user"] == user
        assert stale.cookies.get(AUTH_COOKIE) != token


def test_account_cart_and_own_offers_persist_across_logout_and_restart(app_factory):
    app = app_factory()
    with TestClient(app) as client:
        user = register(client).json()["user"]
        cart = add_cart_item(client)
        created = client.post("/api/offers", json={
            "title": "Проверка аккаунта — тестовое предложение", "category": "cable",
            "quantity": 20, "original_price": 1000, "price": 700,
            "lat": 43.24, "lng": 76.90, "address": "Алматы, тестовый склад",
        })
        assert created.status_code == 201, created.text
        offer_id = created.json()["item"]["id"]
        assert client.get("/api/bootstrap").json()["cart"] == cart
        assert client.post("/api/auth/logout").status_code == 200
        assert client.get("/api/cart").json()["items"] == []
        assert client.get("/api/offers?mine=true").status_code == 401
        assert login(client).json()["user"] == user
        assert client.get("/api/cart").json()["items"] == cart

    restarted = app_factory()
    with TestClient(restarted) as client, TestClient(restarted) as guest:
        assert login(client).json()["user"] == user
        assert client.get("/api/bootstrap").json()["cart"] == cart
        own = client.get("/api/offers?mine=true").json()["items"]
        assert [item["id"] for item in own] == [offer_id]
        assert own[0]["owner_id"] == user["id"]
        assert guest.get("/api/auth/me").json()["user"] is None
        assert guest.get("/api/cart").json()["items"] == []
        assert guest.delete(f"/api/offers/{offer_id}").status_code == 401
        other = register(guest, email="other@example.org").json()["user"]
        assert other["id"] != user["id"]
        assert guest.get("/api/cart").json()["items"] == []
        assert guest.get("/api/offers?mine=true").json()["items"] == []
        assert guest.delete(f"/api/offers/{offer_id}").status_code == 404
        assert client.get("/api/offers?mine=true").json()["total"] == 1


def test_login_rotation_and_expired_session(app_factory):
    app = app_factory()
    with TestClient(app) as client:
        register(client)
        old_token = client.cookies.get(AUTH_COOKIE)
        assert login(client).status_code == 200
        assert client.cookies.get(AUTH_COOKIE) != old_token
        assert app.state.platform.user_for_token(old_token) is None
        with app.state.platform.db:
            app.state.platform.db.execute("UPDATE auth_sessions SET expires_at=0")
        assert client.get("/api/auth/me").json()["user"] is None
        assert client.get("/api/offers?mine=true").status_code == 401
        assert login(client).status_code == 200


@pytest.mark.parametrize("base_url,secure", [("http://testserver", False), ("https://testserver", True)])
def test_auth_cookie_attributes_and_origin_guard(app_factory, base_url, secure):
    with TestClient(app_factory(), base_url=base_url) as client:
        response = register(client)
        assert response.status_code == 201
        cookies = SimpleCookie()
        for header in response.headers.get_list("set-cookie"):
            cookies.load(header)
        cookie = cookies[AUTH_COOKIE]
        assert cookie["httponly"]
        assert cookie["samesite"] == "strict"
        assert cookie["path"] == "/"
        assert int(cookie["max-age"]) == SESSION_SECONDS
        assert bool(cookie["secure"]) is secure
        assert client.get("/api/auth/me").json()["user"] is not None
        assert client.post("/api/auth/logout", headers={"Origin": "https://other.example"}).status_code == 403
        assert client.get("/api/auth/me").json()["user"] is not None
        assert client.post("/api/auth/logout", headers={"Origin": base_url}).status_code == 200
