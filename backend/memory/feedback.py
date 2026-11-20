"""User-reviewed preferences and feedback for the local context store."""

from contextlib import closing
from datetime import datetime, timezone
import json
import uuid

from memory.transcripts import local_store


RATINGS = {"helpful", "unhelpful", "incorrect", "unnecessary", "wrong_action", "wrong_memory", "wrong_attribution"}
PREFERENCES = {
    "standard": "",
    "material_only": "Interrupt only for material contradictions supported by a specific source.",
}


def save_feedback(kind, rating, source_ids, correction, preference=None, reference=None):
    if kind not in {"chat", "alert", "insight"} or rating not in RATINGS:
        raise ValueError("Choose a feedback category.")
    if preference is not None and preference not in PREFERENCES:
        raise ValueError("Choose a supported preference.")
    event_id = str(uuid.uuid4())
    with closing(local_store().connect()) as db, db:
        db.execute("""CREATE TABLE IF NOT EXISTS feedback (
            id TEXT PRIMARY KEY, kind TEXT, rating TEXT, source_ids TEXT,
            correction TEXT, preference TEXT, recorded_at TEXT
        )""")
        db.execute(
            "INSERT INTO feedback VALUES (?, ?, ?, ?, ?, ?, ?)",
            (event_id, kind, rating, json.dumps(source_ids), correction, preference,
             datetime.now(timezone.utc).isoformat()),
        )
        if reference is not None:
            db.execute("""CREATE TABLE IF NOT EXISTS feedback_reference (
                feedback_id TEXT PRIMARY KEY, event_id TEXT NOT NULL
            )""")
            db.execute("INSERT INTO feedback_reference VALUES (?, ?)", (event_id, reference))
    return event_id


def approved_instruction():
    store = local_store()
    if not store.path.exists():
        return ""
    with closing(store.connect()) as db:
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'feedback'"
        ).fetchone()
        if not exists:
            return ""
        row = db.execute(
            "SELECT preference FROM feedback WHERE preference IS NOT NULL ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
    return PREFERENCES.get(row["preference"], "") if row else ""
