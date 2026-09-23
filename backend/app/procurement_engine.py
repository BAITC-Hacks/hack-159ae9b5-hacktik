"""
Procurement Engine — deterministic, explainable demand calculation.

This module contains NO AI calls. Every number it produces must be reproducible
from the same inputs. The AI Gateway (ai_gateway.py) only ever *reads* the output
of this module to explain it in natural language — it never influences the math.

Pipeline (per SKU):
  1. Load monthly sales history + stockout days + current stock/in-transit/MOQ.
  2. Detect & down-weight one-off outlier sales (big single spikes, or repeated
     large orders concentrated in one anonymized customer bucket).
  3. Compensate stockout months (sales during stockout periods understate real demand).
  4. Estimate a robust trend (only if growth is consistent across periods, not a
     single jump).
  5. Compute a seasonal index per calendar month from history.
  6. Combine into a forecast for the upcoming period, apply seasonal index.
  7. Recommend order quantity = max(0, forecast - stock - in_transit), rounded up
     to MOQ.
  8. Assign urgency from stock cover (days of stock remaining vs lead time).
  9. Emit a short, human-readable explanation ("reasons") for the UI.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from statistics import median
from typing import List, Optional
import math


# ---------------------------------------------------------------------------
# Input data shapes (populated by data_loader.py from Excel / DB)
# ---------------------------------------------------------------------------

@dataclass
class MonthlySales:
    month: str            # "YYYY-MM"
    units_sold: float
    stockout_days: int = 0        # days that month the SKU was fully out of stock
    days_in_month: int = 30
    large_single_customer_units: float = 0.0  # anonymized: units from one customer bucket
    is_single_large_order: bool = False        # pre-flagged one-off very large order


@dataclass
class SkuInput:
    sku: str
    name: str
    category: str
    supplier: str
    current_stock: float
    in_transit: float
    moq: Optional[float]
    sales_history: List[MonthlySales]   # chronological, oldest first, ~24 months
    lead_time_days: int = 21            # supplier lead time, default if unknown
    target_period_months: int = 1       # how many months ahead we're ordering for


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------

@dataclass
class Recommendation:
    sku: str
    name: str
    category: str
    supplier: str
    current_stock: float
    in_transit: float
    forecast_demand: float
    recommended_quantity: float
    urgency: str  # low / medium / high
    reasons: List[str]
    moq: Optional[float]
    debug: dict = field(default_factory=dict)  # factors shown behind "Подробнее"


# ---------------------------------------------------------------------------
# Step 1: outlier detection (one-off large orders)
# ---------------------------------------------------------------------------

def _detect_outliers(history: List[MonthlySales]) -> dict:
    """
    Median Absolute Deviation (MAD) based outlier detection — robust to skew,
    unlike mean/stddev which the outliers themselves would distort.
    Returns {month: cleaned_units} plus a list of excluded/adjusted months for
    the explanation, without ever discarding the record itself (audit-friendly).
    """
    raw = [m.units_sold for m in history if m.stockout_days == 0]
    if len(raw) < 4:
        cleaned = {m.month: m.units_sold for m in history}
        return {"cleaned": cleaned, "excluded": []}

    med = median(raw)
    abs_devs = [abs(x - med) for x in raw]
    mad = median(abs_devs) or 1.0
    # Modified z-score (Iglewicz & Hoaglin). Threshold 3.5 is a standard default.
    threshold = 3.5

    cleaned = {}
    excluded = []
    for m in history:
        if m.is_single_large_order:
            # Explicitly flagged one-off order: replace with the local median.
            cleaned[m.month] = med
            excluded.append({"month": m.month, "raw_units": m.units_sold,
                              "reason": "Отмечен как разовый крупный заказ"})
            continue
        z = 0.6745 * (m.units_sold - med) / mad
        if m.stockout_days == 0 and abs(z) > threshold and m.units_sold > med:
            cleaned[m.month] = med
            excluded.append({"month": m.month, "raw_units": m.units_sold,
                              "reason": "Аномальный всплеск (выброс)"})
        else:
            cleaned[m.month] = m.units_sold

    # Large-single-customer concentration: if one customer bucket accounts for
    # most of a month's sales and that month is far above the median, treat the
    # concentrated portion as non-recurring demand.
    for m in history:
        if m.month in [e["month"] for e in excluded]:
            continue
        if m.large_single_customer_units and m.units_sold > 0:
            share = m.large_single_customer_units / m.units_sold
            if share > 0.6 and m.units_sold > med * 1.8:
                adjusted = m.units_sold - m.large_single_customer_units * 0.8
                cleaned[m.month] = max(adjusted, med)
                excluded.append({"month": m.month, "raw_units": m.units_sold,
                                  "reason": "Крупная продажа одному клиенту (обезличено)"})

    return {"cleaned": cleaned, "excluded": excluded}


# ---------------------------------------------------------------------------
# Step 2: stockout compensation
# ---------------------------------------------------------------------------

def _compensate_stockouts(history: List[MonthlySales], cleaned: dict) -> dict:
    """
    If a SKU was out of stock for part of a month, scale that month's sales up
    to estimate the demand that was likely lost, capped at 2x to avoid wild
    extrapolation from very short in-stock windows.
    """
    compensated = dict(cleaned)
    notes = []
    for m in history:
        if m.stockout_days > 0 and m.days_in_month > m.stockout_days:
            in_stock_days = m.days_in_month - m.stockout_days
            factor = min(m.days_in_month / in_stock_days, 2.0)
            est = cleaned.get(m.month, m.units_sold) * factor
            compensated[m.month] = est
            notes.append({
                "month": m.month,
                "stockout_days": m.stockout_days,
                "raw_units": m.units_sold,
                "compensated_units": round(est, 1),
            })
    return {"compensated": compensated, "notes": notes}


# ---------------------------------------------------------------------------
# Step 3: trend estimation (robust — requires consistency, not one jump)
# ---------------------------------------------------------------------------

def _estimate_trend(history: List[MonthlySales], series: dict) -> dict:
    """
    Fits a simple linear trend over the cleaned/compensated series, but only
    "believes" it if growth is broadly monotonic across the recent window
    (checked via consecutive period-over-period direction), rather than driven
    by one big jump between two points.
    """
    months = [m.month for m in history]
    values = [series.get(mo, 0.0) for mo in months]
    n = len(values)
    if n < 6:
        return {"slope_per_month": 0.0, "trend_applied": False, "reason": "Недостаточно истории"}

    # Split into halves, compare average of last third vs first third to reduce
    # sensitivity to any single point, then confirm direction is consistent.
    third = max(n // 3, 2)
    first_avg = sum(values[:third]) / third
    last_avg = sum(values[-third:]) / third

    # Direction consistency: count how many consecutive 3-month windows moved
    # the same direction as the overall trend.
    window = 3
    directions = []
    for i in range(0, n - window, window):
        a = sum(values[i:i + window]) / window
        b = sum(values[i + window:i + 2 * window]) / window if i + 2 * window <= n else None
        if b is not None:
            directions.append(1 if b > a else (-1 if b < a else 0))

    overall_dir = 1 if last_avg > first_avg else (-1 if last_avg < first_avg else 0)
    consistent = sum(1 for d in directions if d == overall_dir)
    is_consistent = directions and (consistent / len(directions)) >= 0.6

    if not is_consistent or overall_dir == 0 or first_avg == 0:
        return {"slope_per_month": 0.0, "trend_applied": False,
                "reason": "Рост непостоянный — тренд не применён"}

    growth_ratio = (last_avg - first_avg) / max(first_avg, 1e-6)
    # Cap trend influence to +/-50% to avoid runaway extrapolation.
    growth_ratio = max(min(growth_ratio, 0.5), -0.5)
    return {
        "slope_per_month": growth_ratio / n,
        "trend_applied": True,
        "growth_ratio": round(growth_ratio, 3),
        "reason": "Устойчивый рост" if overall_dir > 0 else "Устойчивое снижение",
    }


# ---------------------------------------------------------------------------
# Step 4: seasonality index
# ---------------------------------------------------------------------------

def _seasonal_index(history: List[MonthlySales], series: dict, target_month_num: int) -> dict:
    """
    Seasonal index = avg(demand in calendar month M) / avg(demand across all months).
    Needs at least ~12 months of history per calendar month to be meaningful;
    falls back to 1.0 (no adjustment) otherwise.
    """
    by_month_num: dict = {}
    for m in history:
        month_num = int(m.month.split("-")[1])
        by_month_num.setdefault(month_num, []).append(series.get(m.month, 0.0))

    overall_values = [v for vals in by_month_num.values() for v in vals]
    if not overall_values:
        return {"index": 1.0, "confident": False}
    overall_avg = sum(overall_values) / len(overall_values)
    if overall_avg == 0:
        return {"index": 1.0, "confident": False}

    target_vals = by_month_num.get(target_month_num, [])
    if len(target_vals) < 1:
        return {"index": 1.0, "confident": False}

    target_avg = sum(target_vals) / len(target_vals)
    index = target_avg / overall_avg
    # Dampen extreme indices (e.g. from very sparse data) toward 1.0.
    index = 1.0 + (index - 1.0) * min(len(target_vals) / 2.0, 1.0)
    return {"index": round(index, 3), "confident": len(target_vals) >= 2}


# ---------------------------------------------------------------------------
# Step 5: urgency
# ---------------------------------------------------------------------------

def _urgency(current_stock: float, in_transit: float, daily_demand: float, lead_time_days: int) -> str:
    if daily_demand <= 0:
        return "low"
    cover_days = (current_stock + in_transit) / daily_demand
    if cover_days <= lead_time_days * 0.5:
        return "high"
    if cover_days <= lead_time_days:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Orchestration: full calculation for one SKU
# ---------------------------------------------------------------------------

def calculate_recommendation(item: SkuInput, target_date: Optional[date] = None) -> Recommendation:
    target_date = target_date or date.today()
    target_month_num = target_date.month

    outlier_result = _detect_outliers(item.sales_history)
    cleaned = outlier_result["cleaned"]

    stockout_result = _compensate_stockouts(item.sales_history, cleaned)
    compensated = stockout_result["compensated"]

    trend = _estimate_trend(item.sales_history, compensated)
    seasonality = _seasonal_index(item.sales_history, compensated, target_month_num)

    values = list(compensated.values())
    base_monthly_demand = sum(values) / len(values) if values else 0.0

    # Apply trend as an extrapolation to "now" from the middle of the history window.
    if trend["trend_applied"]:
        n = len(item.sales_history)
        months_from_mid = n / 2
        trended_demand = base_monthly_demand * (1 + trend["slope_per_month"] * months_from_mid)
    else:
        trended_demand = base_monthly_demand

    seasonal_demand = trended_demand * seasonality["index"]
    forecast_demand = max(seasonal_demand, 0.0) * item.target_period_months

    daily_demand = seasonal_demand / 30.0
    raw_recommended = forecast_demand - item.current_stock - item.in_transit
    recommended = max(raw_recommended, 0.0)

    if item.moq and recommended > 0:
        recommended = math.ceil(recommended / item.moq) * item.moq

    urgency = _urgency(item.current_stock, item.in_transit, daily_demand, item.lead_time_days)

    # ---- Explanation ----
    reasons = []
    if trend["trend_applied"]:
        reasons.append(f"{trend['reason']} спроса (изменение {trend['growth_ratio']*100:+.0f}% за период)")
    if seasonality["confident"] and abs(seasonality["index"] - 1.0) > 0.1:
        direction = "рост" if seasonality["index"] > 1 else "снижение"
        reasons.append(f"Сезонный {direction} спроса в текущем периоде (индекс {seasonality['index']})")
    if stockout_result["notes"]:
        reasons.append(f"Скорректирован упущенный спрос за периоды отсутствия на складе ({len(stockout_result['notes'])} мес.)")
    if outlier_result["excluded"]:
        reasons.append(f"Исключены разовые аномальные продажи ({len(outlier_result['excluded'])} случ.)")
    if item.current_stock <= 0:
        reasons.append("Текущий остаток отсутствует")
    elif urgency == "high":
        reasons.append(f"Низкий остаток относительно среднего спроса ({item.current_stock:.0f} шт.)")
    if item.in_transit:
        reasons.append(f"Учтено {item.in_transit:.0f} шт. в пути")
    if item.moq and recommended > raw_recommended > 0:
        reasons.append(f"Округлено до MOQ ({item.moq:.0f} шт.)")
    if not reasons:
        reasons.append("Спрос стабилен, остаток и товары в пути покрывают прогноз")

    return Recommendation(
        sku=item.sku,
        name=item.name,
        category=item.category,
        supplier=item.supplier,
        current_stock=item.current_stock,
        in_transit=item.in_transit,
        forecast_demand=round(forecast_demand, 1),
        recommended_quantity=round(recommended, 0),
        urgency=urgency,
        reasons=reasons,
        moq=item.moq,
        debug={
            "base_monthly_demand": round(base_monthly_demand, 1),
            "trend": trend,
            "seasonality": seasonality,
            "outliers_excluded": outlier_result["excluded"],
            "stockout_compensation": stockout_result["notes"],
            "daily_demand": round(daily_demand, 2),
            "lead_time_days": item.lead_time_days,
        },
    )


def calculate_batch(items: List[SkuInput], target_date: Optional[date] = None) -> List[Recommendation]:
    return [calculate_recommendation(item, target_date) for item in items]
