"""Tests for the considered-silent trace."""

import time
from unittest.mock import MagicMock, patch

import decision.trace as trace_module
import decision.agent as agent_module
import decision.fixtures as fixtures


def _mock_response(json_text: str):
    block = MagicMock()
    block.text = json_text
    resp = MagicMock()
    resp.content = [block]
    return resp


def setup_function(_fn):
    trace_module.clear()


def test_records_silent_with_reason():
    with patch.object(agent_module, "get_latest_facts", return_value=[]), \
         patch.object(agent_module.client.messages, "create",
                      return_value=_mock_response('{"action": "silent"}')):
        agent_module.make_decision("some neutral line")

    entries = trace_module.get_trace()
    assert len(entries) == 1
    assert entries[0].action == "silent"
    assert entries[0].reason == "silent-by-model"
    assert entries[0].utterance == "some neutral line"
    assert entries[0].timestamp > 0


def test_records_whisper_with_reason():
    whisper = '{"action": "whisper", "content": "He said 18. He just said 12."}'
    with patch.object(agent_module, "get_latest_facts", return_value=[]), \
         patch.object(agent_module.client.messages, "create",
                      return_value=_mock_response(whisper)):
        agent_module.make_decision("trigger")

    entries = trace_module.get_trace()
    assert len(entries) == 1
    assert entries[0].action == "whisper"
    assert entries[0].reason == "whisper"


def test_records_timeout_silent():
    def slow_create(*_a, **_kw):
        time.sleep(2.0)
        raise AssertionError("should have timed out")

    with patch.object(agent_module, "DECISION_TIMEOUT_SECONDS", 0.15), \
         patch.object(agent_module, "get_latest_facts", return_value=[]), \
         patch.object(agent_module.client.messages, "create", side_effect=slow_create):
        agent_module.make_decision("neutral chat")

    entries = trace_module.get_trace()
    assert len(entries) == 1
    assert entries[0].action == "silent"
    assert entries[0].reason == "timeout-silent"


def test_records_fallback_fired():
    trigger, _ = fixtures.TRIGGERS[0]

    def slow_create(*_a, **_kw):
        time.sleep(2.0)
        raise AssertionError("should have timed out")

    with patch.object(agent_module, "DECISION_TIMEOUT_SECONDS", 0.15), \
         patch.object(agent_module, "get_latest_facts",
                      return_value=["April 15 call: Marcus stated 18 months runway."]), \
         patch.object(agent_module.client.messages, "create", side_effect=slow_create):
        agent_module.make_decision(trigger)

    entries = trace_module.get_trace()
    assert len(entries) == 1
    assert entries[0].action == "whisper"
    assert entries[0].reason == "fallback-fired-after-timeout"


def test_records_silent_by_model_error():
    with patch.object(agent_module, "get_latest_facts", return_value=[]), \
         patch.object(agent_module.client.messages, "create",
                      side_effect=RuntimeError("api down")):
        agent_module.make_decision("anything")

    entries = trace_module.get_trace()
    assert len(entries) == 1
    assert entries[0].action == "silent"
    assert entries[0].reason == "silent-by-model-error"


def test_buffer_capped_at_capacity():
    trace_module.clear()
    # Stuff in 60 fake entries directly
    for i in range(60):
        trace_module.record(f"utterance {i}", "silent", "silent-by-model")
    entries = trace_module.get_trace()
    assert len(entries) == trace_module.CAPACITY
    # Oldest entries dropped — entry 0 is the 10th we pushed (60 - 50)
    assert entries[0].utterance == "utterance 10"
    assert entries[-1].utterance == "utterance 59"


def test_get_trace_dicts_serializable():
    trace_module.record("hi", "silent", "silent-by-model")
    dicts = trace_module.get_trace_dicts()
    assert dicts[0]["action"] == "silent"
    assert dicts[0]["utterance"] == "hi"
    assert isinstance(dicts[0]["timestamp"], float)


def test_clear_empties_buffer():
    trace_module.record("hi", "silent", "silent-by-model")
    trace_module.clear()
    assert trace_module.get_trace() == []
