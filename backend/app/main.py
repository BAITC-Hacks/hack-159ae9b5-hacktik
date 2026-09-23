"""
FastAPI backend for the ИЭК procurement platform.

Endpoints follow spec section 19. The AI is reachable only via
/api/ai/* endpoints, which call ai_gateway.py's five restricted functions —
never the raw Anthropic API directly from a route, and never with DB/shell access.
"""
from __future__ import annotations
from datetime import date
from typing import Optional, List
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import data_loader, audit, ai_gateway
from .procurement_engine import calculate_batch, SkuInput
from .models import (
    CalculateRequest, CalculateResponse, RecommendationItem,
    AdjustItemRequest, ConfirmOrderRequest,
)

app = FastAPI(title="ИЭК — Расчёт потребности в закупках")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- in-memory stores (swap for PostgreSQL later; same shape) -------------
_DATASET: List[SkuInput] = data_loader.load_sample_dataset()
_CALCULATIONS: dict = {}   # calculation_id -> {"recs": [...], "items": {sku: SkuInput}, "meta": {...}}
_ORDERS: dict = {}         # order_id -> order dict


def _urgency_rank(u: str) -> int:
    return {"high": 0, "medium": 1, "low": 2}[u]


# --- reference data ---------------------------------------------------------

@app.get("/api/warehouses")
def get_warehouses():
    return data_loader.list_warehouses()


@app.get("/api/categories")
def get_categories():
    return data_loader.list_categories(_DATASET)


@app.get("/api/suppliers")
def get_suppliers():
    return data_loader.list_suppliers(_DATASET)


# --- core calculation --------------------------------------------------------

@app.post("/api/calculate", response_model=CalculateResponse)
def calculate(req: CalculateRequest):
    items = data_loader.filter_dataset(_DATASET, req.warehouse, req.category)
    recs = calculate_batch(items, target_date=date.today())

    calc_id = str(uuid4())
    _CALCULATIONS[calc_id] = {
        "recs": {r.sku: r for r in recs},
        "items": {i.sku: i for i in items},
        "meta": {"warehouse": req.warehouse, "category": req.category},
    }

    rec_items = [
        RecommendationItem(
            sku=r.sku, name=r.name, category=r.category, supplier=r.supplier,
            current_stock=r.current_stock, in_transit=r.in_transit,
            forecast_demand=r.forecast_demand, recommended_quantity=r.recommended_quantity,
            urgency=r.urgency, reasons=r.reasons, moq=r.moq,
            original_recommended_quantity=r.recommended_quantity,
        )
        for r in sorted(recs, key=lambda r: (_urgency_rank(r.urgency), -r.recommended_quantity))
        if r.recommended_quantity > 0
    ]

    return CalculateResponse(
        calculation_id=calc_id,
        warehouse=req.warehouse,
        category=req.category,
        generated_at=date.today().isoformat(),
        summary={
            "items_count": len(rec_items),
            "urgent_count": sum(1 for i in rec_items if i.urgency == "high"),
            "suppliers_count": len({i.supplier for i in rec_items}),
            "total_units": sum(i.recommended_quantity for i in rec_items),
        },
        items=rec_items,
    )


@app.get("/api/recommendations/{calculation_id}", response_model=CalculateResponse)
def get_recommendations(calculation_id: str):
    calc = _CALCULATIONS.get(calculation_id)
    if not calc:
        raise HTTPException(404, "Расчёт не найден")
    recs = list(calc["recs"].values())
    rec_items = [
        RecommendationItem(
            sku=r.sku, name=r.name, category=r.category, supplier=r.supplier,
            current_stock=r.current_stock, in_transit=r.in_transit,
            forecast_demand=r.forecast_demand, recommended_quantity=r.recommended_quantity,
            urgency=r.urgency, reasons=r.reasons, moq=r.moq,
        ) for r in recs if r.recommended_quantity > 0
    ]
    meta = calc["meta"]
    return CalculateResponse(
        calculation_id=calculation_id, warehouse=meta.get("warehouse"), category=meta.get("category"),
        generated_at=date.today().isoformat(),
        summary={
            "items_count": len(rec_items),
            "urgent_count": sum(1 for i in rec_items if i.urgency == "high"),
            "suppliers_count": len({i.supplier for i in rec_items}),
            "total_units": sum(i.recommended_quantity for i in rec_items),
        },
        items=rec_items,
    )


@app.get("/api/recommendations/{calculation_id}/product/{sku}")
def get_product_detail(calculation_id: str, sku: str):
    calc = _CALCULATIONS.get(calculation_id)
    if not calc or sku not in calc["recs"]:
        raise HTTPException(404, "Позиция не найдена")
    rec = calc["recs"][sku]
    item = calc["items"][sku]
    return {
        "sku": rec.sku, "name": rec.name, "category": rec.category, "supplier": rec.supplier,
        "monthly_sales": [{"month": m.month, "units": m.units_sold, "stockout_days": m.stockout_days}
                           for m in item.sales_history],
        "current_stock": rec.current_stock, "in_transit": rec.in_transit, "moq": rec.moq,
        "recommended_quantity": rec.recommended_quantity, "forecast_demand": rec.forecast_demand,
        "urgency": rec.urgency, "reasons": rec.reasons, "debug": rec.debug,
    }


# --- draft / adjust / confirm order ------------------------------------------

class DraftOrderRequest(BaseModel):
    calculation_id: str


@app.post("/api/orders/draft")
def draft_order(req: DraftOrderRequest):
    calc = _CALCULATIONS.get(req.calculation_id)
    if not calc:
        raise HTTPException(404, "Расчёт не найден")
    order_id = str(uuid4())
    _ORDERS[order_id] = {
        "order_id": order_id,
        "calculation_id": req.calculation_id,
        "status": "draft",
        "items": {sku: {"recommended": r.recommended_quantity, "quantity": r.recommended_quantity,
                          "manually_adjusted": False}
                   for sku, r in calc["recs"].items() if r.recommended_quantity > 0},
    }
    return {"order_id": order_id, "status": "draft"}


@app.patch("/api/orders/{order_id}/items/{sku}")
def adjust_item(order_id: str, sku: str, req: AdjustItemRequest):
    order = _ORDERS.get(order_id)
    if not order or sku not in order["items"]:
        raise HTTPException(404, "Позиция заказа не найдена")
    original = order["items"][sku]["recommended"]
    order["items"][sku]["quantity"] = req.new_quantity
    order["items"][sku]["manually_adjusted"] = (req.new_quantity != original)
    audit.log_change(req.user, sku, original, req.new_quantity, action="adjust")
    return order["items"][sku]


@app.post("/api/orders/{order_id}/confirm")
def confirm_order(order_id: str, req: ConfirmOrderRequest):
    order = _ORDERS.get(order_id)
    if not order:
        raise HTTPException(404, "Заказ не найден")
    order["status"] = "confirmed"
    for sku, data in order["items"].items():
        audit.log_change(req.user, sku, data["recommended"], data["quantity"], action="confirm")
    # Explicitly NOT sending to a supplier here — per spec, that step stays manual/out of scope.
    return {"order_id": order_id, "status": "confirmed"}


@app.get("/api/orders/{order_id}")
def get_order(order_id: str):
    order = _ORDERS.get(order_id)
    if not order:
        raise HTTPException(404, "Заказ не найден")
    return order


@app.get("/api/orders/{order_id}/export")
def export_order(order_id: str):
    order = _ORDERS.get(order_id)
    if not order:
        raise HTTPException(404, "Заказ не найден")
    calc = _CALCULATIONS.get(order["calculation_id"], {})
    recs = calc.get("recs", {})
    rows = ["SKU,Наименование,Поставщик,Количество,Изменено вручную"]
    for sku, data in order["items"].items():
        rec = recs.get(sku)
        name = rec.name if rec else sku
        supplier = rec.supplier if rec else ""
        rows.append(f"{sku},{name},{supplier},{data['quantity']},{data['manually_adjusted']}")
    return {"format": "csv", "content": "\n".join(rows)}


@app.get("/api/orders/{order_id}/audit-log")
def order_audit_log(order_id: str):
    order = _ORDERS.get(order_id)
    if not order:
        raise HTTPException(404, "Заказ не найден")
    skus = list(order["items"].keys())
    return [e for sku in skus for e in audit.get_log(sku)]


# --- restricted AI endpoints ---------------------------------------------

class ExplainRequest(BaseModel):
    calculation_id: str
    sku: str


class AskRequest(BaseModel):
    calculation_id: str
    sku: str
    question: str


@app.post("/api/ai/explain")
def ai_explain(req: ExplainRequest):
    calc = _CALCULATIONS.get(req.calculation_id)
    if not calc or req.sku not in calc["recs"]:
        raise HTTPException(404, "Позиция не найдена")
    text = ai_gateway.explain_recommendation(calc["recs"][req.sku])
    return {"sku": req.sku, "explanation": text}


@app.post("/api/ai/ask")
def ai_ask(req: AskRequest):
    calc = _CALCULATIONS.get(req.calculation_id)
    if not calc or req.sku not in calc["recs"]:
        raise HTTPException(404, "Позиция не найдена")
    text = ai_gateway.suggest_adjustment(calc["recs"][req.sku], req.question)
    return {"sku": req.sku, "answer": text}
