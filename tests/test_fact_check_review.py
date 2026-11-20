from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from action import agent, search
from memory import insights
from memory.transcripts import TranscriptStore


def test_saved_for_later_claim_is_not_searched(monkeypatch, tmp_path):
    monkeypatch.setenv("TAVILY_API_KEY", "synthetic")
    store = TranscriptStore(tmp_path / "context.sqlite3")
    quote = "The public acquisition closed in 2022."
    source = store.import_text("meetup.txt", f"Arjun: {quote}")
    monkeypatch.setattr(insights, "_analyze_excerpt", lambda *_: [
        {"kind": "claim", "quote": quote, "summary": "Acquisition date", "public_check": True, "review_later": True}
    ])
    check = Mock()
    monkeypatch.setattr(agent, "research_claim", check)
    result = insights.analyze(store, store.get(source["id"]))[0]
    assert result["review_later"] and result["public_check"]
    assert result["research"]["status"] == "skipped"
    assert "later review" in result["research"]["summary"]
    check.assert_not_called()


def test_tavily_failure_returns_sanitized_error_and_no_evidence(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "synthetic")
    monkeypatch.setattr(agent.client.messages, "create", lambda **_: SimpleNamespace(
        content=[SimpleNamespace(text="public acquisition official announcement")]
    ))
    response = requests.Response()
    response.status_code = 429
    monkeypatch.setattr(search.requests, "post", lambda *_, **__: response)
    result = agent.research_claim("The acquisition closed in 2022.")
    assert result["status"] == "unavailable"
    assert "HTTP 429" in result["summary"]
    assert not result["sources"]


@pytest.mark.parametrize("query,expected", [("skip", "skipped"), ("public acquisition announcement", "unsupported")])
def test_unverified_claim_does_not_report_success(monkeypatch, query, expected):
    monkeypatch.setattr(agent.client.messages, "create", lambda **_: SimpleNamespace(
        content=[SimpleNamespace(text=query)]
    ))
    monkeypatch.setattr(agent, "search_sources", lambda *_, **__: [])
    result = agent.research_claim("The acquisition closed in 2022.")
    assert result["status"] == expected and result["summary"]
    assert not result["sources"]


def test_desktop_does_not_start_automatic_live_web_whispers(monkeypatch):
    monkeypatch.setenv("CONTEXT_AGENT_DESKTOP", "1")
    monkeypatch.setenv("TAVILY_API_KEY", "synthetic")
    thread = Mock()
    monkeypatch.setattr(agent.threading, "Thread", thread)
    agent.fire_fact_check("A public claim during live listening.")
    thread.assert_not_called()
