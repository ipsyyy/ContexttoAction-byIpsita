"""End-to-end tests for decision/loop.py.

Verifies the contract: cold-start trigger still fires the whisper, decoy after
cold start stays silent, and a finalized utterance kicks belief update in the
background WITHOUT blocking the decision call.
"""

import threading
import time
from unittest.mock import MagicMock, patch

import memory.belief as belief_module
import decision.agent as agent_module
import decision.loop as loop_module


TRIGGER = "We're tracking about 12 months of runway right now."
DECOY = "Yeah, I think we're in a good spot heading into Q3."
RUNWAY_FACT = "April 15 call: Marcus stated 18 months runway."


def _mock_response(json_text: str):
    block = MagicMock()
    block.text = json_text
    resp = MagicMock()
    resp.content = [block]
    return resp


def _reset_belief_cold():
    """Force belief back to cold start for a clean test."""
    belief_module.reset_belief()


def test_cold_start_trigger_fires_whisper():
    """No prior partials. First utterance is the trigger. The whisper must fire
    on a cold belief — belief is an enhancer, not a precondition."""
    _reset_belief_cold()
    assert belief_module.get_belief() == "Conversation has not started."

    whisper_json = (
        '{"action": "whisper", '
        '"content": "He said 18 months runway on April call. He just said 12."}'
    )
    emitted = []

    with patch.object(agent_module.client.messages, "create",
                      return_value=_mock_response(whisper_json)), \
         patch.object(agent_module, "get_latest_facts", return_value=[RUNWAY_FACT]), \
         patch.object(loop_module, "emit_whisper", side_effect=lambda c: emitted.append(c)), \
         patch.object(loop_module, "fire_action"), \
         patch.object(loop_module, "note_final_utterance"):
        loop_module.on_utterance_final(TRIGGER)

    assert len(emitted) == 1
    assert "18 months" in emitted[0]
    assert "12" in emitted[0]


def test_cold_start_decoy_stays_silent():
    """Cold belief + decoy utterance → no whisper emitted."""
    _reset_belief_cold()

    emitted = []
    with patch.object(agent_module.client.messages, "create",
                      return_value=_mock_response('{"action": "silent"}')), \
         patch.object(agent_module, "get_latest_facts", return_value=[RUNWAY_FACT]), \
         patch.object(loop_module, "emit_whisper", side_effect=lambda c: emitted.append(c)), \
         patch.object(loop_module, "fire_action"), \
         patch.object(loop_module, "note_final_utterance"):
        loop_module.on_utterance_final(DECOY)

    assert emitted == []


def test_final_utterance_kicks_belief_in_background():
    """note_final_utterance must spawn a thread that calls update_belief —
    and on_utterance_final must NOT block waiting for it."""
    _reset_belief_cold()

    fired = threading.Event()

    def fake_update(text):
        # If this runs in the foreground, on_utterance_final hasn't returned
        # yet. The test below times the call to detect that.
        time.sleep(0.5)
        fired.set()

    with patch.object(agent_module.client.messages, "create",
                      return_value=_mock_response('{"action": "silent"}')), \
         patch.object(agent_module, "get_latest_facts", return_value=[]), \
         patch.object(belief_module, "update_belief", side_effect=fake_update), \
         patch.object(loop_module, "emit_whisper"), \
         patch.object(loop_module, "fire_action"):
        t0 = time.perf_counter()
        loop_module.on_utterance_final("some final utterance")
        elapsed = time.perf_counter() - t0

    # on_utterance_final must NOT have waited the 0.5s for fake_update to finish
    assert elapsed < 0.4, f"on_utterance_final blocked on belief update (took {elapsed*1000:.0f}ms)"
    # But the thread should still complete eventually
    assert fired.wait(timeout=2), "update_belief never fired in the background"


def test_note_final_ignores_empty_text():
    """Empty/whitespace text must not spawn any work."""
    with patch.object(belief_module, "update_belief") as upd:
        belief_module.note_final_utterance("")
        belief_module.note_final_utterance("   ")
        time.sleep(0.1)
        assert not upd.called
