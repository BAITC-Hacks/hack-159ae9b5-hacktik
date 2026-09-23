"""Read-only retrieval from supplied spreadsheets; never execute workbook content."""
from pathlib import Path
import json
import re
import sqlite3
from contextlib import closing


class SourceLibrary:
    def __init__(self, path):
        self.path = Path(path)

    def connect(self):
        connection = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    def status(self):
        if not self.path.exists():
            return {"supplied_sources": [], "source_records": 0}
        with closing(self.connect()) as db:
            sources = [dict(r) for r in db.execute("SELECT name,rows,sheets FROM files ORDER BY name")]
        return {"supplied_sources": sources, "source_records": sum(r["rows"] for r in sources)}

    def search(self, query):
        if not self.path.exists():
            return []
        tokens = re.findall(r"[\w-]+", query.lower())
        identifiers = [t for t in tokens if any(c.isdigit() for c in t) and len(t) >= 5]
        with closing(self.connect()) as db:
            rows = []
            topic = next((pattern for word, pattern in [
                ("moq", "%MOQ%"), ("кратност", "%MOQ%"), ("сезон", "%Сезонность%"),
                ("пути", "%Товар в пути%"), ("остат", "%Ежемесячные остатки%"),
                ("продаж", "%Ежемесячные продажи%")]
                if word in query.lower()), None)
            for token in identifiers[:3]:
                rows.extend(db.execute("SELECT * FROM documents WHERE lower(sku) LIKE ? ORDER BY CASE WHEN source LIKE ? THEN 0 ELSE 1 END, priority DESC LIMIT 7",
                                       ("%" + token + "%", topic or "")).fetchall())
            if not rows and topic:
                rows = db.execute("SELECT * FROM documents WHERE source LIKE ? ORDER BY CASE WHEN priority=100 THEN 0 ELSE 1 END, id LIMIT 8", (topic,)).fetchall()
            if not rows:
                stop = {"какие", "какой", "сколько", "покажи", "расскажи", "есть", "меня", "этот", "этого", "товар", "товара", "про", "для", "что", "это", "как", "найди", "мне"}
                meaningful = [t for t in tokens if len(t) >= 3 and t not in stop]
                # Russian inflections: prefix matching is intentionally lexical, not model training.
                terms = ['"' + (t[:7] if len(t) > 7 and not any(c.isdigit() for c in t) else t) + '"*' for t in meaningful[:8]]
                if terms:
                    try:
                        rows = db.execute("SELECT d.* FROM search JOIN documents d ON d.id=search.rowid WHERE search MATCH ? ORDER BY bm25(search), d.priority DESC LIMIT 8", (" OR ".join(terms),)).fetchall()
                    except sqlite3.OperationalError:
                        rows = []
            if not rows and re.search(r"данны|источник|баз[аыу]|документ|файл|systemelectric", query.lower()):
                rows = db.execute("SELECT * FROM documents WHERE priority=100 ORDER BY id LIMIT 6").fetchall()
        seen, result = set(), []
        for row in rows:
            if row["id"] in seen:
                continue
            seen.add(row["id"])
            result.append({"title": row["title"], "content": row["body"],
                           "source": row["source"], "url": "/api/sources/" + str(row["id"])})
        return result[:8]

    def document(self, document_id):
        if not self.path.exists():
            return None
        with closing(self.connect()) as db:
            row = db.execute("SELECT source,title,body FROM documents WHERE id=?", (document_id,)).fetchone()
        return dict(row) if row else None
