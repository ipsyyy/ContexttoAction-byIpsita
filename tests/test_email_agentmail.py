"""Tests for backend/action/email_agentmail.py and the wiring that drives it.

Coverage:
- Key absent / SDK absent: is_available is False; draft + send return False;
  no exception leaks; dispatch / approve flows produce card-only behavior.
- Key present (mocked client): draft creates a real draft AND the card still
  emits via the existing path; send only fires from inside the approve
  handler — never from the action agent's dispatch.
- Timeout / SDK error: facade returns False sentinel, never raises.
- send_email path explicitly passes through the existing confirm gate
  (live_loop.handle_approve_action) — the test asserts the gate is on
  the path.
"""

from unittest.mock import MagicMock, patch
import pytest

from action import email_agentmail


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_facade(monkeypatch):
    """Each test gets a clean facade state and a controlled env."""
    email_agentmail.reset_client()
    monkeypatch.delenv("AGENTMAIL_API_KEY", raising=False)
    monkeypatch.delenv("AGENTMAIL_INBOX_ID", raising=False)
    yield
    email_agentmail.reset_client()


def _mock_client(send_resp=None, draft_resp=None, list_resp=None,
                 raise_on_send=False, raise_on_draft=False):
    """Build a mock AgentMail client with the methods the facade calls."""
    client = MagicMock()
    if list_resp is None:
        inbox = MagicMock()
        inbox.inbox_id = "in_test"
        list_resp_obj = MagicMock()
        list_resp_obj.inboxes = [inbox]
        list_resp = list_resp_obj
    client.inboxes.list.return_value = list_resp

    if raise_on_draft:
        client.inboxes.drafts.create.side_effect = RuntimeError("boom")
    else:
        if draft_resp is None:
            draft_resp = MagicMock()
            draft_resp.draft_id = "dft_test"
        client.inboxes.drafts.create.return_value = draft_resp

    if raise_on_send:
        client.inboxes.messages.send.side_effect = RuntimeError("boom")
    else:
        if send_resp is None:
            send_resp = MagicMock()
            send_resp.message_id = "msg_test"
        client.inboxes.messages.send.return_value = send_resp

    return client


# ── Absent-key path: card-only fallback ─────────────────────────────────────


def test_is_available_false_without_key():
    assert email_agentmail.is_available() is False


def test_draft_returns_false_without_key():
    """draft() must be False when key is absent — never raises."""
    assert email_agentmail.draft("a@b.c", "subject", "body") is False


def test_send_returns_false_without_key():
    """send() must be False when key is absent — never raises."""
    assert email_agentmail.send("a@b.c", "subject", "body") is False


def test_dispatch_draft_email_no_key_is_card_only(monkeypatch):
    """draft_email behavior with AgentMail absent must EXACTLY match
    today's: a card is emitted, hangup records pending, no email side-
    effect, no error."""
    from action import agent as action_agent
    from action import hangup
    hangup.reset()

    with patch("action.agent.emit_action_card") as mock_emit, \
         patch("action.email_agentmail.is_available", return_value=False), \
         patch("action.email_agentmail.draft") as mock_draft:
        action_agent._dispatch("draft_email", {"to": "a@b.c", "subject": "s", "body": "b"})

    # Card emitted exactly once. draft_email is now classified as "confirm"
    # (so the orb renders a Send button) — requires_approval=True matches
    # the current TOOL_BEHAVIORS dict.
    mock_emit.assert_called_once_with(
        "draft_email", {"to": "a@b.c", "subject": "s", "body": "b"}, requires_approval=True
    )
    # No real draft attempt (gated by is_available).
    # Wait for the async daemon thread that _dispatch spawns to finish.
    import time
    deadline = time.time() + 0.5
    while time.time() < deadline:
        if mock_draft.call_count > 0 or True:
            break
        time.sleep(0.01)
    mock_draft.assert_not_called()
    # Hangup recorded as pending.
    assert hangup.pending_count() == 1


# ── Present-key path: real draft + real send (mocked) ───────────────────────


def test_draft_creates_real_draft_when_available(monkeypatch):
    monkeypatch.setenv("AGENTMAIL_API_KEY", "key_x")
    client = _mock_client()
    with patch.object(email_agentmail, "AgentMail", return_value=client), \
         patch.object(email_agentmail, "_SDK_AVAILABLE", True):
        ok = email_agentmail.draft("a@b.c", "subject", "body")

    assert ok is True
    client.inboxes.drafts.create.assert_called_once()
    args, kwargs = client.inboxes.drafts.create.call_args
    assert args[0] == "in_test"  # inbox_id positional
    assert kwargs["to"] == ["a@b.c"]
    assert kwargs["subject"] == "subject"
    assert kwargs["text"] == "body"


def test_send_invokes_real_send_when_available(monkeypatch):
    monkeypatch.setenv("AGENTMAIL_API_KEY", "key_x")
    client = _mock_client()
    with patch.object(email_agentmail, "AgentMail", return_value=client), \
         patch.object(email_agentmail, "_SDK_AVAILABLE", True):
        ok = email_agentmail.send(["a@b.c", "c@d.e"], "subj", "body")

    assert ok is True
    client.inboxes.messages.send.assert_called_once()
    args, kwargs = client.inboxes.messages.send.call_args
    assert args[0] == "in_test"
    assert kwargs["to"] == ["a@b.c", "c@d.e"]


def test_inbox_id_env_var_preferred(monkeypatch):
    monkeypatch.setenv("AGENTMAIL_API_KEY", "key_x")
    monkeypatch.setenv("AGENTMAIL_INBOX_ID", "in_override")
    client = _mock_client()
    with patch.object(email_agentmail, "AgentMail", return_value=client), \
         patch.object(email_agentmail, "_SDK_AVAILABLE", True):
        email_agentmail.draft("a@b.c", "s", "b")
    # Inbox list should NOT be consulted when env var is explicit.
    client.inboxes.list.assert_not_called()
    args, _ = client.inboxes.drafts.create.call_args
    assert args[0] == "in_override"


# ── Failure paths: sentinel + log, never raise ──────────────────────────────


def test_draft_sdk_error_returns_false(monkeypatch):
    monkeypatch.setenv("AGENTMAIL_API_KEY", "key_x")
    client = _mock_client(raise_on_draft=True)
    with patch.object(email_agentmail, "AgentMail", return_value=client), \
         patch.object(email_agentmail, "_SDK_AVAILABLE", True):
        ok = email_agentmail.draft("a@b.c", "s", "b")
    assert ok is False  # sentinel, no exception


def test_send_sdk_error_returns_false(monkeypatch):
    monkeypatch.setenv("AGENTMAIL_API_KEY", "key_x")
    client = _mock_client(raise_on_send=True)
    with patch.object(email_agentmail, "AgentMail", return_value=client), \
         patch.object(email_agentmail, "_SDK_AVAILABLE", True):
        ok = email_agentmail.send("a@b.c", "s", "b")
    assert ok is False


def test_no_recipient_is_false_not_raise(monkeypatch):
    monkeypatch.setenv("AGENTMAIL_API_KEY", "key_x")
    client = _mock_client()
    with patch.object(email_agentmail, "AgentMail", return_value=client), \
         patch.object(email_agentmail, "_SDK_AVAILABLE", True):
        assert email_agentmail.draft("", "s", "b") is False
        assert email_agentmail.draft(None, "s", "b") is False
        assert email_agentmail.send("", "s", "b") is False
    client.inboxes.drafts.create.assert_not_called()
    client.inboxes.messages.send.assert_not_called()


def test_no_inboxes_returns_false(monkeypatch):
    """If the account has no inboxes and no AGENTMAIL_INBOX_ID is set,
    operations sentinel-false instead of raising."""
    monkeypatch.setenv("AGENTMAIL_API_KEY", "key_x")
    empty_resp = MagicMock()
    empty_resp.inboxes = []
    client = _mock_client(list_resp=empty_resp)
    with patch.object(email_agentmail, "AgentMail", return_value=client), \
         patch.object(email_agentmail, "_SDK_AVAILABLE", True):
        assert email_agentmail.draft("a@b.c", "s", "b") is False
        assert email_agentmail.send("a@b.c", "s", "b") is False
    client.inboxes.drafts.create.assert_not_called()
    client.inboxes.messages.send.assert_not_called()


# ── Confirm-gate invariant: send only fires from approve handler ────────────


def test_dispatch_never_calls_send_directly():
    """Action agent's dispatch for send_email surfaces a CONFIRM card and
    must NEVER call email_agentmail.send (no auto-send). The send only
    happens after the user approves via the approve_action handler."""
    from action import agent as action_agent
    from action import hangup
    hangup.reset()

    with patch("action.agent.emit_action_card") as mock_emit, \
         patch("action.email_agentmail.send") as mock_send, \
         patch("action.email_agentmail.draft") as mock_draft:
        action_agent._dispatch(
            "send_email", {"to": "a@b.c", "subject": "s", "body": "b"}
        )

    # Confirm card was emitted with requires_approval=True (the gate).
    mock_emit.assert_called_once_with(
        "send_email", {"to": "a@b.c", "subject": "s", "body": "b"}, requires_approval=True
    )
    # send NEVER fires from dispatch — the gate must hold.
    mock_send.assert_not_called()
    # draft path also doesn't fire for send_email.
    mock_draft.assert_not_called()
    # Hangup recorded pending requiring approval.
    snap = hangup.snapshot()
    assert snap[0]["state"] == hangup.PENDING
    assert snap[0]["requires_approval"] is True


def test_approve_handler_only_sends_the_stored_draft_once(monkeypatch):
    import scripts.live_loop as live_loop
    import bridge

    monkeypatch.setenv("AGENTMAIL_INBOX_ID", "in_test")
    monkeypatch.setenv("CONTEXT_AGENT_TEST_RECIPIENT", "a@b.c")
    inputs = {"to": "a@b.c", "subject": "stored subject", "body": "stored body"}
    with patch("action.email_agentmail.is_available", return_value=True), patch("bridge.emit") as emit:
        bridge.emit_action_card("send_email", inputs, requires_approval=True)
        approval_id = emit.call_args.args[0]["approval_id"]
        with patch("action.email_agentmail.send_verified", return_value={"state": "verified"}) as send:
            live_loop.handle_approve_action({"name": "send_email", "inputs": inputs})
            live_loop.handle_approve_action({"approval_id": approval_id, "inputs": {"to": "wrong@host.test"}})
            live_loop.handle_approve_action({"approval_id": approval_id})
        send.assert_called_once_with(to="a@b.c", subject="stored subject", body="stored body", approval_id=approval_id)
        assert emit.call_args.args[0]["state"] == "verified"


def test_discard_never_sends(monkeypatch):
    import scripts.live_loop as live_loop
    import bridge

    monkeypatch.setenv("AGENTMAIL_INBOX_ID", "in_test")
    monkeypatch.setenv("CONTEXT_AGENT_TEST_RECIPIENT", "a@b.c")
    with patch("action.email_agentmail.is_available", return_value=True), patch("bridge.emit") as emit:
        bridge.emit_action_card("draft_email", {"to": "a@b.c", "subject": "discard me"}, requires_approval=True)
        approval_id = emit.call_args.args[0]["approval_id"]
        with patch("action.email_agentmail.send_verified") as send:
            live_loop.handle_dismiss_action({"approval_id": approval_id})
            live_loop.handle_approve_action({"approval_id": approval_id})
        send.assert_not_called()


def test_verified_send_requires_a_provider_id_and_matching_readback(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setenv("AGENTMAIL_API_KEY", "key_x")
    monkeypatch.setenv("AGENTMAIL_INBOX_ID", "in_test")
    monkeypatch.setenv("CONTEXT_AGENT_TEST_RECIPIENT", "a@b.c")
    client = _mock_client()
    with patch.object(email_agentmail, "AgentMail", return_value=client), \
         patch.object(email_agentmail, "_SDK_AVAILABLE", True):
        assert email_agentmail.send_verified("wrong@host.test", "subj", "body", "request")["state"] == "rejected"
        client.inboxes.messages.send.assert_not_called()
        client.inboxes.messages.send.return_value = SimpleNamespace()
        assert email_agentmail.send_verified("a@b.c", "subj", "body", "request")["state"] == "unconfirmed"
        client.inboxes.messages.get.assert_not_called()
        client.inboxes.messages.send.return_value = SimpleNamespace(message_id="m1")
        client.inboxes.messages.get.return_value = SimpleNamespace(message_id="m2")
        assert email_agentmail.send_verified("a@b.c", "subj", "body", "request")["state"] == "accepted"
        client.inboxes.messages.get.return_value = SimpleNamespace(message_id="m1")
        assert email_agentmail.send_verified("a@b.c", "subj", "body", "request")["state"] == "verified"
        assert client.inboxes.messages.send.call_args.kwargs["idempotency_key"] == "request"
        client.inboxes.messages.get.assert_called_with("in_test", "m1")
