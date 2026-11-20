from unittest.mock import Mock

from memory import insights
from memory.transcripts import local_store


def test_upload_analysis_persists_sources_and_draft_only_for_owner(monkeypatch, tmp_path):
    monkeypatch.setenv("CONTEXT_AGENT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-model")
    monkeypatch.setenv("TAVILY_API_KEY", "synthetic-search")
    monkeypatch.setenv("USER_NAME", "Fixture Owner")
    from action import agent, email_agentmail

    research = Mock(return_value={"status": "contradicted", "summary": "WAL exists.", "sources": [
        {"url": "https://sqlite.org/wal.html", "title": "WAL", "excerpt": "SQLite supports WAL."},
    ]})
    send = Mock()
    monkeypatch.setattr(agent, "research_claim", research)
    monkeypatch.setattr(email_agentmail, "send_verified", send)
    monkeypatch.setattr(insights, "publish_memory", Mock())
    monkeypatch.setattr(insights._executor, "submit", lambda function, *args: function(*args))

    store = local_store()
    old = store.import_text("prior.txt", "The invoice threshold is 70 percent.")
    source_text = (
        "Fixture Owner: I will email the checklist.\n"
        "Fixture Mentor: I will share the notes.\n"
        "Fixture Mentor: The invoice threshold is 95 percent.\n"
        "Fixture Mentor: SQLite has no write-ahead log.\n"
    )
    draft = {"subject": "Checklist", "body": "Here is a recap of the checklist plan."}
    analyze = Mock(return_value=[
        {"kind": "commitment", "summary": "Owner will email a checklist.", "quote": "I will email the checklist.", "draft": draft},
        {"kind": "commitment", "summary": "Mentor will share notes.", "quote": "I will share the notes.", "draft": draft},
        {"kind": "contradiction", "summary": "Threshold changed.", "quote": "The invoice threshold is 95 percent.", "historical_source": 1},
        {"kind": "claim", "summary": "SQLite has no WAL.", "quote": "SQLite has no write-ahead log.", "public_check": True},
    ])
    monkeypatch.setattr(insights, "_analyze_excerpt", analyze)
    source = store.import_text("engineering.txt", source_text)
    assert insights.queue(source["id"])
    duplicate = store.import_text("copy.txt", source_text)
    assert duplicate["duplicate"] and not insights.queue(duplicate["id"])
    result = insights.status(local_store(), source["id"])
    assert result["state"] == "complete"
    own, other, contradiction, fact = result["findings"]
    assert own["draft"] == draft and own["attribution"] == "user"
    assert "draft" not in other and other["attribution"] == "other_or_unknown"
    assert contradiction["historical"]["source_id"] == old["id"]
    assert fact["research"]["sources"][0]["url"] == "https://sqlite.org/wal.html"
    analyze.assert_called_once()
    research.assert_called_once()
    send.assert_not_called()
    assert store.delete(source["id"])
    assert insights.status(store, source["id"])["state"] == "not_started"


def test_supermemory_attribution_tags_and_ids_preserve_entity():
    from memory import supermemory_store
    from memory.supermemory_backend import SupermemoryBackend

    claim = {"entity_id": "fixture-owner", "attribute": "decision", "source": "fixture"}
    assert "chief:entity:fixture-owner" in supermemory_store._claim_tags(claim)
    assert "chief:entity:fixture-owner" in SupermemoryBackend._tags_for(claim)
    assert supermemory_store._claim_custom_id(claim).startswith("chief:claim:fixture-owner:")


def test_tavily_request_and_url_validation(monkeypatch):
    from action import search

    monkeypatch.setenv("TAVILY_API_KEY", "synthetic")
    response = Mock()
    response.json.return_value = {"results": [
        {"url": "javascript:example", "title": "Discard"},
        {"url": "https://sqlite.org/wal.html", "title": "WAL", "content": "SQLite supports WAL."},
    ]}
    send = Mock(return_value=response)
    monkeypatch.setattr(search.requests, "post", send)
    results = search.search_sources("Does SQLite support WAL?", 3)
    assert [result["url"] for result in results] == ["https://sqlite.org/wal.html"]
    assert send.call_args.args[0] == "https://api.tavily.com/search"
    assert send.call_args.kwargs["json"]["query"] == "Does SQLite support WAL?"
