import httpx
from unittest.mock import Mock

from memory import superlinked
from memory.chat import answer_question
from memory.transcripts import TranscriptStore


def test_opt_in_ranking_preserves_source_references_and_falls_back(tmp_path, monkeypatch):
    store = TranscriptStore(tmp_path / "demo.sqlite3")
    first = store.import_text("decision.txt", "Retrieval uses one pass.")
    second = store.import_text("proposal.txt", "Retrieval might use three passes.")
    question = "Compare retrieval"
    post = Mock()
    monkeypatch.setattr(superlinked.httpx, "post", post)
    monkeypatch.setenv("SIE_API_KEY", "synthetic-key")
    monkeypatch.setenv("CONTEXT_AGENT_SUPERLINKED_ENABLED", "0")
    original = answer_question(store, question)
    post.assert_not_called()
    assert {item["source_id"] for item in original["sources"]} == {first["id"], second["id"]}
    assert "ranking" not in original

    monkeypatch.setenv("CONTEXT_AGENT_SUPERLINKED_ENABLED", "1")
    post.return_value = httpx.Response(200, request=httpx.Request("POST", "https://example.test"), json={
        "scores": [{"item_id": "1", "rank": 0, "score": 2.0}, {"item_id": "0", "rank": 1, "score": -1.0}],
    })
    scored = answer_question(store, question)
    assert scored["sources"] == original["sources"][::-1]
    assert scored["ranking"]["passages"][0]["source_id"] == original["sources"][1]["source_id"]
    assert scored["ranking"]["passages"][0]["before"] == 2
    assert post.call_args.kwargs["json"]["query"] == {"text": question}

    post.side_effect = httpx.ReadTimeout("synthetic timeout")
    timed_out = answer_question(store, question)
    assert timed_out["sources"] == original["sources"]
    assert timed_out["ranking"]["status"] == "fallback"

    post.side_effect = None
    post.return_value = httpx.Response(200, request=httpx.Request("POST", "https://example.test"), json={
        "scores": [{"item_id": "1", "rank": 0, "score": 2.0}, {"item_id": "1", "rank": 1, "score": 1.0}],
    })
    invalid = answer_question(store, question)
    assert invalid["sources"] == original["sources"]
    assert invalid["ranking"]["status"] == "fallback"
