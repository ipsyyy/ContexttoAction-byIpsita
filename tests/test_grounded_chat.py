from types import SimpleNamespace
from unittest.mock import patch

from memory.chat import ABSTAIN, answer_question
from memory.transcripts import TranscriptStore


def test_local_chat_searches_imported_text_and_seed(tmp_path):
    store = TranscriptStore(tmp_path / "context.sqlite3")
    source = store.import_text("workshop.txt", "Ada: Try a purple invoice router.")
    result = answer_question(store, "purple invoice router")
    assert result["mode"] == "local"
    assert result["sources"][0]["source_id"] == source["id"]
    assert result["sources"][0]["excerpt"] == "Ada: Try a purple invoice router."
    assert answer_question(store, "Marcus Chen runway")["sources"][0]["filename"] == "Chief sample memory"
    assert answer_question(store, "Coordinates of Saturn")["answer"] == ABSTAIN


def test_model_cites_both_sources_and_cannot_send(tmp_path, monkeypatch):
    store = TranscriptStore(tmp_path / "context.sqlite3")
    first = store.import_text("workshop.txt", "Ada: Invoice review reduces risk.")
    second = store.import_text("hackathon.md", "Lin: Invoice review caught duplicate payments.")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only")
    response = SimpleNamespace(content=[SimpleNamespace(
        text='```json\n{"answer":"Draft: both sessions used invoice review.","citations":[1,2]}\n```',
    )])
    with patch("anthropic.Anthropic") as anthropic, patch("action.email_agentmail.send") as send:
        anthropic.return_value.messages.create.return_value = response
        result = answer_question(store, "Draft an invoice review follow-up", use_model=True)
    assert result["mode"] == "claude"
    assert {item["source_id"] for item in result["sources"]} == {first["id"], second["id"]}
    send.assert_not_called()
    assert "tools" not in anthropic.return_value.messages.create.call_args.kwargs
