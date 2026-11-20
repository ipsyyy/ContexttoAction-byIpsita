import json
from contextlib import closing
from uuid import uuid4

import pytest

from memory import workspace
from memory.transcripts import TranscriptStore


@pytest.fixture
def saved_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTEXT_AGENT_TEST_RECIPIENT", "test@example.test")
    store = TranscriptStore(tmp_path / "work.sqlite")
    source = store.import_text("review.txt", "A saved claim.")
    item = {"id": str(uuid4()), "source_id": source["id"], "kind": "claim", "quote": "A saved claim.",
            "draft": {"subject": "Original subject", "body": "Original content."}}
    with closing(store.connect()) as db, db:
        workspace._setup(db)
        db.execute("INSERT INTO source_analysis VALUES (?, 'complete', ?)", (source["id"], json.dumps([item])))
    return store, source, item


def test_drafts_persist_edits_and_require_one_explicit_approval(saved_workspace, monkeypatch):
    from action import email_agentmail
    from unittest.mock import Mock

    store, source, item = saved_workspace
    send = Mock(return_value={"state": "verified", "message_id": "mock-id", "message": "Mock sender readback."})
    monkeypatch.setattr(email_agentmail, "send_verified", send)
    data = workspace.workspace_snapshot(store)
    assert data["sources"][0]["count"] == 1
    draft = data["actions"][0]
    assert draft["state"] == "draft"
    review = workspace.review_email(store, draft["id"], "test@example.test", "Edited", "Final approved body")
    send.assert_not_called()
    workspace.cancel_review(store, draft["id"], review["token"])
    with pytest.raises(ValueError):
        workspace.approve_email(store, draft["id"], review["token"])
    send.assert_not_called()
    review = workspace.review_email(store, draft["id"], "test@example.test", "Edited", "Final approved body")
    with pytest.raises(ValueError):
        workspace.approve_email(store, draft["id"], "invalid")
    workspace.approve_email(store, draft["id"], review["token"])
    send.assert_called_once_with("test@example.test", "Edited", "Final approved body", review["token"])
    with pytest.raises(ValueError):
        workspace.approve_email(store, draft["id"], review["token"])
    result = workspace.workspace_snapshot(TranscriptStore(store.path))["actions"][0]
    assert (result["state"], result["message_id"]) == ("verified", "mock-id")


def test_fact_check_preserves_returned_evidence(saved_workspace, monkeypatch):
    from action import agent

    store, source, item = saved_workspace
    monkeypatch.setenv("TAVILY_API_KEY", "placeholder")
    assert "research" not in workspace.workspace_snapshot(store)["insights"][0]
    monkeypatch.setattr(agent, "research_claim", lambda _: {"status": "skipped", "summary": "", "sources": []})
    assert workspace.fact_check(store, source["id"], item["id"])["status"] == "skipped"
    evidence = {"status": "supported", "summary": "Evidence", "sources": [{"url": "https://sqlite.org/wal.html"}]}
    monkeypatch.setattr(agent, "research_claim", lambda _: evidence)
    workspace.fact_check(store, source["id"], item["id"])
    assert workspace.workspace_snapshot(store)["insights"][0]["research"] == evidence
