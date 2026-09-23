"""Explainable stock-cover suggestions over a read-only supplied workbook snapshot."""
from __future__ import annotations

import json
import math
import sqlite3
from contextlib import closing
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Query

SNAPSHOT_FILE = "Товар в пути_SystemElectric на 22.09.2026.xlsx"
MOQ_FILE = "MOQ SystemElectric.xlsx"
AS_OF = "2026-09-22"
SNAPSHOT_FIELDS = {
    "sku": "Артикул поставщика", "code": "Код 1с", "name": "Наименование",
    "stock": "Остаток", "available_stock": "Свободный остаток",
    "monthly_sales": "Ср мес за последние 12 мес", "in_transit": "СЭ в пути 24.09",
    "last_12_month_sales": "Сумма последние 12 мес", "reserved": "Зарезервировано",
    "existing_order": "Заказ",
}
METHOD = [
    "Ориентир запаса: выбранное число месяцев × сохранённые среднемесячные продажи из Excel. Это простая эвристика, не обученный и не проверенный прогноз спроса.",
    "Покрытие = (свободный остаток + товар в пути) / среднемесячные продажи. Общий остаток показан отдельно; резервы повторно не вычитаются.",
    "Пополнение = недостающее до ориентира количество, округлённое вверх до «Кратности» из MOQ. Кратность упаковки не подтверждает минимальную сумму заказа.",
    "Кандидат на избыток = превышение ориентира с учётом поставки; количество ограничено доступным сейчас остатком и округлено вниз до целой единицы.",
    "При пустых данных, неположительных продажах, неизвестной кратности или противоречивых значениях требуется проверка. Пустые ячейки не заменяются нулём.",
]
WARNINGS = [
    "Снимок на 22.09.2026, не текущие остатки. Сентябрь 2026 может быть неполным; использовано сохранённое среднее из исходного отчёта.",
    "Товар в пути взят из поля «СЭ в пути 24.09»; фактическое прибытие и доступность поставки нужно подтвердить.",
    "Количество указано в единицах исходного отчёта. Сроки поставки, сезонность, страховой запас и стоимость в расчёт не включены.",
    "Рекомендации не создают заказы или объявления автоматически. Цены, скидки и адреса из этих данных не определяются.",
]


def number(value):
    """Keep missing, invalid and signed values distinguishable from numeric zero."""
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        result = float(value)
    except (ValueError, TypeError):
        return None
    return result if math.isfinite(result) else None


def _decimal(value):
    return Decimal(str(value))


def recommend(record, target_months=3):
    """Return a new record; never mutate source values or invent missing quantities."""
    item = dict(record)
    for field in ("stock", "available_stock", "monthly_sales", "in_transit", "moq",
                  "last_12_month_sales", "reserved", "existing_order"):
        item[field] = number(record.get(field))
    item.update(kind="review", label="Проверить данные", suggested_quantity=None,
                coverage_months=None, target_stock=None, net_stock=None)
    demand, available, transit, moq = (item[k] for k in
                                      ("monthly_sales", "available_stock", "in_transit", "moq"))
    missing = [label for key, label in (("stock", "остаток"),
               ("available_stock", "свободный остаток"), ("monthly_sales", "средние продажи"),
               ("in_transit", "товар в пути")) if item[key] is None]
    if missing:
        item["reason"] = "Не указаны: " + ", ".join(missing) + ". Пустые ячейки не означают ноль."
        return item
    if demand <= 0:
        item["reason"] = ("Среднемесячные продажи отрицательные; проверьте возвраты и исходный отчёт."
                          if demand < 0 else "Среднемесячные продажи равны нулю. Спрос и необходимость перемещения нужно проверить вручную.")
        return item
    if any(item[k] < 0 for k in ("stock", "available_stock", "in_transit")) or available > item["stock"]:
        item["reason"] = "Остатки или поставка противоречивы: проверьте отрицательные значения и свободный остаток."
        return item
    net = _decimal(available) + _decimal(transit)
    target = _decimal(demand) * _decimal(target_months)
    item.update(net_stock=float(net), target_stock=float(target), coverage_months=float(net / _decimal(demand)))
    if moq is None or moq <= 0 or record.get("moq_conflict"):
        item["reason"] = "Кратность заказа не найдена или противоречива в MOQ; подтвердите её перед действием."
        return item
    gap = target - net
    if gap > 0:
        quantity = (gap / _decimal(moq)).to_integral_value(rounding=ROUND_CEILING) * _decimal(moq)
        item.update(kind="reorder", label="Пополнить запас", suggested_quantity=float(quantity),
                    reason=f"Покрытие {item['coverage_months']:.2f} мес. ниже ориентира {target_months:g} мес. Пополнение округлено вверх до кратности {moq:g}.")
    elif gap < 0:
        quantity = min(-gap, _decimal(available)).to_integral_value(rounding=ROUND_FLOOR)
        if quantity >= 1:
            item.update(kind="surplus", label="Кандидат на избыток", suggested_quantity=float(quantity),
                        reason=f"Покрытие {item['coverage_months']:.2f} мес. выше ориентира {target_months:g} мес. Можно проверить перемещение или скидку на доступный избыток.")
            if transit > 0:
                item["reason"] += " Расчёт зависит от ожидаемой поставки: перед перемещением подтвердите её прибытие."
        else:
            item["reason"] = "Превышение ориентира меньше одной доступной единицы; немедленное перемещение не рассчитано."
    else:
        item.update(label="В пределах ориентира", suggested_quantity=0,
                    reason="Свободный остаток и поставка точно покрывают выбранный ориентир; пополнение не требуется.")
    return item


def _headers(row):
    return {" ".join(str(value).split()): column for column, value in row.items()}


def _identity(value):
    return str(value or "").strip().casefold()


def _source(row):
    return {"file": row["source"], "sheet": row["sheet"], "row": row["row_number"]}


def _read_records(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT * FROM raw_rows WHERE source=? AND sheet=? ORDER BY row_number",
                          (SNAPSHOT_FILE, "TDSheet")).fetchall()
        header = next((json.loads(r["values_json"]) for r in rows if r["row_number"] == 2), {})
        columns = _headers(header)
        if not all(label in columns for label in SNAPSHOT_FIELDS.values()):
            raise ValueError("Структура листа остатков не соответствует известным заголовкам.")
        moq_rows = db.execute("SELECT * FROM raw_rows WHERE source=? ORDER BY sheet,row_number", (MOQ_FILE,)).fetchall()
    by_code, by_sku, moq_columns = {}, {}, {}
    for row in moq_rows:
        values = json.loads(row["values_json"])
        if row["row_number"] == 1:
            moq_columns[row["sheet"]] = _headers(values)
            continue
        cols = moq_columns.get(row["sheet"], {})
        if not all(k in cols for k in ("Артикул", "Номенклатура.Код", "Кратность")):
            continue
        value = {"moq": number(values.get(cols["Кратность"])), "source": _source(row)}
        for index, field in ((by_code, "Номенклатура.Код"), (by_sku, "Артикул")):
            key = _identity(values.get(cols[field]))
            if key:
                index.setdefault(key, []).append(value)
    result = []
    for row in rows:
        if row["row_number"] <= 2:
            continue
        values = json.loads(row["values_json"])
        item = {field: values.get(columns[label]) for field, label in SNAPSHOT_FIELDS.items()}
        if not item["sku"] or not item["name"]:
            continue
        matches = by_code.get(_identity(item["code"]), []) + by_sku.get(_identity(item["sku"]), [])
        quantities = {match["moq"] for match in matches}
        item["moq"] = next(iter(quantities)) if len(quantities) == 1 else None
        item["moq_conflict"] = len(quantities) > 1
        item["moq_source"] = matches[0]["source"] if matches else None
        item["source"] = _source(row)
        result.append(item)
    return result


def recommendations(path, target_months=3, q="", kind="all", limit=100):
    response = {"as_of": None, "target_months": target_months,
                "summary": {"total": 0, "surplus": 0, "reorder": 0, "review": 0},
                "items": [], "returned": 0, "filtered_total": 0,
                "units": "ед. исходного отчёта", "methodology": METHOD[:], "warnings": WARNINGS[:]}
    if not Path(path).is_file():
        response["warnings"] = ["Предоставленная база исходных таблиц недоступна. Рекомендации не рассчитаны."]
        return response
    try:
        records = _read_records(path)
    except (sqlite3.Error, ValueError, KeyError, TypeError):
        response["warnings"] = ["Не удалось прочитать ожидаемые таблицы остатков. Проверьте импорт исходных файлов."]
        return response
    response["as_of"] = AS_OF
    items = [recommend(record, target_months) for record in records]
    response["summary"]["total"] = len(items)
    for item in items:
        response["summary"][item["kind"]] += 1
    terms = q.casefold().split()
    items = [item for item in items if (kind == "all" or item["kind"] == kind)
             and all(term in f"{item['sku']} {item['code']} {item['name']}".casefold() for term in terms)]
    def sort_key(item):
        priority = {"surplus": 0, "reorder": 1, "review": 2}[item["kind"]]
        score = (-(item["suggested_quantity"] or 0) if item["kind"] == "surplus"
                 else item["coverage_months"] if item["coverage_months"] is not None else math.inf)
        return priority, score, str(item["sku"])
    items.sort(key=sort_key)
    response.update(filtered_total=len(items), items=items[:limit], returned=min(len(items), limit))
    return response


def mount_recommendations(app: FastAPI, path=None):
    source_path = Path(path) if path is not None else Path(__file__).parent.parent / "data" / "sources.sqlite3"

    @app.get("/api/recommendations")
    def get_recommendations(target_months: float = Query(default=3, ge=0.1, le=24, allow_inf_nan=False),
                            q: str = Query(default="", max_length=200),
                            kind: Literal["all", "surplus", "reorder", "review"] = "all",
                            limit: int = Query(default=100, ge=1, le=500)):
        return recommendations(source_path, target_months, q, kind, limit)
