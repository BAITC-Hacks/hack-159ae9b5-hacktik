from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from .catalog import CatalogGateway
from .router import build_router
from .service import ChatResponse, ChatService, PendingView, SessionRegistry
from .platform import mount_platform, AUTH_COOKIE
from .geo import mount_geo
from .source_data import SourceLibrary
from .recommendations import mount_recommendations

logger = logging.getLogger(__name__)
STATIC = Path(__file__).parent.parent / "static"
COOKIE = "ekt_assistant_session"


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=6000)
    locale: Literal["ru", "kk"] = "ru"
    lat: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    lng: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)


class ProposeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku: str = Field(min_length=1, max_length=80)
    quantity: int = Field(ge=1, le=10000)
    locale: Literal["ru", "kk"] = "ru"


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pending_id: str = Field(min_length=32, max_length=32)
    locale: Literal["ru", "kk"] = "ru"


def create_app(catalog: CatalogGateway | None = None, router=None, db_path=None) -> FastAPI:
    app = FastAPI(title="EKT Space — товары, данные и ассистент", version="2.0.0")
    platform = mount_platform(app, db_path=db_path or (":memory:" if catalog is not None else None))
    library = SourceLibrary(Path(__file__).parent.parent / "data" / "sources.sqlite3")
    original_context, original_status = platform.context, platform.status

    def context_with_sources(query, lat=None, lng=None):
        context = original_context(query, lat=lat, lng=lng)
        context["knowledge"] = library.search(query) + context.get("knowledge", [])
        return context

    def status_with_sources():
        return {**original_status(), **library.status()}

    platform.context, platform.status = context_with_sources, status_with_sources
    app.state.catalog = catalog if catalog is not None else platform.catalog
    app.state.sessions = SessionRegistry()
    app.state.chat = ChatService(app.state.catalog, router if router is not None else build_router(), platform=platform)
    mount_geo(app)
    mount_recommendations(app)

    @app.middleware("http")
    async def same_origin_guard(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if request.headers.get("sec-fetch-site") == "cross-site":
                return Response(status_code=403)
            origin = request.headers.get("origin")
            expected = os.getenv("PUBLIC_ORIGIN") or f"{request.url.scheme}://{request.url.netloc}"
            if origin and origin.rstrip("/") != expected.rstrip("/"):
                return Response(status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' https://unpkg.com; "
            "style-src 'self' 'unsafe-inline' https://unpkg.com; "
            "connect-src 'self'; img-src 'self' data: https://tile.openstreetmap.org https://*.tile.openstreetmap.org https://unpkg.com; "
            "frame-ancestors 'self'; base-uri 'self'; form-action 'self'"
        )
        response.headers["Permissions-Policy"] = "geolocation=(self)"
        if request.url.path in {"/docs", "/redoc", "/docs/oauth2-redirect"}:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "img-src 'self' data: https://fastapi.tiangolo.com; connect-src 'self'; "
                "font-src 'self' https://fonts.gstatic.com; frame-ancestors 'self'"
            )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def session_for(request: Request, response: Response):
        session = app.state.sessions.get(request.cookies.get(COOKIE))
        user = platform.user_for_token(request.cookies.get(AUTH_COOKIE))
        session.cart_key = "user:" + user["id"] if user else None
        response.set_cookie(COOKIE, session.id, httponly=True, samesite="strict",
                            secure=request.url.scheme == "https", max_age=3600, path="/")
        return session

    @app.get("/api/bootstrap")
    async def bootstrap(request: Request, response: Response):
        session = session_for(request, response)
        pending = session.pending
        if pending and time.time() > pending.expires_at:
            session.pending = pending = None
        pending_view = None
        if pending:
            product = await app.state.catalog.get_product(pending.sku)
            if product:
                pending_view = PendingView(id=pending.id, sku=pending.sku, name=product.name("ru"),
                                           quantity=pending.quantity, price_kzt=pending.price_kzt,
                                           expires_at=pending.expires_at)
        return {"demo": True, "ai_enabled": bool(os.getenv("OPENAI_API_KEY")),
                "geocoding_enabled": bool(os.getenv("GEOAPIFY_API_KEY")),
                "data": platform.status(),
                "cart": await app.state.catalog.get_cart(session.cart_key or session.id), "pending": pending_view}

    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(body: ChatRequest, request: Request, response: Response):
        session = session_for(request, response)
        try:
            if (body.lat is None) != (body.lng is None):
                raise HTTPException(status_code=422, detail="Передайте обе координаты")
            return await app.state.chat.chat(session, body.message, body.locale, lat=body.lat, lng=body.lng)
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Assistant request failed")
            raise HTTPException(status_code=503, detail="Assistant temporarily unavailable") from exc

    @app.post("/api/cart/propose", response_model=ChatResponse)
    async def propose(body: ProposeRequest, request: Request, response: Response):
        session = session_for(request, response)
        return await app.state.chat.propose(session, body.sku, body.quantity, body.locale)

    @app.post("/api/cart/confirm", response_model=ChatResponse)
    async def confirm(body: ConfirmRequest, request: Request, response: Response):
        session = session_for(request, response)
        return await app.state.chat.confirm(session, body.pending_id, body.locale)

    @app.get("/api/cart")
    async def cart(request: Request, response: Response):
        session = session_for(request, response)
        return {"items": await app.state.catalog.get_cart(session.cart_key or session.id), "demo": True}

    @app.get("/api/sources/{document_id}")
    async def source_document(document_id: int):
        document = library.document(document_id)
        if not document:
            raise HTTPException(404, "Источник не найден")
        return document

    @app.get("/api/knowledge")
    async def knowledge(q: str = ""):
        return {"items": platform.context(q).get("knowledge", [])}

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "catalog": "sqlite", "ai_enabled": bool(os.getenv("OPENAI_API_KEY"))}

    app.mount("/assets", StaticFiles(directory=STATIC), name="assets")

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/demo-cart")
    async def demo_cart():
        return FileResponse(STATIC / "cart.html")

    return app


app = create_app()
