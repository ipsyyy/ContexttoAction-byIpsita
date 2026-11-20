"""SingleStore-backed workspace with a disposable SQLite projection per request."""

from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from memory import workspace
from memory.transcripts import TranscriptStore, chunk
from scripts.demo_singlestore import database_settings

MAX_WORKSPACE_BYTES = 2 * 1024 * 1024

# Fixed column order for the existing shared workspace schema.
TABLES = (
    ("sources", "id, filename, imported_at, text"),
    ("source_analysis", "source_id, state, findings"),
    ("workspace_email", "id, source_id, recipient, subject, body, state, approval_id, message, message_id, updated_at"),
)


class StorageUnavailable(Exception):
    pass


class WorkspaceConflict(Exception):
    pass


class WorkspaceFull(Exception):
    pass


class CloudWorkspace:
    def __init__(self, name):
        self.name = name

    @contextmanager
    def sql(self):
        try:
            import singlestoredb

            settings = database_settings(os.getenv("SINGLESTORE_URL", "").strip())
            with closing(singlestoredb.connect(**settings, autocommit=True)) as db, closing(db.cursor()) as cursor:
                yield cursor
        except (WorkspaceConflict, WorkspaceFull):
            raise
        except Exception as error:
            raise StorageUnavailable(type(error).__name__) from None

    def initialize(self):
        with self.sql() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS cta_web_workspaces (
                    workspace_id VARCHAR(64) PRIMARY KEY, revision BIGINT NOT NULL,
                    payload JSON NOT NULL, SHARD KEY (workspace_id), SORT KEY ()
                )
            """)

    def load(self):
        with self.sql() as cursor:
            cursor.execute("SELECT revision, payload FROM cta_web_workspaces WHERE workspace_id=%s", (self.name,))
            row = cursor.fetchone()
            return (row[0], json.loads(row[1]) if isinstance(row[1], (str, bytes)) else row[1]) if row else (0, {})

    def save(self, revision, snapshot):
        payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
        if len(payload.encode("utf-8")) > MAX_WORKSPACE_BYTES:
            raise WorkspaceFull
        with self.sql() as cursor:
            if revision == 0:
                cursor.execute("INSERT IGNORE INTO cta_web_workspaces VALUES (%s, 1, %s)", (self.name, payload))
            else:
                cursor.execute("""
                    UPDATE cta_web_workspaces SET payload=%s, revision=revision+1
                    WHERE workspace_id=%s AND revision=%s
                """, (payload, self.name, revision))
            if cursor.rowcount != 1:
                raise WorkspaceConflict

    @contextmanager
    def open(self, *, write=False):
        revision, initial = self.load()
        with TemporaryDirectory(prefix="cta-web-") as directory:
            store = TranscriptStore(Path(directory) / "projection.sqlite3")
            with closing(store.connect()) as db, db:
                workspace._setup(db)
                for table, columns in TABLES:
                    placeholders = ", ".join("?" for _ in columns.split(", "))
                    db.executemany(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", initial.get(table, []))
                db.executemany("INSERT INTO passages VALUES (?, ?, ?)", (
                    (source_id, offset, excerpt)
                    for source_id, _, _, body in initial.get("sources", [])
                    for offset, excerpt in chunk(body)
                ))
            yield store
            if write:
                with closing(store.connect()) as db:
                    final = {
                        table: [list(row) for row in db.execute(f"SELECT {columns} FROM {table} ORDER BY 1")]
                        for table, columns in TABLES
                    }
                if revision == 0 or final != initial:
                    self.save(revision, final)
