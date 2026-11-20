"""Copy one existing engineering demo session into a dedicated SingleStore table."""

from argparse import ArgumentParser
from contextlib import closing
from hashlib import sha256
from html import escape
import json
import os
from pathlib import Path
import signal
import sqlite3
from ssl import SSLSocket
import sys
from urllib.parse import unquote, urlsplit

import certifi
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
FILENAME = "01_engineering_retrieval_review.txt"
OUTPUT = ROOT / ".local-data" / "sponsor-evidence"


def demo_session():
    source_id = sha256((ROOT / "demo-data" / FILENAME).read_bytes()).hexdigest()
    path = ROOT / ".local-data" / "transcripts.sqlite3"
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        row = db.execute("""
            SELECT s.filename, s.imported_at, a.findings, s.text
            FROM sources s JOIN source_analysis a ON a.source_id = s.id
            WHERE s.id = ? AND a.state = 'complete'
        """, (source_id,)).fetchone()
    if row is None:
        raise ValueError("engineering demo analysis is unavailable")
    filename, imported_at, findings, original = row
    if sha256(original.encode("utf-8")).hexdigest() != source_id:
        raise ValueError("original source hash did not match")
    selected = [
        {field: item[field] for field in ("id", "source_id", "kind", "summary", "quote", "offset")}
        for item in json.loads(findings) if item["kind"] in {"decision", "lesson"}
    ][:4]
    if not selected or not any(item["kind"] == "decision" for item in selected):
        raise ValueError("engineering demo has no saved decision")
    for item in selected:
        offset = item["offset"]
        if (item["source_id"] != source_id or type(offset) is not int or offset < 0
                or not item["quote"] or original[offset:offset + len(item["quote"])] != item["quote"]):
            raise ValueError("insight citation did not match the original")
    return source_id, filename, imported_at, selected


def database_settings(dsn=None):
    if dsn:
        url = urlsplit(dsn if "://" in dsn else f"mysql://{dsn}")
        if (url.scheme not in {"mysql", "singlestoredb"} or not url.hostname or not url.username or not url.password
                or not url.path.strip("/") or url.query or url.fragment):
            raise ValueError("expected a MySQL DSN")
        host, port = url.hostname, url.port or 3306
        user, password, database = unquote(url.username), unquote(url.password), unquote(url.path.strip("/"))
    else:
        required = ("HOST", "PORT", "USER", "PASSWORD", "DATABASE")
        parts = {name: os.getenv(f"SINGLESTORE_{name}") for name in required}
        if not all(parts.values()):
            missing = ", ".join(f"SINGLESTORE_{name}" for name in required if not parts[name])
            raise ValueError(f"missing configuration: {missing}")
        host, port = parts["HOST"], int(parts["PORT"])
        user, password, database = parts["USER"], parts["PASSWORD"], parts["DATABASE"]
    if not 1 <= port <= 65535:
        raise ValueError("SQL port is outside the valid range")
    return {
        "host": host, "port": port, "user": user, "password": password, "database": database,
        "ssl_ca": os.getenv("SINGLESTORE_SSL_CA") or certifi.where(),
        "ssl_verify_identity": True, "ssl_verify_cert": True, "ssl_disabled": False, "pure_python": True,
        "connect_timeout": 5, "track_env": False,
    }


def write_session(connect, settings, session):
    source_id, filename, imported_at, insights = session
    with closing(connect(**settings)) as db, closing(db.cursor()) as cursor:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cta_ipsita_hackathon_session (
                source_id CHAR(64) PRIMARY KEY, filename VARCHAR(255) NOT NULL,
                imported_at VARCHAR(40) NOT NULL, insights JSON NOT NULL,
                SHARD KEY (source_id), SORT KEY ()
            )
        """)
        cursor.execute("""
            INSERT INTO cta_ipsita_hackathon_session VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE filename=VALUES(filename), imported_at=VALUES(imported_at),
                insights=VALUES(insights)
        """, (source_id, filename, imported_at, json.dumps(insights)))
        db.commit()


def read_session(connect, settings, source_id):
    with closing(connect(**settings)) as db, closing(db.cursor()) as cursor:
        cursor.execute("""
            SELECT source_id, filename, imported_at, insights
            FROM cta_ipsita_hackathon_session WHERE source_id = %s
        """, (source_id,))
        row = cursor.fetchone()
    if row is None:
        raise ValueError("no persisted session")
    identifier, filename, imported_at, insights = row
    return identifier, filename, imported_at, json.loads(insights) if isinstance(insights, (str, bytes)) else insights


def save_report(session):
    source_id, filename, imported_at, insights = session
    OUTPUT.mkdir(parents=True, exist_ok=True)
    report = {"provider": "SingleStore", "verification": "fresh SQL connection", "source_id": source_id,
              "filename": filename, "imported_at": imported_at, "insights": insights}
    (OUTPUT / "singlestore.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    cards = "".join(
        f"<h2>{escape(item['summary'])}</h2><blockquote>{escape(item['quote'])}</blockquote>"
        f"<p>Original character {item['offset']} · finding {escape(item['id'])}</p>"
        for item in insights
    )
    (OUTPUT / "singlestore.html").write_text(
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
        "<title>Engineering session from SingleStore</title>"
        "<style>body{font:17px system-ui;max-width:760px;margin:40px auto;padding:0 20px}"
        "code{overflow-wrap:anywhere}</style>"
        f"<h1>{escape(filename)}</h1><p>Read through a fresh SingleStore connection</p>"
        f"<p>{escape(imported_at)}</p><code>{escape(source_id)}</code>{cards}", encoding="utf-8",
    )


def timeout(*_):
    raise TimeoutError("SingleStore command deadline")


def safe_error(error):
    code = getattr(error, "errno", None)
    if type(code) is not int:
        return type(error).__name__
    descriptions = {
        1044: "database access denied", 1045: "SQL authentication rejected",
        1049: "unknown database", 1142: "SQL privilege denied", 2003: "connection unavailable",
    }
    return f"{type(error).__name__} {code} ({descriptions.get(code, 'SQL error')})"


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("probe", "publish", "read"))
    args = parser.parse_args()
    load_dotenv(ROOT / ".env", interpolate=False)
    try:
        from singlestoredb import connect
    except ImportError:
        print("SingleStore blocked: install the optional singlestoredb driver.", file=sys.stderr)
        return 2
    signal.signal(signal.SIGALRM, timeout)
    signal.alarm(25)
    try:
        settings = database_settings(os.getenv("SINGLESTORE_URL"))
        with closing(connect(**settings)) as db, closing(db.cursor()) as cursor:
            socket = getattr(db, "_sock", None)
            if not isinstance(socket, SSLSocket) or not socket.version():
                raise ValueError("SQL connection did not negotiate TLS")
            cursor.execute("SELECT DATABASE(), @@memsql_version")
            row = cursor.fetchone()
            if not row or row[0] != settings["database"] or not row[1]:
                raise ValueError("SingleStore did not select the configured database")
            tls_version = socket.version()
        if args.operation == "probe":
            print(f"SingleStore selected the configured database over verified {tls_version}.")
            return 0
        expected = demo_session()
        if args.operation == "publish":
            write_session(connect, settings, expected)
        persisted = read_session(connect, settings, expected[0])
        if persisted != expected:
            raise ValueError("readback differs from local evidence")
        save_report(persisted)
        print(f"SingleStore read back {len(persisted[3])} insights for {persisted[0]} through a fresh connection.")
        return 0
    except Exception as error:
        print(f"SingleStore {args.operation} failed: {safe_error(error)}.", file=sys.stderr)
        return 2
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    raise SystemExit(main())
