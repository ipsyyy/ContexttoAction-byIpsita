"""Loopback web workspace. Serve only public assets and explicit API operations."""

import asyncio
from contextlib import closing
import json
import mimetypes
import os
from pathlib import Path
import re
from socketserver import ThreadingMixIn
import sys
from urllib.parse import urlsplit
from uuid import uuid4
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from evaluation.telemetry import record
from memory import chat, insights, retrieval, workspace
from web.store import CloudWorkspace, StorageUnavailable, WorkspaceConflict, WorkspaceFull

PUBLIC = ROOT / ".local-data" / "web-app" / "public"
ASSETS = {"/": "index.html", "/workspace.css": "workspace.css", "/react.js": "react.js",
          "/react-dom.js": "react-dom.js", "/marked.js": "marked.js", "/purify.js": "purify.js", "/app.js": "app.js"}
MAX_BODY = 2 * 1024 * 1024
MAX_ANALYSIS_CHARS = 24_000
BASE_HEADERS = [
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; "
     "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"),
]


class APIError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def text_field(body, key, maximum):
    value = body.get(key)
    if not isinstance(value, str) or len(value) > maximum or not value.strip():
        raise ValueError(f"Provide a valid {key.replace('_', ' ')}.")
    return value


def source_record(store, source_id):
    source = store.get(source_id)
    if source is None:
        raise APIError("404 Not Found", "Transcript not found.")
    return source


def analyze(store, source_id):
    source = source_record(store, source_id)
    old = insights.status(store, source_id)
    if old["state"] == "complete":
        return old
    if len(source["text"]) > MAX_ANALYSIS_CHARS:
        raise APIError("422 Unprocessable Content", "Analysis supports up to 24,000 characters per transcript.")
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise APIError("503 Service Unavailable", "Claude is unavailable. The transcript is saved and can be analyzed later.")
    try:
        findings = insights.analyze(store, source)
    except Exception as error:
        print(f"[web] Analysis failed: {type(error).__name__}", file=sys.stderr)
        raise APIError("502 Bad Gateway", "Claude analysis failed. The transcript is saved. Try analysis again.") from None
    with closing(store.connect()) as db, db:
        insights._setup(db)
        db.execute("INSERT OR REPLACE INTO source_analysis VALUES (?, 'complete', ?)", (source_id, json.dumps(findings)))
    workspace.workspace_snapshot(store)
    return {"state": "complete", "findings": findings}


def operation(cloud, method, path, body):
    if method == "GET" and path == "/api/workspace":
        with cloud.open() as store:
            records = workspace.workspace_snapshot(store)
            records["services"] = {"claude": bool(os.getenv("ANTHROPIC_API_KEY")),
                                   "tavily": bool(os.getenv("TAVILY_API_KEY") and os.getenv("ANTHROPIC_API_KEY"))}
            return records
    source_path = re.fullmatch(r"/api/transcripts/([a-f0-9]{64})", path)
    if method == "GET" and source_path:
        with cloud.open() as store:
            source = source_record(store, source_path[1])
            return {**source, "analysis": insights.status(store, source["id"])}
    if method == "POST" and path == "/api/chat":
        question = text_field(body, "question", 2_000).strip()
        with cloud.open() as store:
            result = chat.answer_question(store, question, body.get("use_model") is True)
        fields = {"count": len(result["sources"]), "mode": result["mode"]}
        event_id = str(uuid4())
        record("retrieval", event_id=event_id, **fields)
        result["trace_id"] = record("chat", event_id=event_id, ok=True, **fields)
        return result

    if method != "POST" or path not in {
        "/api/transcripts", "/api/analyze", "/api/fact_check", "/api/review_saved",
        "/api/cancel_review", "/api/draft",
    }:
        raise APIError("404 Not Found", "This workspace operation is unavailable.")
    with cloud.open(write=True) as store:
        if path == "/api/transcripts":
            result = store.import_text(text_field(body, "filename", 255), text_field(body, "text", MAX_BODY))
            record("transcript", operation="import", ok=True, duplicate=result["duplicate"])
            return result
        if path == "/api/analyze":
            return analyze(store, text_field(body, "source_id", 64))
        if path == "/api/fact_check":
            if not (os.getenv("TAVILY_API_KEY") and os.getenv("ANTHROPIC_API_KEY")):
                raise APIError("503 Service Unavailable", "Tavily and Claude must be configured for fact-checking.")
            return workspace.fact_check(store, text_field(body, "source_id", 64), text_field(body, "insight_id", 64))
        if path == "/api/draft":
            source = source_record(store, text_field(body, "source_id", 64))
            return workspace.save_email_draft(store, source["id"], text_field(body, "subject", 200),
                                              text_field(body, "body", 8_000))
        action_id = text_field(body, "action_id", 64)
        if path == "/api/review_saved":
            return workspace.review_email(store, action_id, text_field(body, "recipient", 254),
                                          text_field(body, "subject", 200), text_field(body, "body", 8_000))
        workspace.cancel_review(store, action_id, text_field(body, "token", 64))
        return True


def create_app(cloud, origin):
    expected_host = urlsplit(origin).netloc

    def app(environ, start_response):
        method, path = environ["REQUEST_METHOD"], environ["PATH_INFO"]
        status, content_type = "200 OK", "application/json; charset=utf-8"
        try:
            if environ.get("HTTP_HOST") != expected_host:
                raise APIError("403 Forbidden", "Use the workspace URL shown at startup.")
            if method == "GET" and path in ASSETS:
                file = PUBLIC / ASSETS[path]
                if not file.is_file():
                    raise APIError("503 Service Unavailable", "Build the browser assets before starting the web workspace.")
                content = file.read_bytes()
                content_type = (mimetypes.guess_type(file.name)[0] or "application/octet-stream") + "; charset=utf-8"
            else:
                body = {}
                if method == "POST":
                    if environ.get("HTTP_ORIGIN") != origin:
                        raise APIError("403 Forbidden", "Open the workspace in its own browser tab.")
                    if environ.get("CONTENT_TYPE", "").split(";", 1)[0] != "application/json":
                        raise APIError("415 Unsupported Media Type", "Send a JSON request.")
                    length = int(environ.get("CONTENT_LENGTH") or 0)
                    if not 0 < length <= MAX_BODY:
                        raise APIError("413 Content Too Large", "Request exceeds the workspace upload limit.")
                    body = json.loads(environ["wsgi.input"].read(length))
                    if not isinstance(body, dict):
                        raise ValueError("Send a JSON object.")
                content = json.dumps({"ok": True, "result": operation(cloud, method, path, body)}).encode()
        except (APIError, ValueError, StorageUnavailable, WorkspaceConflict, WorkspaceFull) as error:
            if isinstance(error, APIError):
                status, message = error.status, error.message
            elif isinstance(error, StorageUnavailable):
                print(f"[web] SingleStore unavailable: {error}", file=sys.stderr)
                status, message = "503 Service Unavailable", "Cloud storage is unavailable. Try again shortly."
            elif isinstance(error, WorkspaceConflict):
                status, message = "409 Conflict", "The workspace changed in another request. Refresh and try again."
            elif isinstance(error, WorkspaceFull):
                status, message = "413 Content Too Large", "This web workspace has reached its storage limit."
            else:
                status, message = "400 Bad Request", "Invalid JSON request." if isinstance(error, json.JSONDecodeError) else str(error)
            content = json.dumps({"ok": False, "error": message}).encode()
        except Exception as error:
            print(f"[web] Request failed: {type(error).__name__}", file=sys.stderr)
            status = "500 Internal Server Error"
            content = b'{"ok":false,"error":"The workspace request could not be completed."}'
        start_response(status, [("Content-Type", content_type), ("Content-Length", str(len(content))), *BASE_HEADERS])
        return [content]

    return app


class ThreadedServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class QuietHandler(WSGIRequestHandler):
    def log_request(self, *_):
        pass


def main():
    load_dotenv(ROOT / ".env")
    # Reuse grounded transcript mode without sample memories or live action handling.
    os.environ["CONTEXT_AGENT_DESKTOP"] = "1"
    os.environ.pop("CONTEXT_AGENT_TEST_RECIPIENT", None)
    name = os.getenv("CONTEXT_WEB_WORKSPACE", "ipsita-browser")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name):
        raise SystemExit("CONTEXT_WEB_WORKSPACE must contain 1–64 letters, digits, hyphens or underscores.")
    cloud = CloudWorkspace(name)
    try:
        cloud.initialize()
    except StorageUnavailable as error:
        print(f"[web] SingleStore startup: {error}. Pages will show storage errors until it is available.", file=sys.stderr)
    if os.getenv("CONTEXT_WEB_MOSS_READ_ONLY") == "1":
        try:
            asyncio.run(asyncio.wait_for(retrieval.init_moss(), timeout=8))
        except TimeoutError:
            print("[web] Moss initialization timed out.", file=sys.stderr)
    port = int(os.getenv("CONTEXT_WEB_PORT", "8765"))
    origin = f"http://127.0.0.1:{port}"
    with make_server("127.0.0.1", port, create_app(cloud, origin), ThreadedServer, QuietHandler) as server:
        print(f"Context to Action web workspace: {origin}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
