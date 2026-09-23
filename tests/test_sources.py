from pathlib import Path
import sqlite3

from app.source_data import SourceLibrary


def test_supplied_workbooks_are_indexed_with_provenance():
    library = SourceLibrary(Path(__file__).parents[1] / "data" / "sources.sqlite3")
    status = library.status()
    assert len(status["supplied_sources"]) == 6
    assert status["source_records"] == 79685
    result = library.search("MOQ ATN000343")
    assert result and "MOQ" in result[0]["source"]
    assert "ATN000343" in result[0]["content"]
    assert "строка" in result[0]["source"]
    assert not library.search("Привет")


def test_raw_rows_preserve_sign_and_historical_snapshots():
    library = SourceLibrary(Path(__file__).parents[1] / "data" / "sources.sqlite3")
    db = library.connect()
    assert db.execute("SELECT count(*) FROM raw_rows").fetchone()[0] == 79685
    row = db.execute("SELECT values_json FROM raw_rows WHERE source LIKE 'Динамика%' AND row_number=2").fetchone()[0]
    assert '2023' in row and '-10' in row
    db.close()
    items = library.search("товар в пути ATN000343")
    assert "1118" in items[0]["content"]
    assert "22.09.2026" in items[0]["content"]


def test_source_api_and_geocoding_configuration():
    from fastapi.testclient import TestClient
    from app.main import create_app
    with TestClient(create_app(db_path=":memory:")) as client:
        assert client.get("/api/data/status").json()["source_records"] == 79685
        assert client.get("/api/sources/1").status_code == 200
        assert client.post("/api/chat", json={"message":"   "}).status_code == 422
        assert client.post("/api/chat", json={"message":"скидки", "lat":43}).status_code == 422
