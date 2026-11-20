import threading
from unittest.mock import patch, MagicMock
import memory.belief as belief_module


def _mock_stream(text: str):
    stream = MagicMock()
    stream.__enter__ = lambda s: s
    stream.__exit__ = MagicMock(return_value=False)
    stream.get_final_text.return_value = text
    return stream


def test_initial_belief():
    assert belief_module.current_belief == "Conversation has not started."


def test_update_belief_writes_new_value():
    with patch.object(belief_module.client.messages, "stream", return_value=_mock_stream("Updated belief.")):
        belief_module.update_belief("Some utterance")
    assert belief_module.current_belief == "Updated belief."


def test_on_partial_utterance_is_nonblocking():
    event = threading.Event()
    def fake_update(text):
        event.set()

    with patch.object(belief_module, "update_belief", side_effect=fake_update):
        belief_module.on_partial_utterance("hello")
        fired = event.wait(timeout=2)
    assert fired, "update_belief never called from background thread"


def test_get_belief_returns_current():
    belief_module.current_belief = "sentinel"
    assert belief_module.get_belief() == "sentinel"
    belief_module.current_belief = "Conversation has not started."
