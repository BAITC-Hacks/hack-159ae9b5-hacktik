from __future__ import annotations

import asyncio
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .catalog import (CartLine, CatalogGateway, InsufficientStock, PriceChanged,
                      Product, UnknownProduct)
from .router import is_cancel, is_explicit_yes, language_of
from .ai import KnowledgeAssistant, context_sources, public_context, remember


class ProductView(BaseModel):
    sku: str
    name: str
    price_kzt: int
    stock: int
    specifications: dict[str, str]
    certificate_url: str | None


class PendingView(BaseModel):
    id: str
    sku: str
    name: str
    quantity: int
    price_kzt: int
    expires_at: float


class Source(BaseModel):
    label: str
    url: str


class ChatResponse(BaseModel):
    reply: str
    locale: str
    products: list[ProductView] = Field(default_factory=list)
    alternatives: list[ProductView] = Field(default_factory=list)
    alternative_reason: str | None = None
    pending: PendingView | None = None
    cart: list[CartLine] = Field(default_factory=list)
    checkout_url: str | None = None
    sources: list[Source] = Field(default_factory=list)
    demo: bool = True
    mode: Literal["local", "ai"] = "local"
    offers: list[dict[str, Any]] = Field(default_factory=list)


@dataclass
class Pending:
    id: str
    sku: str
    quantity: int
    price_kzt: int
    expires_at: float


@dataclass
class Conversation:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    cart_key: str | None = None
    last_skus: list[str] = field(default_factory=list)
    pending: Pending | None = None
    history: list[dict[str, str]] = field(default_factory=list)
    last_seen: float = field(default_factory=time.time)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class SessionRegistry:
    def __init__(self):
        self.sessions: dict[str, Conversation] = {}

    def get(self, session_id: str | None) -> Conversation:
        now = time.time()
        if session_id in self.sessions and now - self.sessions[session_id].last_seen < 3600:
            session = self.sessions[session_id]
            session.last_seen = now
            return session
        if len(self.sessions) >= 1000:
            self.sessions = {key: value for key, value in self.sessions.items()
                             if now - value.last_seen < 3600}
        session = Conversation()
        self.sessions[session.id] = session
        return session


TERMS = {
    "delivery": ("Условия доставки зависят от города и заказа. Проверьте актуальные правила на сайте EKT.",
                 "Жеткізу шарттары қала мен тапсырысқа байланысты. Қазіргі шарттарды EKT сайтынан тексеріңіз.",
                 "https://ekt.kz/checkout-delivery/", "Доставка и оплата / Жеткізу және төлем"),
    "payment": ("Способы оплаты указаны на сайте EKT. Данные карты вводите только на защищённой странице оформления, не в чате.",
                "Төлем тәсілдері EKT сайтында көрсетілген. Карта деректерін чатта емес, тек қорғалған рәсімдеу бетінде енгізіңіз.",
                "https://ekt.kz/checkout-delivery/", "Доставка и оплата / Жеткізу және төлем"),
    "returns": ("Правила возврата и обмена смотрите на сайте EKT; условия конкретного заказа уточните у менеджера.",
                "Қайтару және айырбастау ережелері EKT сайтында бар; нақты тапсырыс бойынша менеджерден сұраңыз.",
                "https://ekt.kz/return/", "Возврат и обмен / Қайтару және айырбастау"),
    "general": ("Инструкции по оформлению заказа и условия покупки доступны на сайте EKT.",
                "Тапсырыс рәсімдеу нұсқаулығы мен сатып алу шарттары EKT сайтында берілген.",
                "https://ekt.kz/about/howto/", "Как сделать заказ / Тапсырыс беру"),
}


def contains_payment_secret(message: str) -> bool:
    if re.search(r"\bsk-[A-Za-z0-9_-]{10,}|\b(?:api_key|OPENAI_API_KEY)\s*[:=]\s*\S+", message):
        return True
    if re.search(r"(?i)\b(?:cvv|cvc|password|card\s*number|номер\s*карты|пароль)\b|құпия\s*сөз|карта\s*нөмірі", message):
        return True
    for candidate in re.findall(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)", message):
        digits = [int(char) for char in candidate if char.isdigit()]
        if 13 <= len(digits) <= 19:
            checksum = sum(d if i % 2 == 0 else (d * 2 - 9 if d * 2 > 9 else d * 2)
                           for i, d in enumerate(reversed(digits)))
            if checksum % 10 == 0:
                return True
    return False


class ChatService:
    def __init__(self, catalog: CatalogGateway, router: Any, platform: Any = None):
        self.catalog = catalog
        self.router = router
        self.platform = platform
        self.assistant = KnowledgeAssistant()

    @staticmethod
    def view(product: Product, locale: str) -> ProductView:
        return ProductView(sku=product.sku, name=product.name(locale), price_kzt=product.price_kzt,
                           stock=product.stock, specifications=product.specifications,
                           certificate_url=str(product.certificate_url) if product.certificate_url else None)

    async def response(self, session: Conversation, locale: str, reply: str, **kwargs: Any) -> ChatResponse:
        return ChatResponse(reply=reply, locale=locale,
                            cart=await self.catalog.get_cart(session.cart_key or session.id), **kwargs)

    async def chat(self, session: Conversation, message: str, requested_locale: str,
                   lat: float | None = None, lng: float | None = None) -> ChatResponse:
        locale = language_of(message, requested_locale)
        async with session.lock:
            result = await self._chat_locked(session, message, locale, lat, lng)
            if not contains_payment_secret(message):
                remember(session.history, message, result.reply)
            return result

    async def _chat_locked(self, session: Conversation, message: str, locale: str,
                           lat: float | None, lng: float | None) -> ChatResponse:
        if contains_payment_secret(message):
            session.pending = None
            return await self.response(session, locale, self._msg(locale, "payment_block"))
        if session.pending and time.time() > session.pending.expires_at:
            session.pending = None
        if session.pending and is_explicit_yes(message):
            return await self._confirm_locked(session, session.pending.id, locale)
        if session.pending and is_cancel(message):
            session.pending = None
            return await self.response(session, locale, self._msg(locale, "cancel"))
        # An intervening question invalidates the old proposal; an isolated 'yes' later cannot use it.
        session.pending = None
        intent = await self.router.classify(message, session.last_skus)
        if intent.kind == "assistant":
            return await self._answer_locked(session, message, locale, lat, lng)
        if intent.kind == "terms":
            ru, kk, url, label = TERMS[intent.term]
            return await self.response(session, locale, kk if locale == "kk" else ru,
                                       sources=[Source(label=label, url=url)])
        if intent.kind == "clarify":
            return await self._answer_locked(session, message, locale, lat, lng)
        if intent.kind == "prepare":
            sku = intent.sku
            if not sku and intent.query:
                matches = await self.catalog.search_products(intent.query)
                if len(matches) == 1:
                    sku = matches[0].sku
                elif matches:
                    session.last_skus = [p.sku for p in matches]
                    return await self.response(session, locale, self._msg(locale, "choose"),
                                               products=[self.view(p, locale) for p in matches])
            if not sku and len(session.last_skus) == 1:
                sku = session.last_skus[0]
            if not sku:
                return await self.response(session, locale, self._msg(locale, "choose"))
            return await self._propose_locked(session, sku, intent.quantity, locale)
        if intent.kind == "details" and intent.sku:
            product = await self.catalog.get_product(intent.sku)
            matches = [product] if product else []
        else:
            matches = await self.catalog.search_products(intent.query or message)
        if not matches:
            return await self._answer_locked(session, message, locale, lat, lng)
        session.last_skus = [p.sku for p in matches]
        out_of_stock = next((p for p in matches if p.stock == 0), None)
        alternatives = await self.catalog.find_alternatives(out_of_stock.sku) if out_of_stock else []
        reply = self._msg(locale, "out" if out_of_stock else "found")
        return await self.response(session, locale, reply,
                                   products=[self.view(p, locale) for p in matches],
                                   alternatives=[self.view(p, locale) for p in alternatives],
                                   alternative_reason=self._msg(locale, "similar") if alternatives else None)

    async def _answer_locked(self, session: Conversation, message: str, locale: str,
                              lat: float | None, lng: float | None) -> ChatResponse:
        context = self.platform.context(message, lat=lat, lng=lng) if self.platform is not None else {}
        context = public_context(context)
        if not context["products"]:
            products = []
            if session.last_skus and re.search(r"\b(их|это|эти|этот|него|них|они|сравни|олар|осы)\b", message.casefold()):
                products = [product for sku in session.last_skus[:5]
                            if (product := await self.catalog.get_product(sku)) is not None]
            if not products and self.platform is None:
                products = await self.catalog.search_products(message)
            context["products"] = [item.model_dump(mode="json") for item in products]
            context = public_context(context)
        # Location is used by the retrieval layer only. Exact user coordinates are
        # not sent to the model; distances to public offers are enough for advice.
        reply, mode = await self.assistant.answer(message, locale, session.history, context,
                                                  has_location=lat is not None and lng is not None)
        return await self.response(session, locale, reply, mode=mode,
                                   sources=[Source(**source) for source in context_sources(context)],
                                   offers=context["offers"])

    async def propose(self, session: Conversation, sku: str, quantity: int, locale: str) -> ChatResponse:
        async with session.lock:
            session.pending = None
            return await self._propose_locked(session, sku, quantity, locale)

    async def _propose_locked(self, session: Conversation, sku: str, quantity: int,
                              locale: str) -> ChatResponse:
        product = await self.catalog.get_product(sku)
        if product is None:
            return await self.response(session, locale, self._msg(locale, "missing"))
        if quantity <= 0 or quantity > 10000:
            return await self.response(session, locale, self._msg(locale, "quantity"))
        if product.stock < quantity:
            alternatives = await self.catalog.find_alternatives(product.sku) if product.stock == 0 else []
            key = "out" if product.stock == 0 else "insufficient"
            return await self.response(session, locale, self._msg(locale, key),
                                       products=[self.view(product, locale)],
                                       alternatives=[self.view(p, locale) for p in alternatives],
                                       alternative_reason=self._msg(locale, "similar") if alternatives else None)
        pending = Pending(uuid.uuid4().hex, product.sku, quantity, product.price_kzt, time.time() + 600)
        session.pending = pending
        session.last_skus = [product.sku]
        return await self.response(session, locale, self._msg(locale, "confirm"),
                                   pending=PendingView(id=pending.id, sku=pending.sku,
                                                       name=product.name(locale), quantity=quantity,
                                                       price_kzt=product.price_kzt,
                                                       expires_at=pending.expires_at))

    async def confirm(self, session: Conversation, pending_id: str, locale: str) -> ChatResponse:
        async with session.lock:
            return await self._confirm_locked(session, pending_id, locale)

    async def _confirm_locked(self, session: Conversation, pending_id: str, locale: str) -> ChatResponse:
        pending = session.pending
        if pending is None or pending.id != pending_id or time.time() > pending.expires_at:
            session.pending = None
            return await self.response(session, locale, self._msg(locale, "expired"))
        # A one-time, server-side proposal binds SKU, quantity and observed price.
        session.pending = None
        try:
            line = await self.catalog.add_checked(session.cart_key or session.id, pending.sku, pending.quantity,
                                                  locale, pending.price_kzt)
        except PriceChanged:
            product = await self.catalog.get_product(pending.sku)
            return await self.response(session, locale, self._msg(locale, "price_changed"),
                                       products=[self.view(product, locale)] if product else [])
        except InsufficientStock:
            product = await self.catalog.get_product(pending.sku)
            alternatives = await self.catalog.find_alternatives(pending.sku) if product and product.stock == 0 else []
            return await self.response(session, locale, self._msg(locale, "insufficient"),
                                       products=[self.view(product, locale)] if product else [],
                                       alternatives=[self.view(p, locale) for p in alternatives],
                                       alternative_reason=self._msg(locale, "similar") if alternatives else None)
        except UnknownProduct:
            return await self.response(session, locale, self._msg(locale, "missing"))
        return await self.response(session, locale, self._msg(locale, "added"),
                                   checkout_url=await self.catalog.checkout_url(session.cart_key or session.id))

    @staticmethod
    def _msg(locale: str, key: str) -> str:
        ru, kk = {
            "cancel": ("Хорошо, добавление отменено.", "Жақсы, себетке қосу тоқтатылды."),
            "clarify": ("Напишите название товара, артикул или вопрос об условиях покупки.",
                        "Тауар атауын, артикулын немесе сатып алу шарттары туралы сұрақты жазыңыз."),
            "choose": ("Уточните, какой товар выбрать. Количество можно изменить перед подтверждением.",
                       "Қай тауарды таңдайтыныңызды нақтылаңыз. Растау алдында санын өзгертуге болады."),
            "missing": ("Не нашёл такой товар в доступном каталоге. Уточните артикул или обратитесь к менеджеру.",
                        "Бұл тауар қолжетімді каталогтан табылмады. Артикулын нақтылаңыз немесе менеджерге хабарласыңыз."),
            "found": ("Вот сведения из каталога. Уточните совместимость с вашим проектом у специалиста.",
                      "Каталогтағы мәліметтер төменде. Жобаңызға сәйкестігін маманнан нақтылаңыз."),
            "out": ("Запрошенного товара сейчас нет на складе. Ниже показаны возможные аналоги, если они найдены.",
                    "Сұралған тауар қазір қоймада жоқ. Табылса, ықтимал баламалар төменде көрсетілген."),
            "similar": ("Совпадают указанные ключевые характеристики; пригодность для вашего применения проверьте у специалиста.",
                        "Көрсетілген негізгі сипаттамалар сәйкес келеді; қолдануға жарамдылығын маманнан тексеріңіз."),
            "insufficient": ("Нужного количества сейчас нет в наличии. Посмотрите доступный остаток ниже.",
                             "Қажетті мөлшер қазір жоқ. Қолжетімді қалдық төменде көрсетілген."),
            "quantity": ("Укажите количество от 1 до 10 000.", "1-ден 10 000-ға дейінгі санды көрсетіңіз."),
            "confirm": ("Проверьте товар, количество и цену. Добавить его в корзину?",
                        "Тауарды, санын және бағасын тексеріңіз. Себетке қосайын ба?"),
            "expired": ("Подтверждение истекло или уже использовано. Выберите товар заново.",
                        "Растау мерзімі өтті немесе ол қолданылған. Тауарды қайта таңдаңыз."),
            "price_changed": ("Цена изменилась до добавления. Посмотрите новую цену и подтвердите покупку заново.",
                              "Қоспас бұрын баға өзгерді. Жаңа бағаны қарап, қайта растаңыз."),
            "added": ("Товар добавлен в демонстрационную корзину. Оформление оплаты здесь недоступно.",
                      "Тауар демонстрациялық себетке қосылды. Мұнда төлем жасау мүмкін емес."),
            "payment_block": ("Не отправляйте данные карты или пароль в чат. Используйте защищённую страницу оформления заказа.",
                              "Карта деректерін немесе құпия сөзді чатқа жібермеңіз. Қорғалған тапсырыс бетіне өтіңіз."),
        }[key]
        return kk if locale == "kk" else ru
