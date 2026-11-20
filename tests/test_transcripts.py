import pytest

from memory.transcripts import TranscriptStore, MAX_BYTES


SAMPLE = """[00:01] Ada: The workshop example was a purple invoice router.
[00:44] Lin: The router held low-confidence invoices for a human review.
"""


def test_import_restart_duplicate_and_delete(tmp_path):
    path = tmp_path / "memory.sqlite3"
    store = TranscriptStore(path)
    source = store.import_text("workshop.md", SAMPLE)
    restarted = TranscriptStore(path)
    assert restarted.get(source["id"])["text"] == SAMPLE
    assert restarted.import_text("copy.txt", SAMPLE)["duplicate"]
    assert len(restarted.list_sources()) == 1
    assert restarted.delete(source["id"])
    assert not restarted.search("purple invoice")
    assert restarted.get(source["id"]) is None


def test_retrieval_returns_original_excerpt_and_source(tmp_path):
    store = TranscriptStore(tmp_path / "memory.sqlite3")
    workshop = store.import_text("workshop.txt", SAMPLE)
    store.import_text("small-talk.md", "A sunny weekend at the park.")
    matches = store.search("What purple invoice example did the workshop give?")
    assert matches == [{
        "source_id": workshop["id"], "filename": "workshop.txt", "offset": 0, "excerpt": SAMPLE,
    }]
    assert store.search("What were the coordinates of Saturn?") == []


@pytest.mark.parametrize("name,text", [
    ("memo.pdf", "hello"),
    ("empty.txt", "   "),
    ("oversized.md", "x" * (MAX_BYTES + 1)),
])
def test_invalid_transcripts_are_rejected(tmp_path, name, text):
    store = TranscriptStore(tmp_path / "memory.sqlite3")
    with pytest.raises(ValueError):
        store.import_text(name, text)
    assert not store.path.exists()



def test_live_decision_uses_the_same_source_index(tmp_path, monkeypatch):
    from unittest.mock import patch
    from decision import agent
    from memory.transcripts import local_store

    monkeypatch.setenv("CONTEXT_AGENT_DATA_DIR", str(tmp_path))
    record = local_store().import_text("workshop.txt", SAMPLE)
    with patch.object(agent, "_call_model", return_value={
        "action": "whisper", "content": "Review low-confidence invoices.", "imported_source": 1,
    }) as model:
        result = agent.make_decision("What should the purple invoice router do?")
    assert result["provenance"] == {"source": "workshop.txt", "source_id": record["id"]}
    assert any(SAMPLE in fact for fact in model.call_args.args[1])
