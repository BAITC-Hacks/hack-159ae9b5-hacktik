from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Scenario(StrictModel):
    as_of: date = Field(default_factory=date.today)
    review_days: int = Field(default=30, ge=1, le=180)
    safety_days: int = Field(default=0, ge=0, le=90)
    fallback_lead_days: int = Field(default=0, ge=0, le=180)
    growth_pct: float = Field(default=0, ge=-80, le=200)
    warehouse: str = ""
    category: str = ""
    brand: str = ""


class SupplierInput(StrictModel):
    supplier_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    lead_days: int = Field(ge=0, le=365)
    minimum_order_value: float = Field(default=0, ge=0)


class AssignmentInput(StrictModel):
    product_ids: list[str] = Field(min_length=1, max_length=3000)
    supplier_id: str


class ItemEdit(StrictModel):
    stock_qty: float | None = Field(default=None, ge=0)
    reserved_qty: float | None = Field(default=None, ge=0)
    stock_basis: Literal["physical_snapshot", "free_stock_snapshot", "monthly_balance_proxy"] | None = None
    stock_date: date | None = None
    category: str | None = Field(default=None, max_length=100)
    supplier_id: str | None = None
    unit_cost: float | None = Field(default=None, ge=0)
    moq: float | None = Field(default=None, gt=0)
    moq_rule: Literal["minimum", "multiple"] | None = None


class ImportInput(StrictModel):
    kind: Literal["snapshot", "sales", "monthly_sales", "stockouts", "inbound", "suppliers", "assignments", "categories"]
    csv_text: str = Field(min_length=1, max_length=15_000_000)
    replace: bool = False


class OrderLineInput(StrictModel):
    product_id: str
    warehouse: str = "unspecified"
    quantity: float = Field(gt=0, le=1e12)
    reason: str = Field(default="", max_length=1000)


class OrderInput(StrictModel):
    scenario: Scenario
    lines: list[OrderLineInput] = Field(min_length=1, max_length=3000)


class ApprovalInput(StrictModel):
    reviewer: str = Field(min_length=2, max_length=100)
    acknowledge_data_gaps: bool = False
    confirmed: Literal[True]

    @field_validator("reviewer")
    @classmethod
    def real_label(cls, value):
        if len(value.strip()) < 2:
            raise ValueError("Укажите ответственного сотрудника")
        return value.strip()


class AssistantInput(StrictModel):
    message: str = Field(min_length=1, max_length=1500)
    scenario: Scenario = Field(default_factory=Scenario)
    product_id: str = ""
