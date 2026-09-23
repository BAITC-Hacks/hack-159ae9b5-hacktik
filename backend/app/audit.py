"""
Simple in-memory/file-backed audit log for manual order adjustments.
Swap `_STORE` for a real table when moving to PostgreSQL — the interface
(`log_change` / `get_log`) stays the same.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import List, Dict

_STORE: List[Dict] = []


def log_change(user: str, sku: str, original_quantity: float, new_quantity: float, action: str = "adjust"):
    _STORE.append({
        "user": user,
        "time": datetime.now(timezone.utc).isoformat(),
        "sku": sku,
        "original_quantity": original_quantity,
        "new_quantity": new_quantity,
        "action": action,
    })


def get_log(sku: str | None = None) -> List[Dict]:
    if sku:
        return [e for e in _STORE if e["sku"] == sku]
    return list(_STORE)
