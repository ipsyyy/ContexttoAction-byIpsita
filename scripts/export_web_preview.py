"""Export the two demo sources and their saved findings for local browser import."""

from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / ".local-data"
DEMO = ("01_engineering_retrieval_review.txt", "02_networking_product_chat.txt")


def main():
    exported = []
    with closing(sqlite3.connect((DATA / "transcripts.sqlite3").as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        for name in DEMO:
            source_id = sha256((ROOT / "demo-data" / name).read_bytes()).hexdigest()
            row = db.execute("""
                SELECT s.id, s.filename, s.imported_at, s.text, a.state, a.findings
                FROM sources s JOIN source_analysis a ON a.source_id = s.id
                WHERE s.id = ? AND a.state = 'complete'
            """, (source_id,)).fetchone()
            if row is None:
                raise SystemExit(f"Analyze {name} in the desktop app before exporting.")
            actions = db.execute("""
                SELECT id, source_id, subject, body FROM workspace_email
                WHERE source_id = ? AND state IN ('draft', 'awaiting_approval', 'preview')
            """, (source_id,)).fetchall()
            exported.append({**dict(row), "findings": json.loads(row["findings"]),
                             "actions": [dict(action) for action in actions]})

    output = DATA / "sponsor-evidence" / "engineering-desktop-export.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump({"format": "context-to-action-preview-v1", "sources": exported}, stream, ensure_ascii=False)
    print(output)


if __name__ == "__main__":
    main()
