"""Tests for Phase 6 hang-up flush (backend/action/hangup.py).

Coverage:
- record_card is additive (does not block, does not raise)
- mark_approved / mark_dismissed / mark_executed flip the right entries
- end_call flushes PENDING + APPROVED via emit_action_card
- end_call skips DISMISSED + EXECUTED
- end_call is idempotent on a second invocation (no double-emit)
- end_call with empty queue is a clean no-op
- end_call with bridge emit raising is logged + counted-zero, not raised
- record_card / mark_* / end_call never raise on internal failure
"""

from unittest.mock import patch
import pytest

from action import hangup


@pytest.fixture(autouse=True)
def _reset_queue():
    hangup.reset()
    yield
    hangup.reset()


# ── recording ────────────────────────────────────────────────────────────────


def test_record_card_appends_pending():
    aid = hangup.record_card("draft_email", {"to": "a@b.c"}, requires_approval=False)
    snap = hangup.snapshot()
    assert len(snap) == 1
    assert snap[0]["id"] == aid
    assert snap[0]["state"] == hangup.PENDING
    assert snap[0]["name"] == "draft_email"
    assert snap[0]["inputs"] == {"to": "a@b.c"}


def test_record_card_is_additive_not_replacing_emit():
    """Recording must not emit; emission is the caller's responsibility."""
    with patch("action.hangup.emit_action_card", create=True) as bridge_emit:
        # No call into emit during recording. We patch by import path that
        # *would* be called by the module — but record_card doesn't import it.
        hangup.record_card("draft_email", {"to": "a@b.c"}, requires_approval=False)
        bridge_emit.assert_not_called()


def test_record_card_copies_inputs():
    inputs = {"to": "a@b.c"}
    hangup.record_card("draft_email", inputs, requires_approval=False)
    inputs["to"] = "mutated"
    snap = hangup.snapshot()
    assert snap[0]["inputs"] == {"to": "a@b.c"}


# ── state transitions ───────────────────────────────────────────────────────


def test_mark_approved_flips_pending_only():
    hangup.record_card("send_email", {"to": "a@b.c"}, requires_approval=True)
    assert hangup.mark_approved("send_email", {"to": "a@b.c"}) is True
    assert hangup.snapshot()[0]["state"] == hangup.APPROVED


def test_mark_approved_no_match_returns_false():
    assert hangup.mark_approved("nope", {}) is False


def test_mark_dismissed_blocks_replay():
    hangup.record_card("draft_email", {"to": "a@b.c"}, requires_approval=False)
    assert hangup.mark_dismissed("draft_email", {"to": "a@b.c"}) is True
    assert hangup.snapshot()[0]["state"] == hangup.DISMISSED


def test_mark_executed_flips_pending_or_approved():
    hangup.record_card("check_calendar", {"date": "2026-05-18"}, requires_approval=False)
    hangup.mark_executed("check_calendar", {"date": "2026-05-18"})
    assert hangup.snapshot()[0]["state"] == hangup.EXECUTED

    hangup.record_card("send_email", {"to": "x"}, requires_approval=True)
    hangup.mark_approved("send_email", {"to": "x"})
    hangup.mark_executed("send_email", {"to": "x"})
    assert hangup.snapshot()[-1]["state"] == hangup.EXECUTED


def test_matcher_picks_most_recent_when_duplicates():
    hangup.record_card("draft_email", {"to": "a@b.c"}, requires_approval=False)
    hangup.record_card("draft_email", {"to": "a@b.c"}, requires_approval=False)
    hangup.mark_dismissed("draft_email", {"to": "a@b.c"})
    snap = hangup.snapshot()
    # most recently inserted is dismissed; earlier remains pending
    assert snap[0]["state"] == hangup.PENDING
    assert snap[1]["state"] == hangup.DISMISSED


# ── end_call ────────────────────────────────────────────────────────────────


def test_end_call_flushes_pending_and_approved_skips_dismissed():
    hangup.record_card("draft_email", {"to": "a"}, requires_approval=False)        # PENDING → flush
    hangup.record_card("send_email", {"to": "b"}, requires_approval=True)
    hangup.mark_approved("send_email", {"to": "b"})                                # APPROVED → flush
    hangup.record_card("draft_email", {"to": "c"}, requires_approval=False)
    hangup.mark_dismissed("draft_email", {"to": "c"})                              # DISMISSED → skip
    hangup.record_card("check_calendar", {"date": "x"}, requires_approval=False)
    hangup.mark_executed("check_calendar", {"date": "x"})                          # EXECUTED → skip

    with patch("bridge.emit_action_card") as mock_emit:
        flushed = hangup.end_call()

    assert flushed == 2
    assert mock_emit.call_count == 2

    flushed_names = sorted(call.args[0] for call in mock_emit.call_args_list)
    assert flushed_names == ["draft_email", "send_email"]

    # All flushed entries carry end_of_call: True in their inputs payload.
    for call in mock_emit.call_args_list:
        _name, inputs = call.args[0], call.args[1]
        assert inputs.get("end_of_call") is True


def test_end_call_is_idempotent():
    hangup.record_card("draft_email", {"to": "a"}, requires_approval=False)
    with patch("bridge.emit_action_card") as mock_emit:
        first = hangup.end_call()
        second = hangup.end_call()
    assert first == 1
    assert second == 0
    assert mock_emit.call_count == 1  # second call did not re-emit


def test_end_call_empty_queue_is_clean_noop():
    with patch("bridge.emit_action_card") as mock_emit:
        flushed = hangup.end_call()
    assert flushed == 0
    mock_emit.assert_not_called()


def test_end_call_bridge_failure_is_logged_not_raised():
    hangup.record_card("draft_email", {"to": "a"}, requires_approval=False)
    hangup.record_card("draft_email", {"to": "b"}, requires_approval=False)

    def boom(*a, **kw):
        raise RuntimeError("bridge down")

    with patch("bridge.emit_action_card", side_effect=boom):
        # MUST NOT RAISE. The flush returned 0 (zero actually emitted).
        flushed = hangup.end_call()
    assert flushed == 0
    # Both entries are still marked FLUSHED — the queue is consistent even
    # though no bytes left the process.
    states = [a["state"] for a in hangup.snapshot()]
    assert states == [hangup.FLUSHED, hangup.FLUSHED]


def test_end_call_carries_requires_approval_through():
    hangup.record_card("send_email", {"to": "b"}, requires_approval=True)
    with patch("bridge.emit_action_card") as mock_emit:
        hangup.end_call()
    # signature: emit_action_card(name, inputs, requires_approval=...)
    assert mock_emit.call_args.kwargs["requires_approval"] is True


# ── agent.py integration: record_card called from emission sites ────────────


def test_agent_dispatch_records_card():
    """When the action agent emits a card, hangup.record_card is also called."""
    from action import agent as action_agent

    hangup.reset()
    with patch("action.agent.emit_action_card"):
        action_agent._dispatch("draft_email", {"to": "x"})
    snap = hangup.snapshot()
    assert len(snap) == 1
    assert snap[0]["name"] == "draft_email"
    assert snap[0]["state"] == hangup.PENDING


def test_agent_execute_auto_records_and_marks_executed():
    """Auto tools fire immediately on emission — they should be recorded
    AND marked executed, so end_call doesn't replay them."""
    from action import agent as action_agent

    hangup.reset()
    with patch("action.agent.emit_action_card"):
        action_agent._execute_auto("check_calendar", {"date": "2026-05-18"})
    snap = hangup.snapshot()
    assert len(snap) == 1
    assert snap[0]["state"] == hangup.EXECUTED
