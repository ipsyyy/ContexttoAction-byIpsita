"""Tests for the deterministic decision fallback.

The fallback exists only for the demo runway-contradiction case. It must:
  - stay silent on all 20 decoys, regardless of retrieved facts
  - fire on the trigger when the retrieved facts include the 18-month claim
  - be safe to call with empty / weird inputs

It must NEVER raise.
"""

import concurrent.futures
import time
from unittest.mock import MagicMock, patch

from decision.fallback import fallback_decide
import decision.agent as agent_module
import decision.fixtures as fixtures
from memory.retrieval import _RAW_DOCS


RUNWAY_FACT = "April 15 call: Marcus stated 18 months runway."
NON_RUNWAY_FACTS = [
    "March 2 call: ARR at $1.2M, growing 15% MoM.",
    "March 2 call: Team size 12, hiring 3 engineers.",
]


# -------- pure fallback function --------

def test_fallback_fires_on_runway_trigger():
    trigger, _ = fixtures.TRIGGERS[0]
    result = fallback_decide(trigger, [RUNWAY_FACT])
    assert result is not None
    assert result["action"] == "whisper"
    assert "18" in result["content"]
    assert "12" in result["content"]


def test_fallback_silent_on_all_decoys():
    """The most important property: every decoy must return None even when
    the runway fact is in scope. This is the same conservatism guarantee the
    model has — the fallback may NOT regress it."""
    for decoy in fixtures.DECOYS:
        result = fallback_decide(decoy, [RUNWAY_FACT])
        assert result is None, f"Fallback false-positive on decoy: {decoy!r} -> {result}"


def test_fallback_silent_when_no_runway_fact():
    trigger, _ = fixtures.TRIGGERS[0]
    result = fallback_decide(trigger, NON_RUNWAY_FACTS)
    assert result is None


def test_fallback_silent_when_facts_empty():
    trigger, _ = fixtures.TRIGGERS[0]
    assert fallback_decide(trigger, []) is None
    assert fallback_decide(trigger, None) is None  # type: ignore[arg-type]


def test_fallback_silent_when_utterance_empty():
    assert fallback_decide("", [RUNWAY_FACT]) is None
    assert fallback_decide(None, [RUNWAY_FACT]) is None  # type: ignore[arg-type]


def test_fallback_silent_on_matching_value():
    """Past says 18, current says 18 — agreement, not contradiction."""
    assert fallback_decide("We have 18 months runway.", [RUNWAY_FACT]) is None


def test_fallback_silent_on_tiny_difference():
    """1-month differences are rounding, not contradictions. Require >= 2."""
    assert fallback_decide("We have 17 months runway.", [RUNWAY_FACT]) is None
    assert fallback_decide("We have 19 months runway.", [RUNWAY_FACT]) is None


def test_fallback_fires_on_large_difference():
    result = fallback_decide("We have 6 months runway.", [RUNWAY_FACT])
    assert result is not None
    assert result["action"] == "whisper"


def test_fallback_silent_on_implausible_value():
    """200 months runway is noise, not a real claim."""
    assert fallback_decide("We have 200 months runway.", [RUNWAY_FACT]) is None


# -------- timeout integration with make_decision --------

def test_timeout_triggers_fallback_on_trigger():
    """When the model call hangs past the deadline, the fallback fires on
    the trigger using the retrieved runway fact."""
    trigger, _ = fixtures.TRIGGERS[0]

    def slow_create(*_a, **_kw):
        time.sleep(2.0)  # > the patched timeout below
        raise AssertionError("should have timed out before this returned")

    with patch.object(agent_module, "DECISION_TIMEOUT_SECONDS", 0.2), \
         patch.object(agent_module, "get_latest_facts", return_value=[RUNWAY_FACT]), \
         patch.object(agent_module.client.messages, "create", side_effect=slow_create):
        result = agent_module.make_decision(trigger)

    assert result["action"] == "whisper"
    assert "18" in result["content"]


def test_timeout_stays_silent_on_decoys():
    """When the model times out on a decoy, the fallback must not fire."""
    def slow_create(*_a, **_kw):
        time.sleep(2.0)
        raise AssertionError("should have timed out before this returned")

    with patch.object(agent_module, "DECISION_TIMEOUT_SECONDS", 0.15), \
         patch.object(agent_module, "get_latest_facts", return_value=[RUNWAY_FACT]), \
         patch.object(agent_module.client.messages, "create", side_effect=slow_create):
        for decoy in fixtures.DECOYS:
            result = agent_module.make_decision(decoy)
            assert result["action"] == "silent", \
                f"Fallback fired on decoy under timeout: {decoy!r} -> {result}"


def test_model_error_consults_fallback():
    """A hard error from the API also drops to the fallback."""
    trigger, _ = fixtures.TRIGGERS[0]

    with patch.object(agent_module, "get_latest_facts", return_value=[RUNWAY_FACT]), \
         patch.object(agent_module.client.messages, "create",
                      side_effect=RuntimeError("api down")):
        result = agent_module.make_decision(trigger)

    assert result["action"] == "whisper"


def test_model_returns_silent_is_trusted():
    """When the model successfully returns silent, the fallback is NOT
    consulted — we trust the model on the happy path."""
    trigger, _ = fixtures.TRIGGERS[0]
    block = MagicMock()
    block.text = '{"action": "silent"}'
    resp = MagicMock()
    resp.content = [block]

    with patch.object(agent_module, "get_latest_facts", return_value=[RUNWAY_FACT]), \
         patch.object(agent_module.client.messages, "create", return_value=resp):
        result = agent_module.make_decision(trigger)

    assert result == {"action": "silent"}


def test_fallback_uses_the_first_seeded_runway_claim():
    trigger, _ = fixtures.TRIGGERS[0]
    result = fallback_decide(trigger, [text for _, text in _RAW_DOCS])
    assert result is not None
    assert result["content"] == "Past call said 18 months runway. They just said 12."
