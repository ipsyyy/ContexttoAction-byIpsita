from memory.cache import build_messages
from memory.past_calls import PAST_CALLS


def test_structure():
    msgs = build_messages("belief text", "utterance text", ["fact1", "fact2"])
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
    content = msgs[0]["content"]
    assert len(content) == 2


def test_facts_block_cached():
    msgs = build_messages("b", "u", ["fact1"])
    facts_block = msgs[0]["content"][0]
    assert facts_block["cache_control"] == {"type": "ephemeral"}
    assert "fact1" in facts_block["text"]


def test_utterance_in_second_block():
    msgs = build_messages("my belief", "trigger line", ["fact"])
    second = msgs[0]["content"][1]
    assert "trigger line" in second["text"]
    assert "my belief" in second["text"]


def test_empty_retrieval_preserves_seeded_context():
    msgs = build_messages("b", "u", [])
    assert msgs[0]["content"][0]["text"] == PAST_CALLS.strip()
