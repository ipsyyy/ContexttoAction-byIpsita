"""The browser uses cloud state, exact citations, and a review-only action API."""

from io import BytesIO
import json

import pytest

from web.server import create_app
from web.store import CloudWorkspace, StorageUnavailable, WorkspaceConflict

ORIGIN = "http://127.0.0.1:8765"


class MemoryCloud(CloudWorkspace):
    def __init__(self):
        self.revision = 0
        self.snapshot = {}

    def load(self):
        return self.revision, self.snapshot

    def save(self, revision, snapshot):
        if self.revision != revision:
            raise WorkspaceConflict
        self.revision += 1
        self.snapshot = snapshot


def call(app, method, path, data=None, origin=ORIGIN):
    content = json.dumps(data).encode() if data is not None else b""
    env = {
        "HTTP_HOST": "127.0.0.1:8765", "HTTP_ORIGIN": origin,
        "REQUEST_METHOD": method, "PATH_INFO": path, "CONTENT_TYPE": "application/json",
        "CONTENT_LENGTH": str(len(content)), "wsgi.input": BytesIO(content),
    }
    statuses = []
    response = b"".join(app(env, lambda status, headers: statuses.append(status)))
    return int(statuses[0].split()[0]), json.loads(response)


def test_new_projections_read_cloud_sources_and_drafts(monkeypatch):
    monkeypatch.setenv("CONTEXT_AGENT_DESKTOP", "1")
    monkeypatch.delenv("CONTEXT_AGENT_TRACE_ENABLED", raising=False)
    cloud = MemoryCloud()
    app = create_app(cloud, ORIGIN)
    original = "User: César approved the Finch retrieval plan."
    data = {"filename": "retrieval.md", "text": original}
    status, reply = call(app, "POST", "/api/transcripts", data)
    assert status == 200
    source_id = reply["result"]["id"]
    assert call(app, "POST", "/api/transcripts", data)[1]["result"]["duplicate"]
    assert cloud.revision == 1

    result = call(app, "POST", "/api/chat", {"question": "Finch retrieval plan"})[1]["result"]
    assert result["sources"][0]["source_id"] == source_id
    assert result["sources"][0]["excerpt"] == original
    status, reply = call(app, "POST", "/api/draft", {
        "source_id": source_id, "subject": "Follow-up", "body": "Review the plan.",
    })
    assert status == 200
    draft_id = reply["result"]["id"]

    status, reply = call(app, "POST", "/api/review_saved", {
        "action_id": draft_id, "recipient": "reader@example.test", "subject": "Edited", "body": "Review.",
    })
    assert status == 200
    pending = call(create_app(cloud, ORIGIN), "GET", "/api/workspace")[1]["result"]["actions"][0]
    assert pending["state"] == "awaiting_approval"
    assert pending["source_id"] == source_id
    assert "approval_id" not in pending
    assert call(app, "POST", "/api/approve_saved", {"action_id": draft_id})[0] == 404

    token = reply["result"]["token"]
    assert call(app, "POST", "/api/cancel_review", {"action_id": draft_id, "token": token})[0] == 200
    assert call(app, "GET", "/api/workspace")[1]["result"]["actions"][0]["state"] == "draft"


def test_overlapping_projections_do_not_overwrite_each_other():
    cloud = MemoryCloud()
    with pytest.raises(WorkspaceConflict):
        with cloud.open(write=True) as slow:
            slow.import_text("slow.txt", "Slow concurrent session")
            with cloud.open(write=True) as fast:
                fast.import_text("fast.txt", "Fast concurrent session")
    with cloud.open() as store:
        assert [item["filename"] for item in store.list_sources()] == ["fast.txt"]


def test_browser_api_denies_cross_origin_and_redacts_storage_failure(monkeypatch):
    cloud = MemoryCloud()
    app = create_app(cloud, ORIGIN)
    assert call(app, "POST", "/api/transcripts", {}, origin="https://example.test")[0] == 403

    def offline():
        raise StorageUnavailable("OperationalError")

    monkeypatch.setattr(cloud, "load", offline)
    assert call(app, "GET", "/api/workspace") == (
        503, {"ok": False, "error": "Cloud storage is unavailable. Try again shortly."},
    )
