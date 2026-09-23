"""
Pydantic models / API schemas.
These mirror the /calculate response shape defined in the project spec (section 20).
"""
from __future__ import annotations
from typing import List, Optional, Literal
from pydantic import BaseModel

Urgency = Literal["low", "medium", "high"]


class CalculateRequest(BaseModel):
    warehouse: Optional[str] = None
    category: Optional[str] = None
    period_from: Optional[str] = None  # ISO date, optional period filter
    period_to: Optional[str] = None


class RecommendationItem(BaseModel):
    sku: str
    name: str
    category: str
    supplier: str
    current_stock: float
    in_transit: float
    forecast_demand: float
    recommended_quantity: float
    urgency: Urgency
    reasons: List[str]
    moq: Optional[float] = None
    manually_adjusted: bool = False
    original_recommended_quantity: Optional[float] = None


class CalculateResponse(BaseModel):
    calculation_id: str
    warehouse: Optional[str]
    category: Optional[str]
    generated_at: str
    summary: dict  # {items_count, urgent_count, suppliers_count, total_units}
    items: List[RecommendationItem]


class AdjustItemRequest(BaseModel):
    new_quantity: float
    user: str


class ConfirmOrderRequest(BaseModel):
    calculation_id: str
    user: str


class ProductDetail(BaseModel):
    sku: str
    name: str
    category: str
    supplier: str
    monthly_sales: List[dict]      # [{month: "2025-01", units: 120, stockout_days: 0}, ...]
    forecast: dict                  # {base_demand, trend_adjustment, seasonal_index, stockout_compensation}
    current_stock: float
    in_transit: float
    moq: Optional[float]
    recommended_quantity: float
    urgency: Urgency
    explanation: str
    outliers_excluded: List[dict]   # [{month, raw_units, reason}]
