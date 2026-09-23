import asyncio

import pytest
from fastapi.testclient import TestClient

from app.catalog import DemoCatalog, InsufficientStock, Product
from app.main import create_app
from app.router import DemoRouter


@pytest.fixture
def fixture_app():
    store = DemoCatalog()
    return TestClient(create_app(catalog=store, router=DemoRouter())), store


def propose(client, sku="DEMO-C16-ALT", quantity=2):
    return client.post("/api/cart/propose", json={"sku": sku, "quantity": quantity, "locale": "ru"}).json()


def confirm(client, pending_id):
    return client.post("/api/cart/confirm", json={"pending_id": pending_id, "locale": "ru"}).json()


def test_cart_needs_explicit_confirmation(fixture_app):
    client, store = fixture_app
    pending = propose(client)
    assert pending["pending"]["quantity"] == 2
    assert pending["cart"] == []
    assert asyncio.run(store.check_stock("DEMO-C16-ALT")) == 7
    response = confirm(client, pending["pending"]["id"])
    assert len(response["cart"]) == 1
    assert response["cart"][0]["unit_price_kzt"] == 3200
    assert response["checkout_url"] == "/demo-cart"
    assert asyncio.run(store.check_stock("DEMO-C16-ALT")) == 5
    assert len(confirm(client, pending["pending"]["id"])["cart"]) == 1


def test_invalid_sku_and_excess_quantity_cannot_enter_cart(fixture_app):
    client, _ = fixture_app
    assert propose(client, "UNKNOWN")["pending"] is None
    too_many = propose(client, quantity=8)
    assert too_many["pending"] is None
    assert too_many["products"][0]["stock"] == 7
    assert client.get("/api/cart").json()["items"] == []
    assert client.post("/api/cart/propose", json={"sku": "DEMO-C16-ALT", "quantity": 0}).status_code == 422


def test_out_of_stock_alternatives_match_all_required_attributes(fixture_app):
    client, _ = fixture_app
    result = client.post("/api/chat", json={"message": "DEMO-C16-OOS", "locale": "ru"}).json()
    assert result["products"][0]["stock"] == 0
    assert [p["sku"] for p in result["alternatives"]] == ["DEMO-C16-ALT"]
    assert result["alternative_reason"]
    prepared = propose(client, "DEMO-C16-OOS")
    assert prepared["pending"] is None
    assert prepared["alternatives"][0]["sku"] == "DEMO-C16-ALT"


def test_model_cannot_override_current_price(fixture_app):
    client, _ = fixture_app
    body = {"sku": "DEMO-C16-ALT", "quantity": 1, "locale": "ru", "price_kzt": 1}
    assert client.post("/api/cart/propose", json=body).status_code == 422
    pending = propose(client, quantity=1)["pending"]
    forged = {"pending_id": pending["id"], "locale": "ru", "price_kzt": 1}
    assert client.post("/api/cart/confirm", json=forged).status_code == 422
    assert confirm(client, pending["id"])["cart"][0]["unit_price_kzt"] == 3200


def test_changed_price_requires_new_confirmation(fixture_app):
    client, store = fixture_app
    pending = propose(client, quantity=1)["pending"]
    store._products["DEMO-C16-ALT"].price_kzt = 4000
    result = confirm(client, pending["id"])
    assert result["cart"] == []
    assert result["checkout_url"] is None
    assert result["products"][0]["price_kzt"] == 4000
    assert asyncio.run(store.check_stock("DEMO-C16-ALT")) == 7


def test_changed_stock_is_rechecked_at_commit(fixture_app):
    client, store = fixture_app
    pending = propose(client, quantity=3)["pending"]
    store._products["DEMO-C16-ALT"].stock = 1
    result = confirm(client, pending["id"])
    assert result["cart"] == []
    assert result["products"][0]["stock"] == 1
    assert asyncio.run(store.check_stock("DEMO-C16-ALT")) == 1


def test_pending_is_bound_to_session_and_intervening_question_invalidates_it(fixture_app):
    client, _ = fixture_app
    pending = propose(client)["pending"]
    other = TestClient(client.app)
    assert confirm(other, pending["id"])["cart"] == []
    client.post("/api/chat", json={"message": "доставка", "locale": "ru"})
    assert confirm(client, pending["id"])["cart"] == []
    assert client.post("/api/chat", json={"message": "да", "locale": "ru"}).json()["cart"] == []


def test_qualified_text_confirmation_and_kazakh_terms(fixture_app):
    client, _ = fixture_app
    propose(client, quantity=1)
    result = client.post("/api/chat", json={"message": "Да, добавь", "locale": "ru"}).json()
    assert result["cart"][0]["quantity"] == 1
    terms = client.post("/api/chat", json={"message": "Жеткізу шарттары қандай?", "locale": "ru"}).json()
    assert terms["locale"] == "kk"
    assert terms["sources"][0]["url"] == "https://ekt.kz/checkout-delivery/"
    short = client.post("/api/chat", json={"message": "шам", "locale": "kk"}).json()
    assert short["locale"] == "kk"


def test_cross_site_request_is_rejected(fixture_app):
    client, _ = fixture_app
    blocked = client.post("/api/cart/propose", json={"sku": "DEMO-C16-ALT", "quantity": 1},
                          headers={"Origin": "https://attacker.example"})
    assert blocked.status_code == 403
    assert client.get("/api/cart").json()["items"] == []


def test_technical_rating_is_not_counted_as_quantity():
    intent = asyncio.run(DemoRouter().classify("добавь автомат 16 А", []))
    assert intent.quantity == 1
    assert "16" in intent.query
    intent = asyncio.run(DemoRouter().classify("добавь 2 шт DEMO-C16-ALT", []))
    assert intent.quantity == 2
    assert intent.sku == "DEMO-C16-ALT"


def test_payment_details_do_not_reach_intent_router(fixture_app):
    client, _ = fixture_app
    pending = propose(client)["pending"]
    response = client.post("/api/chat", json={"message": "card number 4111 1111 1111 1111", "locale": "ru"}).json()
    assert "Не отправляйте" in response["reply"]
    assert response["pending"] is None
    assert confirm(client, pending["id"])["cart"] == []


def test_certificate_url_comes_only_from_catalog():
    product = Product(sku="DEMO-DOC", name_ru="Образец", name_kk="Үлгі", category="fixture",
                      price_kzt=42, stock=1, specifications={}, compatibility={},
                      certificate_url="https://ekt.kz/upload/demo.pdf")
    with TestClient(create_app(catalog=DemoCatalog([product]), router=DemoRouter())) as client:
        data = client.post("/api/chat", json={"message": "DEMO-DOC", "locale": "ru"}).json()
        assert data["products"][0]["certificate_url"] == "https://ekt.kz/upload/demo.pdf"


def test_atomic_demo_inventory_under_concurrent_carts():
    async def scenario():
        store = DemoCatalog([Product(sku="DEMO-LAST", name_ru="Тест", name_kk="Сынақ",
                                     category="test", price_kzt=20, stock=1,
                                     specifications={}, compatibility={})])
        attempts = await asyncio.gather(
            store.add_checked("one", "DEMO-LAST", 1, "ru", 20),
            store.add_checked("two", "DEMO-LAST", 1, "ru", 20),
            return_exceptions=True)
        assert sum(isinstance(item, InsufficientStock) for item in attempts) == 1
        assert (await store.check_stock("DEMO-LAST")) == 0
    asyncio.run(scenario())
