from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from typing import Protocol

from pydantic import BaseModel, Field, HttpUrl


class Product(BaseModel):
    sku: str
    name_ru: str
    name_kk: str
    category: str
    price_kzt: int = Field(ge=0)
    stock: int = Field(ge=0)
    specifications: dict[str, str]
    # Every field in this mapping must match exactly for a suggested alternative.
    compatibility: dict[str, str]
    certificate_url: HttpUrl | None = None

    def name(self, locale: str) -> str:
        return self.name_kk if locale == "kk" else self.name_ru


class CartLine(BaseModel):
    sku: str
    name: str
    quantity: int
    unit_price_kzt: int


class CatalogError(Exception):
    pass


class UnknownProduct(CatalogError):
    pass


class InsufficientStock(CatalogError):
    def __init__(self, available: int):
        self.available = available


class PriceChanged(CatalogError):
    def __init__(self, current_price_kzt: int):
        self.current_price_kzt = current_price_kzt


class CatalogGateway(Protocol):
    async def search_products(self, query: str) -> list[Product]: ...
    async def get_product(self, sku: str) -> Product | None: ...
    async def check_stock(self, sku: str) -> int | None: ...
    async def find_alternatives(self, sku: str) -> list[Product]: ...
    async def add_checked(self, session_id: str, sku: str, quantity: int, locale: str,
                          expected_price_kzt: int) -> CartLine: ...
    async def get_cart(self, session_id: str) -> list[CartLine]: ...
    async def checkout_url(self, session_id: str) -> str: ...


class DemoCatalog:
    """A single-process, atomic demo inventory and cart. Replace as one gateway in production."""

    def __init__(self, products: list[Product] | None = None):
        self._products = {p.sku: p.model_copy(deep=True) for p in (products or demo_products())}
        self._carts: dict[str, list[CartLine]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def search_products(self, query: str) -> list[Product]:
        words = re.findall(r"[\w]+", query.casefold())
        if not words:
            return []
        scored: list[tuple[int, Product]] = []
        for product in self._products.values():
            haystack = " ".join((product.sku, product.name_ru, product.name_kk,
                                 product.category, *product.specifications.values())).casefold()
            score = sum(1 for word in words if word in haystack)
            if score:
                scored.append((score, product))
        scored.sort(key=lambda item: (-item[0], item[1].sku))
        return [p.model_copy(deep=True) for _, p in scored[:5]]

    async def get_product(self, sku: str) -> Product | None:
        product = self._products.get(sku.upper())
        return product.model_copy(deep=True) if product else None

    async def check_stock(self, sku: str) -> int | None:
        product = await self.get_product(sku)
        return product.stock if product else None

    async def find_alternatives(self, sku: str) -> list[Product]:
        original = self._products.get(sku.upper())
        if not original:
            return []
        matches = [p for p in self._products.values()
                   if p.sku != original.sku and p.stock > 0
                   and p.category == original.category
                   and p.compatibility == original.compatibility]
        return [p.model_copy(deep=True) for p in matches[:3]]

    async def add_checked(self, session_id: str, sku: str, quantity: int, locale: str,
                          expected_price_kzt: int) -> CartLine:
        # The inventory check, price read, decrement and cart write share one lock.
        # A production gateway must perform this atomically against the actual shop cart/inventory.
        if quantity <= 0:
            raise ValueError("Quantity must be positive")
        async with self._lock:
            product = self._products.get(sku.upper())
            if product is None:
                raise UnknownProduct()
            if product.price_kzt != expected_price_kzt:
                raise PriceChanged(product.price_kzt)
            if product.stock < quantity:
                raise InsufficientStock(product.stock)
            line = CartLine(sku=product.sku, name=product.name(locale), quantity=quantity,
                            unit_price_kzt=product.price_kzt)
            product.stock -= quantity
            self._carts[session_id].append(line)
            return line.model_copy()

    async def get_cart(self, session_id: str) -> list[CartLine]:
        return [line.model_copy() for line in self._carts[session_id]]

    async def checkout_url(self, session_id: str) -> str:
        return "/demo-cart"


def demo_products() -> list[Product]:
    # DEMO SKUs, prices and stock are invented fixtures, never live EKT data.
    return [
        Product(sku="DEMO-C16-OOS", name_ru="Автоматический выключатель C16, 1P (образец A)",
                name_kk="C16, 1P автоматты ажыратқышы (A үлгісі)", category="breaker",
                price_kzt=2900, stock=0,
                specifications={"current": "16 А", "poles": "1", "curve": "C",
                                "breaking_capacity": "6 кА"},
                compatibility={"current": "16A", "poles": "1", "curve": "C", "breaking": "6kA"}),
        Product(sku="DEMO-C16-ALT", name_ru="Автоматический выключатель C16, 1P (образец B)",
                name_kk="C16, 1P автоматты ажыратқышы (B үлгісі)", category="breaker",
                price_kzt=3200, stock=7,
                specifications={"current": "16 А", "poles": "1", "curve": "C",
                                "breaking_capacity": "6 кА"},
                compatibility={"current": "16A", "poles": "1", "curve": "C", "breaking": "6kA"}),
        Product(sku="DEMO-C10", name_ru="Автоматический выключатель C10, 1P (образец)",
                name_kk="C10, 1P автоматты ажыратқышы (үлгі)", category="breaker",
                price_kzt=2400, stock=12,
                specifications={"current": "10 А", "poles": "1", "curve": "C",
                                "breaking_capacity": "6 кА"},
                compatibility={"current": "10A", "poles": "1", "curve": "C", "breaking": "6kA"}),
        Product(sku="DEMO-LED12", name_ru="Светодиодная лампа E27, 12 Вт (образец)",
                name_kk="E27, 12 Вт жарықдиодты шамы (үлгі)", category="lamp",
                price_kzt=1150, stock=24,
                specifications={"socket": "E27", "power": "12 Вт", "color_temperature": "4000 K"},
                compatibility={"socket": "E27", "power": "12W", "color": "4000K"}),
    ]
