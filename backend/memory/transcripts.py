"""Local transcript sources and exact excerpts shared by chat and live decisions."""

import hashlib
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

MAX_BYTES = 1024 * 1024
PASSAGE_SIZE = 1500
WORD = re.compile(r"[^\W_]+", re.UNICODE)
COMMON_WORDS = set("a an and are about as at did do for from give how i in is it me of on or show that the this to was were what when where which who with you".split())


class TranscriptStore:
    def __init__(self, path: Path):
        self.path = path

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        with db:
            db.execute("""CREATE TABLE IF NOT EXISTS sources (
                id TEXT PRIMARY KEY, filename TEXT NOT NULL,
                imported_at TEXT NOT NULL, text TEXT NOT NULL
            )""")
            db.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS passages USING fts5(
                source_id UNINDEXED, offset UNINDEXED, excerpt
            )""")
        return db

    def import_text(self, filename: str, text: str) -> dict:
        if Path(filename).name != filename or Path(filename).suffix.lower() not in {".txt", ".md"}:
            raise ValueError("Choose a TXT or Markdown transcript.")
        if len(text.encode("utf-8")) > MAX_BYTES:
            raise ValueError("Transcript exceeds 1 MB.")
        if not text.strip() or "\x00" in text:
            raise ValueError("Choose a nonempty UTF-8 text file.")
        source_id = hashlib.sha256(text.encode("utf-8")).hexdigest()
        stamp = datetime.now(timezone.utc).isoformat()
        with closing(self.connect()) as db, db:
            inserted = db.execute(
                "INSERT OR IGNORE INTO sources VALUES (?, ?, ?, ?)",
                (source_id, filename, stamp, text),
            ).rowcount
            if inserted:
                db.executemany(
                    "INSERT INTO passages VALUES (?, ?, ?)",
                    ((source_id, offset, excerpt) for offset, excerpt in chunk(text)),
                )
            source = db.execute(
                "SELECT id, filename, imported_at FROM sources WHERE id = ?", (source_id,),
            ).fetchone()
        return {**dict(source), "duplicate": not inserted}

    def list_sources(self) -> list[dict]:
        with closing(self.connect()) as db:
            rows = db.execute("SELECT id, filename, imported_at FROM sources ORDER BY imported_at DESC")
            return [dict(row) for row in rows]

    def get(self, source_id: str) -> dict | None:
        with closing(self.connect()) as db:
            row = db.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
            return dict(row) if row else None

    def delete(self, source_id: str) -> bool:
        with closing(self.connect()) as db, db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name = 'source_analysis'").fetchone():
                db.execute("DELETE FROM source_analysis WHERE source_id = ?", (source_id,))
            db.execute("DELETE FROM passages WHERE source_id = ?", (source_id,))
            return bool(db.execute("DELETE FROM sources WHERE id = ?", (source_id,)).rowcount)

    def search(self, question: str, limit: int = 6, exclude_source: str = "") -> list[dict]:
        terms = list(dict.fromkeys(
            word for word in WORD.findall(question.casefold())
            if word not in COMMON_WORDS and len(word) > 1
        ))[:32]
        if not terms or not self.path.exists():
            return []
        query = " OR ".join(f'"{term}"' for term in terms)
        with closing(self.connect()) as db:
            rows = db.execute(
                """SELECT sources.id AS source_id, filename, offset, excerpt
                   FROM passages JOIN sources ON sources.id = passages.source_id
                   WHERE passages MATCH ? AND sources.id != ? ORDER BY rank, sources.id, offset LIMIT ?""",
                (query, exclude_source, limit),
            )
            return [dict(row) for row in rows]


def chunk(text: str, size: int = PASSAGE_SIZE):
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            line = text.rfind("\n", start + size // 2, end)
            if line != -1:
                end = line + 1
        excerpt = text[start:end]
        if excerpt.strip():
            yield start, excerpt
        start = end


def local_store() -> TranscriptStore:
    default = Path(__file__).resolve().parents[2] / ".local-data"
    return TranscriptStore(Path(os.getenv("CONTEXT_AGENT_DATA_DIR", str(default))) / "transcripts.sqlite3")
