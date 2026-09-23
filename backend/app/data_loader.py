"""
Data layer — loads warehouse/sales/stock/MOQ data and normalizes it into
`SkuInput` objects that the procurement engine understands.

IMPORTANT: This currently ships with a SAMPLE dataset generator so the app
runs end-to-end out of the box. It is a placeholder for the real ИЭК Excel
files, which were not part of this upload.

To wire in the real files:
  1. Drop the ИЭК Excel exports into backend/data/raw/.
  2. Fill in COLUMN_MAP below once you've inspected the real sheets (sheet
     names + column headers differ file to file per the spec — that's what
     COLUMN_MAP / the normalization functions are for).
  3. Replace `load_sample_dataset()` calls with `load_from_excel()`.
  4. Nothing in procurement_engine.py or the API needs to change — they only
     depend on the SkuInput shape, not on where it came from.

Later, swapping Excel for PostgreSQL means replacing the body of
`load_from_excel()` with SQL queries that build the same SkuInput objects —
the rest of the system is unaffected. That's the point of this seam.
"""
from __future__ import annotations
from pathlib import Path
from datetime import date
from typing import List, Dict
import random

from .procurement_engine import SkuInput, MonthlySales

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

# Fill this in once the real Excel files are available. Left empty/placeholder
# on purpose — per the spec, column structure must come from inspecting the
# actual files, not be guessed.
COLUMN_MAP: Dict[str, Dict[str, str]] = {
    # "sales.xlsx": {"sku_col": "Артикул", "month_col": "Период", "qty_col": "Кол-во"},
    # "stock.xlsx": {"sku_col": "...", "stock_col": "...", "in_transit_col": "..."},
    # "moq.xlsx": {"sku_col": "...", "moq_col": "..."},
}


def load_from_excel() -> List[SkuInput]:
    """
    Real loader. Requires pandas/openpyxl and the actual ИЭК files under
    data/raw/. Left as a stub with the intended shape until those files and
    their real column names are available (see module docstring).
    """
    if not RAW_DIR.exists() or not any(RAW_DIR.glob("*.xlsx")):
        raise FileNotFoundError(
            "No Excel files found in backend/data/raw/. "
            "Upload the ИЭК files and fill in COLUMN_MAP in data_loader.py, "
            "or call load_sample_dataset() for the prototype dataset."
        )
    import pandas as pd  # local import: optional dependency until real files exist

    # --- sketch of the normalization steps once real files are supplied ---
    # sales_df = pd.read_excel(RAW_DIR / "sales.xlsx")
    # stock_df = pd.read_excel(RAW_DIR / "stock.xlsx")
    # moq_df = pd.read_excel(RAW_DIR / "moq.xlsx")
    # ... rename columns via COLUMN_MAP, merge on SKU, group sales by month ...
    raise NotImplementedError(
        "Fill in the merge/rename logic once real column names are confirmed "
        "from the ИЭК files (see COLUMN_MAP)."
    )


# ---------------------------------------------------------------------------
# Sample dataset — realistic-shaped data so the whole pipeline is runnable now
# ---------------------------------------------------------------------------

_CATEGORIES = ["Кабельная продукция", "Автоматические выключатели", "Светотехника", "Крепёж"]
_SUPPLIERS = ["ООО «Поставщик 1»", "ООО «Поставщик 2»", "ТОО «СветКомплект»"]
_WAREHOUSES = ["Основной склад", "Склад Алматы"]


def _months_back(n: int, today: date) -> List[str]:
    out = []
    y, m = today.year, today.month
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return list(reversed(out))


def load_sample_dataset(n_skus: int = 60, seed: int = 42) -> List[SkuInput]:
    rnd = random.Random(seed)
    today = date.today()
    months = _months_back(24, today)
    items = []

    for i in range(n_skus):
        sku = f"{10000 + i}"
        category = rnd.choice(_CATEGORIES)
        supplier = rnd.choice(_SUPPLIERS)
        base = rnd.uniform(15, 120)
        trend_rate = rnd.choice([0.0, 0.0, 0.0, 0.015, -0.01, 0.03])  # most flat, some trending
        seasonal_month = rnd.choice([None, None, 3, 11, 6])  # some seasonal peak month

        history = []
        for idx, mo in enumerate(months):
            month_num = int(mo.split("-")[1])
            val = base * (1 + trend_rate) ** idx
            if seasonal_month and month_num == seasonal_month:
                val *= rnd.uniform(1.6, 2.2)
            val *= rnd.uniform(0.85, 1.15)  # noise

            stockout_days = 0
            if rnd.random() < 0.08:
                stockout_days = rnd.randint(5, 18)
                val *= (30 - stockout_days) / 30  # observed sales suppressed

            is_spike = rnd.random() < 0.05
            if is_spike:
                val *= rnd.uniform(4, 8)

            large_customer_units = 0.0
            if rnd.random() < 0.07:
                large_customer_units = val * rnd.uniform(0.6, 0.9)

            history.append(MonthlySales(
                month=mo, units_sold=round(val, 1), stockout_days=stockout_days,
                days_in_month=30, large_single_customer_units=round(large_customer_units, 1),
                is_single_large_order=is_spike,
            ))

        current_stock = round(rnd.uniform(0, base * 2), 0)
        in_transit = round(rnd.choice([0, 0, 0, base * 0.5]), 0)
        moq = rnd.choice([None, 10, 20, 50])

        items.append(SkuInput(
            sku=sku,
            name=f"Товар {sku} ({category})",
            category=category,
            supplier=supplier,
            current_stock=current_stock,
            in_transit=in_transit,
            moq=moq,
            sales_history=history,
            lead_time_days=rnd.choice([14, 21, 30]),
        ))

    return items


def filter_dataset(items: List[SkuInput], warehouse: str | None, category: str | None) -> List[SkuInput]:
    # Sample dataset doesn't carry warehouse per-SKU yet; category filter is real.
    out = items
    if category:
        out = [i for i in out if i.category == category]
    return out


def list_categories(items: List[SkuInput]) -> List[str]:
    return sorted({i.category for i in items})


def list_suppliers(items: List[SkuInput]) -> List[str]:
    return sorted({i.supplier for i in items})


def list_warehouses() -> List[str]:
    return _WAREHOUSES
