"""Deterministic procurement calculations; language models never set quantities."""
from __future__ import annotations

import calendar
import json
import math
import statistics
from collections import defaultdict
from datetime import date, timedelta

from .procurement_db import fingerprint, number
from .procurement_models import Scenario


def mean(values):
    return statistics.mean(values) if values else 0.0


def month_range(start, end):
    d = date(start.year, start.month, 1)
    while d < end:
        yield d
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def quantity_step(unit):
    return .001 if str(unit).casefold() in {"м", "м2", "м²", "м3", "м³", "кг", "л", "т"} else 1.0


def rounded_order(shortage, moq, rule, unit="шт"):
    if shortage <= 1e-8:
        return 0.0
    step = quantity_step(unit)
    moq = moq or step
    if rule == "multiple":
        return round(math.ceil((shortage - 1e-8) / moq) * moq, 6)
    return round(max(math.ceil(shortage / step - 1e-8) * step, moq), 6)


def history_profile(sales, monthly, stockouts, as_of, category_seasons=None):
    """Normalize full months, remove isolated customer/day spikes, impute lost demand."""
    cutoff = date(as_of.year, as_of.month, 1)
    sales = [s for s in sales if date.fromisoformat(s["date"]) < cutoff]
    monthly = [m for m in monthly if date.fromisoformat(m["month"] + "-01") < cutoff]
    if not sales and not monthly:
        return None
    grouped = defaultdict(float)
    for s in sales:
        grouped[(s["date"], s["customer_hash"])] += s["quantity"]
    values = [v for v in grouped.values() if v > 0]
    threshold = math.inf
    if len(values) >= 8:
        median = statistics.median(values)
        mad = statistics.median(abs(v - median) for v in values)
        threshold = max(median * 5, median + 6 * 1.4826 * mad)
    large_counts = defaultdict(int)
    for (_, customer), qty in grouped.items():
        if qty > threshold:
            large_counts[customer] += 1
    excluded = defaultdict(float)
    total = defaultdict(float)
    for (day, customer), qty in grouped.items():
        total[day[:7]] += qty
        if qty > threshold and large_counts[customer] <= 2:
            excluded[day[:7]] += qty
    month_data = {m["month"]: m for m in monthly}
    first_month = min([*total, *month_data])
    last_month = date.fromisoformat(max([*total, *month_data]) + "-01")
    after_last = date(last_month.year + (last_month.month == 12), last_month.month % 12 + 1, 1)
    months = list(month_range(date.fromisoformat(first_month + "-01"), min(cutoff, after_last)))[-48:]
    outages = [(date.fromisoformat(s["start_date"]), date.fromisoformat(s["end_date"])) for s in stockouts]
    series, warnings = [], []
    if after_last < cutoff:
        warnings.append("История заканчивается раньше даты расчёта; прогноз продолжает последний известный спрос")
    if not stockouts and not any(m.get("available_days") not in (None, "") for m in monthly):
        warnings.append("Нет ежедневной доступности: нулевые продажи сами по себе не считаются stockout")
    if len(values) < 8 and sales:
        warnings.append("Мало клиентских операций для надёжного поиска разовых заказов")
    for month in months:
        key = month.strftime("%Y-%m")
        days = calendar.monthrange(month.year, month.month)[1]
        m = month_data.get(key)
        if monthly and m is None and key not in total:
            warnings.append("Внутри истории есть пропущенные месяцы; в сценарии их продажи приняты равными нулю")
        raw = m["quantity"] if m else total.get(key, 0)
        # Monthly totals are authoritative; transaction detail contributes anomaly deductions.
        removed = min(raw, excluded.get(key, 0))
        if m and key in total and abs(total[key] - raw) > max(1, raw * .05):
            warnings.append("Месячные итоги расходятся с детализацией; использован месячный итог")
        absent = sum(any(a <= month + timedelta(days=d) <= b for a, b in outages) for d in range(days))
        available = number(m.get("available_days")) if m else None
        if available is None:
            available = days - absent
        else:
            available = min(available, days - absent)
        regular = max(0, raw - removed)
        series.append({"month": key, "raw": raw, "regular": regular, "excluded": removed,
                       "days": days, "available_days": available,
                       "rate": regular / available if available > 0 else None})
    # Without transactions only monthly spikes can be detected, never attributed to a customer.
    if not sales and len(series) >= 8:
        rates = [x["rate"] for x in series if x["rate"] is not None]
        median = statistics.median(rates) if rates else 0
        mad = statistics.median(abs(v - median) for v in rates) if rates else 0
        cap = max(median * 5, median + 6 * 1.4826 * mad)
        for x in series:
            if cap > 0 and x["rate"] is not None and x["rate"] > cap:
                # Preserve recurring calendar peaks across years.
                peers = [y["rate"] for y in series if y is not x and y["month"][5:] == x["month"][5:] and y["rate"] is not None]
                if peers and max(peers) > cap:
                    continue
                revised = median * x["available_days"]
                x["excluded"] += x["regular"] - revised
                x["regular"] = revised
                x["rate"] = median
        warnings.append("Без клиентской детализации выявляются только месячные выбросы")
    for x in series:
        if x["rate"] is None:
            peers = [y["rate"] for y in series if y["rate"] is not None and y["month"][5:] == x["month"][5:]]
            if not peers:
                peers = [y["rate"] for y in series[-12:] if y["rate"] is not None]
            x["rate"] = statistics.median(peers) if peers else 0
        x["corrected"] = x["rate"] * x["days"]
        x["lost"] = max(0, x["corrected"] - x["regular"])
    seasons = [1.0] * 12
    if category_seasons:
        scale = mean(category_seasons)
        seasons = [v / scale for v in category_seasons]
    elif len(series) >= 24:
        # A centered 12-month baseline separates seasonality from long-run growth.
        ratios = defaultdict(list)
        for i, x in enumerate(series):
            lo, hi = max(0, i - 6), min(len(series), i + 6)
            baseline = mean([s["rate"] for s in series[lo:hi]])
            if baseline:
                ratios[int(x["month"][5:]) - 1].append(x["rate"] / baseline)
        seasons = [max(.15, min(4, statistics.median(ratios[m]))) if ratios[m] else 1 for m in range(12)]
        scale = mean(seasons)
        seasons = [v / scale for v in seasons]
    else:
        warnings.append("Для собственной сезонности нужны 24 полных месяца; можно загрузить профиль категории")
    deseasoned = [x["rate"] / seasons[int(x["month"][5:]) - 1] for x in series]
    base = mean(deseasoned[-3:])
    previous = mean(deseasoned[-6:-3])
    trend = max(.75, min(1.25, base / previous)) if len(series) >= 6 and previous > 0 else 1
    return {"series": series, "base_daily": base, "trend": trend, "seasons": seasons,
            "lost": sum(x["lost"] for x in series[-12:]),
            "excluded": sum(x["excluded"] for x in series[-12:]), "warnings": list(dict.fromkeys(warnings))}


def calculate_product(p, scenario, sources, supplier=None, category=None):
    category = category or {}
    warnings = []
    stock = number(p.get("stock_qty"), 0)
    reserved = number(p.get("reserved_qty"))
    free = max(0, stock - (reserved or 0)) if p.get("stock_basis") != "free_stock_snapshot" else stock
    if reserved is None:
        warnings.append("Резервы неизвестны; в сценарии принят нулевой резерв")
    if p.get("stock_date") and (scenario.as_of - date.fromisoformat(p["stock_date"])).days > 7:
        warnings.append("Остаток устарел: " + p["stock_date"])
    if p.get("stock_basis") == "monthly_balance_proxy":
        warnings.append("Остаток восстановлен из месячного баланса")
    lead = supplier["lead_days"] if supplier else scenario.fallback_lead_days
    if not supplier:
        warnings.append(f"Поставщик и срок не заданы; в сценарии срок {lead} дн.")
    category_safety = number(category.get("safety_days"), 0)
    horizon = scenario.review_days + lead + scenario.safety_days + category_safety
    growth = (1 + scenario.growth_pct / 100) * (1 + number(category.get("growth_pct"), 0) / 100)
    seasons = json.loads(category.get("seasonality_json") or "[]")
    history = history_profile(sources.get("sales", []), sources.get("monthly_sales", []),
                              sources.get("stockouts", []), scenario.as_of, seasons)
    mode = "history" if history else "imported"
    forecast30 = number(p.get("forecast_30_days"))
    forecast90 = number(p.get("forecast_90_days"))
    if history:
        warnings.extend(history["warnings"])
        def demand(days):
            return growth * sum(history["base_daily"] * history["seasons"][(scenario.as_of + timedelta(days=i)).month - 1]
                                * history["trend"] ** (i / 90) for i in range(math.ceil(days)))
        excluded, lost, trend = history["excluded"], history["lost"], history["trend"]
    else:
        warnings.append("Использован готовый прогноз CSV; исходная история не загружена")
        if forecast30 is None:
            warnings.append("Нет истории и готового прогноза: количество рассчитать нельзя")
        def demand(days):
            if forecast30 is None:
                return 0
            # Existing source forecasts already include growth, seasonality and exclusions.
            if seasons and number(p.get("monthly_forecast_deseasonalized")) is not None:
                rate = number(p["monthly_forecast_deseasonalized"]) / 30
                return growth * sum(rate * seasons[(scenario.as_of + timedelta(days=i)).month - 1] for i in range(math.ceil(days)))
            first = forecast30 * min(days, 30) / 30
            tail = max(0, days - 30) * ((max(0, forecast90 - forecast30) / 60) if forecast90 is not None else forecast30 / 30)
            return (first + tail) * growth
        excluded = number(p.get("excluded_outlier_12m"), 0)
        lost = None
        trend = number(p.get("trend_factor"), 1)
    forecast = demand(horizon)
    inbound_rows = sources.get("inbound", [])
    if inbound_rows:
        incoming = sum(r["quantity"] for r in inbound_rows if scenario.as_of <= date.fromisoformat(r["eta"]) <= scenario.as_of + timedelta(days=horizon))
        overdue = sum(r["quantity"] for r in inbound_rows if date.fromisoformat(r["eta"]) < scenario.as_of)
        if overdue:
            warnings.append("Есть просроченные поставки; до подтверждения они не уменьшают заказ")
    else:
        incoming = number(p.get("inbound_30_days"), 0) if horizon >= 30 else 0
        if number(p.get("inbound_total_raw"), 0) > 0 or incoming:
            warnings.append("Нет дат поступлений: учтены только переданные поступления в 30 дней")
    shortage = max(0, forecast - free - incoming)
    moq = number(p.get("moq"))
    rule = p.get("moq_rule") or "minimum"
    step = quantity_step(p.get("unit", "шт"))
    if moq is None or not p.get("moq_rule"):
        warnings.append(f"Условия MOQ не полные; недостающая минимальная партия принята равной {step:g}")
    qty = rounded_order(shortage, moq, rule, p.get("unit", "шт"))
    forecast_available = history is not None or forecast30 is not None
    surplus = round(max(0, math.floor((free - demand(max(90, horizon))) / step + 1e-8) * step), 3) if forecast_available else 0
    daily = demand(30) / 30
    cover = free / daily if daily > 0 else None
    urgency = "critical" if daily > 0 and (free <= 0 or (cover is not None and cover < lead)) else "high" if qty > 0 and cover is not None and cover < 30 else "normal"
    unit_cost = number(p.get("unit_cost"))
    source_flags = json.loads(p.get("quality_flags_json") or "[]")
    explanation = (f"Спрос на {horizon:g} дн.: {forecast:.2f}; свободный остаток: {free:g}; "
                   f"поступления в срок: {incoming:g}. Нехватка: {shortage:.2f}. "
                   f"Заказ с учётом MOQ {moq or step:g} ({'кратность' if rule == 'multiple' else 'минимум'}): {qty:g} {p.get('unit') or 'ед.'}.")
    if not forecast_available:
        explanation = "Для расчёта загрузите историю продаж или готовый прогноз."
    result = {
        "product_id": p["product_id"], "warehouse": p["warehouse"], "name": p["name"],
        "sku": p.get("supplier_sku") or p.get("code_1c") or p["product_id"], "code_1c": p.get("code_1c", ""),
        "brand": p.get("brand", ""), "category": p.get("category", "Без категории"), "unit": p.get("unit") or "ед.",
        "supplier_id": p.get("supplier_id") or "", "supplier": supplier["name"] if supplier else "Поставщик не назначен",
        "stock": stock, "reserved": reserved, "free_stock": free, "stock_date": p.get("stock_date"),
        "forecast": round(forecast, 3), "forecast_30": round(demand(30), 3), "forecast_90": round(demand(90), 3),
        "inbound": incoming, "shortage": round(shortage, 3), "quantity": qty, "surplus": surplus,
        "classification": "unknown" if not forecast_available else "deficit" if qty else "surplus" if surplus else "balanced",
        "source_classification": p.get("classification"), "source_quantity": number(p.get("quantity")),
        "urgency": urgency, "cover_days": round(cover, 1) if cover is not None else None,
        "horizon": horizon, "lead_days": lead, "growth_pct": round((growth - 1) * 100, 3),
        "moq": moq or step, "moq_rule": rule, "unit_cost": unit_cost,
        "budget": round(unit_cost * qty, 2) if unit_cost is not None else None,
        "mode": mode, "trend": trend, "excluded_outlier": excluded, "estimated_lost_demand": lost,
        "explanation": explanation, "warnings": list(dict.fromkeys(warnings)), "source_flags": source_flags,
        "source_explanation": p.get("explanation", ""), "source_date": p.get("calculation_date"),
        "history": history["series"] if history else [],
        "forecast_curve": [{"days": d, "quantity": round(demand(d), 2)} for d in (30,60,90)],
    }
    return result


def calculate(snapshot, scenario: Scenario):
    indexes = {}
    for kind in ("sales", "monthly_sales", "stockouts", "inbound"):
        mapping = defaultdict(list)
        for r in snapshot["sources"].get(kind, []):
            mapping[(r["product_id"], r["warehouse"])].append(r)
        indexes[kind] = mapping
    categories = {r["category"]: r for r in snapshot["sources"].get("categories", [])}
    results = []
    for p in snapshot["products"]:
        if scenario.warehouse and p["warehouse"] != scenario.warehouse:
            continue
        if scenario.category and p.get("category") != scenario.category:
            continue
        if scenario.brand and p.get("brand") != scenario.brand:
            continue
        key = (p["product_id"], p["warehouse"])
        r = calculate_product(p, scenario, {k: v[key] for k, v in indexes.items()},
                              snapshot["suppliers"].get(p.get("supplier_id")), categories.get(p.get("category")))
        r["fingerprint"] = fingerprint({"product": p, "sources": {k: v[key] for k, v in indexes.items()},
                                        "supplier": snapshot["suppliers"].get(p.get("supplier_id")),
                                        "category": categories.get(p.get("category")), "scenario": scenario.model_dump()})
        results.append(r)
    rank = {"critical": 0, "high": 1, "normal": 2}
    results.sort(key=lambda r: (rank[r["urgency"]], r["cover_days"] if r["cover_days"] is not None else 1e20, -r["forecast_30"], r["product_id"]))
    return results
