"""
AI Gateway — the ONLY place in the backend where the Anthropic API is called.

Security model (see spec sections 17-18):
  - The AI never receives a DB connection, SQL access, shell access, or write
    endpoints. It only receives the output of already-computed, deterministic
    Recommendation objects (procurement_engine.py) as plain data.
  - The AI cannot change the recommendation. It can only describe it, answer
    questions about it, or compare two already-computed scenarios.
  - The AI is exposed to the rest of the backend through exactly five
    functions below. Nothing else. If a new capability is needed, it must be
    added here explicitly and reviewed — the AI can never call arbitrary
    backend code.
  - Customer data reaching the AI is pre-anonymized upstream (see
    procurement_engine._detect_outliers — customer identity never enters the
    Recommendation object, only "large single customer" as a boolean/ratio).

Allowed functions (matches spec section 17):
  get_product_history(sku)
  get_recommendation(sku)
  explain_recommendation(sku)
  compare_scenarios(sku, scenario_a, scenario_b)
  suggest_adjustment(sku, manager_note)

Explicitly NOT exposed: execute_sql, modify_database, send_order,
approve_order, delete_data, execute_shell.
"""
from __future__ import annotations
from typing import Optional
import os
import httpx

from .procurement_engine import Recommendation, SkuInput

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-6"


class AIGatewayError(Exception):
    pass


def _call_claude(system: str, user_message: str, max_tokens: int = 500) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise AIGatewayError("ANTHROPIC_API_KEY not configured")
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user_message}],
    }
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(ANTHROPIC_API_URL, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    text_blocks = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
    return "\n".join(text_blocks).strip()


_SYSTEM_PROMPT = (
    "Ты — ассистент менеджера по закупкам в системе ИЭК. "
    "Ты объясняешь уже посчитанные системой рекомендации на понятном языке. "
    "Ты НЕ можешь менять расчёт, подтверждать или отправлять заказ. "
    "Отвечай кратко, по-русски, без технического жаргона. "
    "Используй только предоставленные данные — не придумывай цифры."
)


# ---------------------------------------------------------------------------
# The 5 allowed AI functions
# ---------------------------------------------------------------------------

def get_product_history(rec: Recommendation, item: SkuInput) -> dict:
    """Read-only: returns the sales history already computed for this SKU."""
    return {
        "sku": item.sku,
        "history": [{"month": m.month, "units": m.units_sold, "stockout_days": m.stockout_days}
                    for m in item.sales_history],
    }


def get_recommendation(rec: Recommendation) -> dict:
    """Read-only: returns the deterministic recommendation as computed."""
    return {
        "sku": rec.sku,
        "recommended_quantity": rec.recommended_quantity,
        "forecast_demand": rec.forecast_demand,
        "urgency": rec.urgency,
        "reasons": rec.reasons,
    }


def explain_recommendation(rec: Recommendation) -> str:
    """
    Turns the deterministic `debug` factors into a natural-language
    explanation. The AI only rephrases numbers it's given — it cannot alter them.
    """
    facts = (
        f"SKU {rec.sku} ({rec.name}), категория {rec.category}, поставщик {rec.supplier}.\n"
        f"Текущий остаток: {rec.current_stock}, в пути: {rec.in_transit}.\n"
        f"Прогноз спроса: {rec.forecast_demand}. Рекомендовано заказать: {rec.recommended_quantity}.\n"
        f"Срочность: {rec.urgency}.\n"
        f"Факторы расчёта: {rec.debug}.\n"
        f"Причины (уже сформулированы системой): {rec.reasons}."
    )
    try:
        return _call_claude(
            _SYSTEM_PROMPT,
            f"Объясни менеджеру простыми словами, почему предложено именно такое количество, "
            f"опираясь ТОЛЬКО на эти факты:\n{facts}",
        )
    except AIGatewayError:
        # Deterministic fallback if no API key configured — engine's own
        # `reasons` list is already a valid, if less fluent, explanation.
        return " ".join(rec.reasons)


def compare_scenarios(rec_a: Recommendation, rec_b: Recommendation, label_a: str, label_b: str) -> str:
    """Compares two already-computed recommendations for the same SKU (e.g. different periods/warehouses)."""
    facts = (
        f"Сценарий «{label_a}»: рекомендовано {rec_a.recommended_quantity}, "
        f"прогноз {rec_a.forecast_demand}, срочность {rec_a.urgency}, причины: {rec_a.reasons}.\n"
        f"Сценарий «{label_b}»: рекомендовано {rec_b.recommended_quantity}, "
        f"прогноз {rec_b.forecast_demand}, срочность {rec_b.urgency}, причины: {rec_b.reasons}."
    )
    try:
        return _call_claude(
            _SYSTEM_PROMPT,
            f"Сравни эти два уже рассчитанных сценария и объясни разницу простыми словами:\n{facts}",
        )
    except AIGatewayError:
        return (f"{label_a}: {rec_a.recommended_quantity} шт. vs "
                f"{label_b}: {rec_b.recommended_quantity} шт.")


def suggest_adjustment(rec: Recommendation, manager_note: str) -> str:
    """
    Manager asks a free-text question ("почему не учли акцию в марте?" etc).
    The AI may only *suggest* a rationale in reply — it has no way to actually
    change recommended_quantity; only the manager's own PATCH call does that.
    """
    facts = f"Текущая рекомендация: {rec.recommended_quantity} шт. Факторы: {rec.debug}."
    try:
        return _call_claude(
            _SYSTEM_PROMPT,
            f"Менеджер спрашивает: «{manager_note}»\n"
            f"Вот факты по расчёту:\n{facts}\n"
            f"Ответь по существу, основываясь только на этих данных. "
            f"Если нужно изменить количество — скажи менеджеру самому ввести новое значение в поле «Изменить», "
            f"ты не можешь менять его сам.",
        )
    except AIGatewayError:
        return ("Не могу подключиться к AI-сервису сейчас. "
                "Вы можете вручную изменить количество в поле «Изменить».")
