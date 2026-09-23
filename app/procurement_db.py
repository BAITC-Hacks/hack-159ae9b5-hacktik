from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def number(value, default=None):
    if value in (None, ""):
        return default
    result = float(str(value).replace("\u00a0", "").replace(" ", "").replace(",", "."))
    if not math.isfinite(result):
        raise ValueError("Число должно быть конечным")
    return result


def stable_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, allow_nan=False)


IMPORT_COLUMNS = {
    "sales": {"transaction_id", "product_id", "warehouse", "date", "quantity", "customer_hash", "price"},
    "monthly_sales": {"product_id", "warehouse", "month", "quantity", "available_days"},
    "stockouts": {"product_id", "warehouse", "start_date", "end_date"},
    "inbound": {"product_id", "warehouse", "eta", "quantity", "reference"},
    "suppliers": {"supplier_id", "name", "lead_days", "minimum_order_value"},
    "assignments": {"product_id", "warehouse", "supplier_id"},
    "categories": {"category", "growth_pct", "safety_days", "seasonality_json"},
}
REQUIRED = {
    "sales": {"transaction_id", "product_id", "warehouse", "date", "quantity", "customer_hash"},
    "monthly_sales": {"product_id", "warehouse", "month", "quantity"},
    "stockouts": {"product_id", "warehouse", "start_date", "end_date"},
    "inbound": {"product_id", "warehouse", "eta", "quantity", "reference"},
    "suppliers": {"supplier_id", "name", "lead_days"},
    "assignments": {"product_id", "warehouse", "supplier_id"},
    "categories": {"category", "growth_pct", "safety_days"},
    "snapshot": {"product_id", "name", "stock_qty", "stock_date"},
}


class Database:
    def __init__(self, path: str | Path | None = None, seed=True):
        self.path = Path(path or ROOT / "data" / "ekt.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS products (
                    product_id TEXT NOT NULL, warehouse TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(product_id, warehouse));
                CREATE TABLE IF NOT EXISTS sources (
                    kind TEXT PRIMARY KEY, payload TEXT NOT NULL, imported_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS suppliers (supplier_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY, status TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY, at TEXT NOT NULL, action TEXT NOT NULL, payload TEXT NOT NULL);
            """)
            count = c.execute("SELECT count(*) FROM products").fetchone()[0]
        if seed and count == 0:
            for name in ("have.csv", "need.csv"):
                self.import_csv("snapshot", (ROOT / "data" / name).read_text(encoding="utf-8-sig"))

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def audit(self, c, action, payload):
        c.execute("INSERT INTO audit(at, action, payload) VALUES(?,?,?)", (now(), action, stable_json(payload)))

    def snapshot(self, connection=None):
        if connection is None:
            with self.connect() as c:
                c.execute("BEGIN")
                return self.snapshot(c)
        c = connection
        return {
            "products": [json.loads(r[0]) for r in c.execute("SELECT payload FROM products ORDER BY product_id, warehouse")],
            "suppliers": {r[0]: json.loads(r[1]) for r in c.execute("SELECT supplier_id,payload FROM suppliers")},
            "sources": {r[0]: json.loads(r[1]) for r in c.execute("SELECT kind,payload FROM sources")},
        }

    def import_csv(self, kind, text, replace=False):
        text = text.lstrip("\ufeff")
        try:
            dialect = csv.Sniffer().sniff(text[:5000], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        fields = set(reader.fieldnames or [])
        if not REQUIRED[kind].issubset(fields):
            raise ValueError("Не хватает столбцов: " + ", ".join(sorted(REQUIRED[kind] - fields)))
        if kind != "snapshot" and fields - IMPORT_COLUMNS[kind]:
            raise ValueError("Неизвестные столбцы: " + ", ".join(sorted(fields - IMPORT_COLUMNS[kind])))
        if any(re.search(r"(?i)email|phone|имя.?клиента|customer_name|телефон", f) for f in fields):
            raise ValueError("Удалите персональные данные клиентов до загрузки")
        rows = []
        for index, raw in enumerate(reader, 2):
            if len(rows) >= 100_000:
                raise ValueError("Лимит одной загрузки — 100 000 строк")
            if None in raw or any(v is None for v in raw.values()):
                raise ValueError(f"Строка {index}: число полей отличается от заголовка")
            row = {k: v.strip() for k, v in raw.items()}
            try:
                rows.append(self.validate_row(kind, row))
            except (ValueError, KeyError, TypeError) as e:
                raise ValueError(f"Строка {index}: {e}") from e
        if not rows:
            raise ValueError("CSV не содержит строк")
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            existing = self.snapshot(c)
            keys = {(p["product_id"], p["warehouse"]) for p in existing["products"]}
            for row in rows:
                if kind not in {"snapshot", "categories", "suppliers"} and (row["product_id"], row["warehouse"]) not in keys:
                    raise ValueError(f"Товар/склад не найден: {row['product_id']} / {row['warehouse']}. Сначала загрузите остатки для этого склада.")
                if kind == "assignments" and row["supplier_id"] not in existing["suppliers"]:
                    raise ValueError("Сначала добавьте поставщика " + row["supplier_id"])
            if kind == "snapshot":
                if replace:
                    raise ValueError("Остатки обновляются по паре товар + склад. Полное удаление здесь отключено.")
                seen = set()
                for row in rows:
                    key = (row["product_id"], row["warehouse"])
                    if key in seen:
                        raise ValueError("Дублируется товар/склад: " + str(key))
                    seen.add(key)
                    old = c.execute("SELECT payload FROM products WHERE product_id=? AND warehouse=?", key).fetchone()
                    if old:
                        previous = json.loads(old[0]); previous.update(row); row = previous
                    row.setdefault("category", "Без категории")
                    c.execute("INSERT OR REPLACE INTO products VALUES(?,?,?)", (*key, stable_json(row)))
            elif kind == "suppliers":
                for row in rows:
                    c.execute("INSERT OR REPLACE INTO suppliers VALUES(?,?)", (row["supplier_id"], stable_json(row)))
            elif kind == "assignments":
                for row in rows:
                    self._edit(c, row["product_id"], row["warehouse"], {"supplier_id": row["supplier_id"]})
            else:
                # Snapshot imports upsert. Event imports are complete source snapshots by default;
                # exact duplicate rows never accumulate on repeated imports.
                previous = [] if replace else existing["sources"].get(kind, [])
                merged = {stable_json(r): r for r in previous + rows}
                if kind in {"sales", "monthly_sales", "categories", "inbound"}:
                    keys_for = {"monthly_sales": ("product_id", "warehouse", "month"),
                                "categories": ("category",), "inbound": ("product_id", "warehouse", "reference"),
                                "sales": ("product_id", "warehouse", "transaction_id")}[kind]
                    merged = {tuple(r[k] for k in keys_for): r for r in previous + rows}
                c.execute("INSERT OR REPLACE INTO sources VALUES(?,?,?)", (kind, stable_json(list(merged.values())), now()))
            self.audit(c, "import", {"kind": kind, "rows": len(rows), "replace": replace})
        return {"kind": kind, "rows": len(rows), "message": "Данные загружены"}

    @staticmethod
    def validate_row(kind, row):
        for field in REQUIRED[kind]:
            if not row.get(field):
                raise ValueError("Пустое поле " + field)
        if "product_id" in row:
            row["warehouse"] = row.get("warehouse") or "unspecified"
        for k in ("date", "stock_date", "start_date", "end_date", "eta"):
            if row.get(k):
                date.fromisoformat(row[k])
        if kind == "snapshot":
            for k in ("stock_qty", "reserved_qty", "moq", "quantity", "forecast_30_days", "forecast_90_days", "inbound_30_days", "inbound_total_raw", "unit_cost", "net_sales_12m", "trend_factor", "excluded_outlier_12m"):
                if row.get(k) not in (None, ""):
                    row[k] = number(row[k])
                    if row[k] < 0 and k != "net_sales_12m":
                        raise ValueError(k + " не может быть отрицательным")
            if row.get("moq") == 0:
                raise ValueError("MOQ должен быть положительным")
            if row.get("moq_rule") not in (None, "", "minimum", "multiple"):
                raise ValueError("moq_rule: minimum или multiple")
            for k in ("quality_flags_json", "source_refs_json"):
                if row.get(k):
                    if not isinstance(json.loads(row[k]), list):
                        raise ValueError(k + " должен содержать JSON-массив")
            if "category" in row or "category_source" in row:
                row["category"] = row.get("category") or row.get("category_source") or "Без категории"
        elif kind == "suppliers":
            from .procurement_models import SupplierInput
            return SupplierInput(**{k: v for k, v in row.items() if v != ""}).model_dump()
        elif kind in {"sales", "monthly_sales", "inbound"}:
            row["quantity"] = number(row["quantity"])
            if row["quantity"] < 0:
                raise ValueError("quantity не может быть отрицательным; возвраты сверяются до импорта")
            if kind == "sales":
                if not re.fullmatch(r"[0-9a-fA-F]{64}", row["customer_hash"]):
                    raise ValueError("customer_hash должен быть SHA-256 от обезличенного ID (64 hex-символа)")
                if row.get("price"):
                    row["price"] = number(row["price"])
                    if row["price"] < 0:
                        raise ValueError("price не может быть отрицательным")
            if kind == "monthly_sales":
                import calendar
                month = date.fromisoformat(row["month"] + "-01")
                if row.get("available_days"):
                    row["available_days"] = number(row["available_days"])
                    if not 0 <= row["available_days"] <= calendar.monthrange(month.year, month.month)[1]:
                        raise ValueError("available_days вне диапазона месяца")
        elif kind == "stockouts" and row["end_date"] < row["start_date"]:
            raise ValueError("Дата окончания раньше начала")
        elif kind == "categories":
            row["growth_pct"] = number(row["growth_pct"])
            row["safety_days"] = number(row["safety_days"])
            if not -80 <= row["growth_pct"] <= 200 or not 0 <= row["safety_days"] <= 90:
                raise ValueError("Рост: от −80 до 200%; страховой запас: от 0 до 90 дней")
            factors = json.loads(row.get("seasonality_json") or "[]")
            if factors and (len(factors) != 12 or any(not isinstance(v,(int,float)) or not math.isfinite(v) or not 0.1 <= v <= 5 for v in factors)):
                raise ValueError("Сезонность: JSON-массив из 12 коэффициентов от 0.1 до 5")
            row["seasonality_json"] = stable_json(factors)
        return row

    def _edit(self, c, pid, warehouse, updates):
        old = c.execute("SELECT payload FROM products WHERE product_id=? AND warehouse=?", (pid, warehouse)).fetchone()
        if not old:
            raise ValueError("Товар/склад не найден")
        data = json.loads(old[0]); data.update(updates)
        c.execute("UPDATE products SET payload=? WHERE product_id=? AND warehouse=?", (stable_json(data), pid, warehouse))

    def edit(self, pid, warehouse, updates):
        with self.connect() as c:
            if updates.get("supplier_id") and not c.execute("SELECT 1 FROM suppliers WHERE supplier_id=?", (updates["supplier_id"],)).fetchone():
                raise ValueError("Поставщик не найден")
            self._edit(c, pid, warehouse, updates)
            self.audit(c, "product_edit", {"product_id": pid, "warehouse": warehouse, "updates": updates})

    def save_supplier(self, supplier):
        with self.connect() as c:
            c.execute("INSERT OR REPLACE INTO suppliers VALUES(?,?)", (supplier["supplier_id"], stable_json(supplier)))
            self.audit(c, "supplier_save", supplier)

    def orders(self):
        with self.connect() as c:
            return [json.loads(r[0]) for r in c.execute("SELECT payload FROM orders ORDER BY created_at DESC")]


def fingerprint(value):
    return hashlib.sha256(stable_json(value).encode()).hexdigest()
