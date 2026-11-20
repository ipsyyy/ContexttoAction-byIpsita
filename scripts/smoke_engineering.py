"""Run the synthetic engineering flow with live AI and research, and mocked mail."""

import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
from unittest.mock import Mock, patch
from types import SimpleNamespace

from dotenv import load_dotenv

sys.path[:0] = ["backend", "."]
load_dotenv(".env")
os.environ["CONTEXT_AGENT_DESKTOP"] = "1"
os.environ["CONTEXT_AGENT_TRACE_ENABLED"] = "0"
os.environ["USER_NAME"] = "Synthetic Owner"

from scripts import live_loop
import bridge
from memory import insights
from action import email_agentmail

with TemporaryDirectory() as directory, patch.dict(os.environ, {"CONTEXT_AGENT_DATA_DIR": directory}), patch.object(insights, "publish_memory"):
    store = insights.local_store()
    store.import_text("earlier-decision.txt", "Synthetic Owner: The current invoice review threshold is 70 percent.")
    events = []
    mailbox = Mock()
    mailbox.inboxes.messages.send.return_value = SimpleNamespace(message_id="synthetic-integration")
    mailbox.inboxes.messages.get.return_value = SimpleNamespace(message_id="synthetic-integration")
    with patch.object(bridge, "emit", events.append), patch.object(email_agentmail, "_get_client", return_value=mailbox), patch.object(email_agentmail, "is_available", return_value=True):
        live_loop.handle_transcripts({"id": "import", "operation": "import", "filename": "engineering-session.txt", "text": Path("tests/fixtures/engineering-session.txt").read_text()})
        imported = events[-1]["result"]
        source_id = imported["id"]
        for _ in range(60):
            progress = insights.status(store, source_id)
            if progress["state"] != "pending":
                break
            time.sleep(1)
        assert progress["state"] == "complete", progress["state"]
        findings = progress["findings"]
        print("Saved finding categories", [(item["kind"], item["attribution"], bool(item.get("draft")), item.get("research", {}).get("status")) for item in findings], flush=True)
        assert any(i["kind"] == "contradiction" and i["historical"]["filename"] == "earlier-decision.txt" for i in findings)
        assert any(i.get("research", {}).get("status") == "contradicted" and i["research"]["sources"] for i in findings)
        own = next(i for i in findings if i["attribution"] == "user" and i.get("draft"))
        assert not any(i.get("draft") for i in findings if i["attribution"] != "user")
        assert mailbox.inboxes.messages.send.call_count == 0
        live_loop.handle_transcripts({"id": "review", "operation": "email_draft", "source_ids": [source_id], **own["draft"]})
        approval = next(e["approval_id"] for e in events if e["type"] == "action_card")
        assert mailbox.inboxes.messages.send.call_count == 0
        live_loop.handle_approve_action({"approval_id": "not-issued"})
        assert mailbox.inboxes.messages.send.call_count == 0
        live_loop.handle_approve_action({"approval_id": approval})
        live_loop.handle_approve_action({"approval_id": approval})
        assert mailbox.inboxes.messages.send.call_count == 1
        outcome = next(e for e in events if e["type"] == "email_outcome")
        assert outcome["state"] == "verified"
        print("PASS engineering import to saved insights, Tavily sources, editable draft, approval and mocked sender readback", flush=True)
