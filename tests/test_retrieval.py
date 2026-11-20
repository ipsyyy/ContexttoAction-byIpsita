from unittest.mock import MagicMock, patch
import memory.retrieval as retrieval_module


def _make_moss_mock(texts: list[str]):
    """Moss 1.1.0 is sync — query returns SearchResult directly (no await).
    A plain MagicMock matches that shape."""
    result = MagicMock()
    result.docs = [MagicMock(text=t) for t in texts]
    mock_moss = MagicMock()
    mock_moss.query = MagicMock(return_value=result)
    return mock_moss


def test_retrieve_returns_texts():
    with patch.object(retrieval_module, "moss", _make_moss_mock(["fact A", "fact B"])), \
         patch.object(retrieval_module, "QueryOptions", MagicMock(return_value=MagicMock())):
        facts = retrieval_module.retrieve("some utterance")
    assert facts == ["fact A", "fact B"]


def test_retrieve_runway_utterance_hits_runway_fact():
    runway_fact = "April 15 call: Marcus stated 18 months runway."
    with patch.object(retrieval_module, "moss", _make_moss_mock([runway_fact])), \
         patch.object(retrieval_module, "QueryOptions", MagicMock(return_value=MagicMock())):
        facts = retrieval_module.retrieve("We're tracking about 12 months of runway right now.")
    assert any("runway" in f.lower() for f in facts)
