import copy
import hashlib
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.recommendations import mount_recommendations, recommend, recommendations

SOURCE = Path(__file__).parents[1] / "data" / "sources.sqlite3"


def record(**changes):
    return {"sku": "TEST", "name": "Товар", "stock": 20, "available_stock": 10,
            "in_transit": 5, "monthly_sales": 10, "moq": 12, **changes}


def test_reorder_accounts_for_reserved_stock_incoming_and_moq():
    item = recommend(record(), 3)
    assert item["kind"] == "reorder"
    assert item["net_stock"] == 15
    assert item["coverage_months"] == 1.5
    assert item["suggested_quantity"] == 24  # 15 short, two packs of 12


def test_surplus_never_offers_incoming_or_reserved_stock():
    item = recommend(record(stock=100, available_stock=10, in_transit=90), 3)
    assert item["kind"] == "surplus"
    assert item["suggested_quantity"] == 10
    assert recommend(record(stock=100, available_stock=31, in_transit=0), 3)["suggested_quantity"] == 1


def test_decimal_boundary_does_not_round_up_extra_pack():
    assert recommend(record(stock=0, available_stock=0, in_transit=0, monthly_sales=0.1, moq=0.1), 3)["suggested_quantity"] == 0.3
    equal = recommend(record(stock=30, available_stock=25, in_transit=5), 3)
    assert equal["suggested_quantity"] == 0
    assert equal["kind"] == "review"


@pytest.mark.parametrize("changes", [
    {"available_stock": None}, {"in_transit": ""}, {"stock": None},
    {"moq": None}, {"moq": 0}, {"moq_conflict": True}, {"monthly_sales": None},
    {"monthly_sales": 0}, {"monthly_sales": -2}, {"stock": -2},
    {"available_stock": 21}, {"in_transit": -1}, {"monthly_sales": float("nan")},
])
def test_missing_invalid_or_negative_data_requires_review(changes):
    item = recommend(record(**changes), 3)
    assert item["kind"] == "review"
    assert item["suggested_quantity"] is None
    if changes.get("monthly_sales") == -2:
        assert item["monthly_sales"] == -2
        assert item["coverage_months"] is None


def test_numeric_zero_is_not_treated_as_missing_and_input_is_unchanged():
    original = record(stock=0, available_stock=0, in_transit=0)
    before = copy.deepcopy(original)
    assert recommend(original)["suggested_quantity"] == 36
    assert original == before


def test_supplied_dataset_provenance_summary_filter_and_read_only():
    before = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    result = recommendations(SOURCE, limit=500)
    assert result["as_of"] == "2026-09-22"
    assert result["summary"]["total"] == 497
    assert sum(result["summary"][k] for k in ("surplus", "reorder", "review")) == 497
    assert result["returned"] == 497
    sample = recommendations(SOURCE, q="atn000343", limit=1)
    assert sample["summary"] == result["summary"]
    assert sample["filtered_total"] == 1
    item = sample["items"][0]
    assert item["stock"] == item["available_stock"] == 1118
    assert item["in_transit"] == 0
    assert item["monthly_sales"] == pytest.approx(3773 / 12)
    assert item["source"]["row"] == 3
    assert item["source"]["sheet"] == "TDSheet"
    assert item["moq_source"]["file"] == "MOQ SystemElectric.xlsx"
    surplus = recommendations(SOURCE, kind="surplus", limit=1)
    assert surplus["filtered_total"] == result["summary"]["surplus"]
    assert len(surplus["items"]) == 1
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == before


def test_api_validates_controls_and_missing_dataset_is_honest(tmp_path):
    app = FastAPI()
    mount_recommendations(app, path=SOURCE)
    with TestClient(app) as client:
        assert client.get("/api/recommendations?limit=1").json()["returned"] == 1
        for query in ("target_months=0", "target_months=25", "target_months=nan", "limit=0", "limit=501", "kind=buy"):
            assert client.get("/api/recommendations?" + query).status_code == 422
    missing = recommendations(tmp_path / "missing.sqlite3")
    assert missing["items"] == []
    assert missing["as_of"] is None
    assert missing["warnings"]
    assert not (tmp_path / "missing.sqlite3").exists()
