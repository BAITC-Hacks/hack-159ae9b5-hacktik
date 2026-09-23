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

from .catalog import CatalogGateway, DemoCatalog
from .router import build_router
from .service import ChatResponse, ChatService, PendingView, SessionRegistry

logger = logging.getLogger(__name__)
STATIC = Path(__file__).parent.parent / "static"
COOKIE = "ekt_assistant_session"


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=1000)
    locale: Literal["ru", "kk"] = "ru"


class ProposeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku: str = Field(min_length=1, max_length=80)
    quantity: int = Field(ge=1, le=10000)
    locale: Literal["ru", "kk"] = "ru"


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pending_id: str = Field(min_length=32, max_length=32)
    locale: Literal["ru", "kk"] = "ru"


def create_app(catalog: CatalogGateway | None = None, router=None, procurement_db=None) -> FastAPI:
    app = FastAPI(title="EKT · Закупки", version="2.0.0")
    app.state.catalog = catalog if catalog is not None else DemoCatalog()
    app.state.sessions = SessionRegistry()
    app.state.chat = ChatService(app.state.catalog, router if router is not None else build_router())
    from .procurement_db import Database
    from .procurement_api import install_procurement
    install_procurement(app, procurement_db if procurement_db is not None else Database(os.getenv("EKT_DB_PATH")))

    @app.middleware("http")
    async def same_origin_guard(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if request.headers.get("sec-fetch-site") == "cross-site":
                return Response(status_code=403)
            origin = request.headers.get("origin")
            expected = os.getenv("PUBLIC_ORIGIN", f"{request.url.scheme}://{request.url.netloc}")
            if origin and origin.rstrip("/") != expected.rstrip("/"):
                return Response(status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; frame-ancestors 'self'"
        )
        return response

    def session_for(request: Request, response: Response):
        session = app.state.sessions.get(request.cookies.get(COOKIE))
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
                "cart": await app.state.catalog.get_cart(session.id), "pending": pending_view}

    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(body: ChatRequest, request: Request, response: Response):
        session = session_for(request, response)
        try:
            return await app.state.chat.chat(session, body.message.strip(), body.locale)
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
        return {"items": await app.state.catalog.get_cart(session.id), "demo": True}

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "catalog": "csv", "version": "2.0.0"}

    app.mount("/assets", StaticFiles(directory=STATIC), name="assets")

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/catalog-demo")
    async def catalog_demo():
        return FileResponse(STATIC / "catalog-demo.html")

    @app.get("/demo-cart")
    async def demo_cart():
        return FileResponse(STATIC / "cart.html")

    return app


app = create_app()
