import asyncio
import io
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.catalog import InsufficientStock
from app.platform import AUTH_COOKIE, PlatformStore, mount_platform


@pytest.fixture
def platform(tmp_path, monkeypatch):
    monkeypatch.setenv("EKT_SEED_DEMO", "true")
    app = FastAPI()
    store = mount_platform(app, tmp_path / "test.sqlite3")
    yield app, store
    store.close()


def register(client, email="one@example.org"):
    result = client.post("/api/auth/register", json={"name": "Менеджер", "email": email, "password": "correct horse battery"})
    assert result.status_code == 201, result.text
    return result.json()["user"]


def offer_body(**changes):
    return dict(title="Кабель со склада", category="cable", quantity=20, original_price=1000,
                price=700, lat=43.24, lng=76.90, address="Алматы, тестовый склад", **changes)


def test_guest_and_account_isolation_and_revocation(platform):
    app, store = platform
    first, second = TestClient(app), TestClient(app)
    assert first.get("/api/auth/me").json() == {"user": None}
    assert first.post("/api/offers", json=offer_body()).status_code == 401
    first.cookies.set("ekt_assistant_session", "old-chat", domain="testserver.local", path="/")
    user = register(first)
    token = first.cookies.get(AUTH_COOKIE)
    assert first.cookies.get("ekt_assistant_session") is None
    assert first.get("/api/auth/me").json()["user"] == user
    assert second.get("/api/auth/me").json()["user"] is None
    assert "HttpOnly" in first.post("/api/auth/login", json={"email": "one@example.org", "password": "correct horse battery"}).headers["set-cookie"]
    assert store.user_for_token(token) is None  # login rotates and revokes the previous token
    token = first.cookies.get(AUTH_COOKIE)
    assert first.post("/api/auth/logout").status_code == 200
    assert store.user_for_token(token) is None
    assert first.post("/api/auth/login", json={"email": "one@example.org", "password": "wrong password"}).status_code == 401
    assert "password_hash" not in json.dumps(user)


def test_offer_ownership_validation_and_radius(platform):
    app, _ = platform
    owner, other = TestClient(app), TestClient(app)
    owner_id = register(owner)["id"]
    register(other, "two@example.org")
    created = owner.post("/api/offers", json=offer_body())
    assert created.status_code == 201
    offer = created.json()["item"]
    assert offer["owner_id"] == owner_id and offer["discount_percent"] == 30 and not offer["is_demo"]
    offer_id = offer["id"]
    assert other.delete(f"/api/offers/{offer_id}").status_code == 404
    assert other.put(f"/api/offers/{offer_id}", json=offer_body()).status_code == 404
    assert len(owner.get("/api/offers?mine=true").json()["items"]) == 1
    assert other.get("/api/offers?mine=true").json()["items"] == []
    near = owner.get("/api/offers?lat=43.24&lng=76.90&radius_km=0.1").json()["items"]
    assert any(item["id"] == offer_id and item["distance_km"] == 0 for item in near)
    for params in ("lat=nan&lng=1", "lat=43", "lat=91&lng=1", "radius_km=1", "lat=1&lng=1&radius_km=-1"):
        assert owner.get("/api/offers?" + params).status_code == 422
    invalid = offer_body()
    invalid["price"] = invalid["original_price"]
    assert owner.post("/api/offers", json=invalid).status_code == 422
    assert owner.delete(f"/api/offers/{offer_id}").status_code == 200


def test_import_is_atomic_and_persistent(tmp_path, monkeypatch):
    monkeypatch.setenv("EKT_SEED_DEMO", "false")
    path = tmp_path / "persistent.sqlite3"
    store = PlatformStore(path)
    valid = {"sku": "real-1", "name_ru": "Провод", "category": "cable", "price_kzt": 500, "stock": 7}
    invalid = dict(valid, sku="real-2", stock=-3)
    with pytest.raises(ValueError, match="Строка 2"):
        store.import_records(json.dumps([valid, invalid]).encode(), "products.json", "products", "owner")
    assert store.status()["products"] == 0
    store.import_records(json.dumps([valid]).encode(), "products.json", "products", "owner")
    store.import_records(json.dumps([{"title": "Доставка", "content": "Доставка выполняется за 3 дня."}]).encode(), "faq.json", "knowledge", "owner")
    store.close()
    reopened = PlatformStore(path)
    try:
        assert reopened.status()["real_products"] == 1
        assert reopened.status()["knowledge"] == 1
        assert reopened.product_records()[0]["data"]["sku"] == "REAL-1"
        context = reopened.context("Доставка")
        assert context["knowledge"][0]["source"] == "faq.json"
        assert context["knowledge"][0]["content"] == "Доставка выполняется за 3 дня."
    finally:
        reopened.close()


def test_file_upload_formats_and_auth(platform):
    app, store = platform
    client = TestClient(app)
    rows = b"sku,name_ru,category,price_kzt,stock\nNEW-1,Wire,cable,500,30\n"
    assert client.post("/api/data/import", data={"kind": "products"}, files={"file": ("catalog.csv", rows)}).status_code == 401
    register(client)
    result = client.post("/api/data/import", data={"kind": "products"}, files={"file": ("catalog.csv", rows)})
    assert result.status_code == 200 and result.json()["rows"] == 1
    from openpyxl import Workbook
    book = Workbook()
    book.active.append(["question", "answer"])
    book.active.append(["Склад", "Склад открыт с 9 до 18"])
    payload = io.BytesIO()
    book.save(payload)
    result = client.post("/api/data/import", data={"kind": "knowledge"}, files={"file": ("faq.xlsx", payload.getvalue())})
    assert result.status_code == 200, result.text
    assert store.context("склад")["knowledge"][0]["content"] == "Склад открыт с 9 до 18"
    assert client.post("/api/data/import", data={"kind": "products"}, files={"file": ("evil.sql", b"DROP TABLE products;")}).status_code == 422
    assert client.post("/api/data/import", data={"kind": "products"}, files={"file": ("catalog.json", b"not json")}).status_code == 422


def test_concurrent_inventory_reservation_is_atomic(tmp_path, monkeypatch):
    monkeypatch.setenv("EKT_SEED_DEMO", "true")
    path = tmp_path / "inventory.sqlite3"
    stores = [PlatformStore(path), PlatformStore(path)]
    def reserve(store):
        try:
            asyncio.run(store.catalog.add_checked("cart-session", "DEMO-C16-ALT", 5, "ru", 3200))
            return True
        except InsufficientStock:
            return False
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(reserve, stores))
        assert results.count(True) == 1
        assert asyncio.run(stores[0].catalog.check_stock("DEMO-C16-ALT")) == 2
        assert len(asyncio.run(stores[1].catalog.get_cart("cart-session"))) == 1
    finally:
        for store in stores:
            store.close()


def test_demo_data_remains_explicit(platform):
    _, store = platform
    assert store.status()["demo_products"] == 4
    assert store.status()["real_products"] == 0
    assert all(item["is_demo"] for item in store.offers())
    assert store.status()["model_trained"] is False


def test_import_row_limit_and_oversize_rejected(platform):
    _, store = platform
    with pytest.raises(ValueError, match="10 000"):
        store.import_records(json.dumps([{}] * 10001).encode(), "too-many.json", "knowledge", "owner")
    with pytest.raises(ValueError, match="5 МБ"):
        store.import_records(b"x" * (5 * 1024 * 1024 + 1), "big.csv", "knowledge", "owner")
    assert store.status()["knowledge"] == 0
