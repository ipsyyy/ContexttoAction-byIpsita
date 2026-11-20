"""AgentMail facade — the single point of contact with the AgentMail SDK.

Drives the substance behind two existing tools the action agent already knows
about: ``draft_email`` and ``send_email``. The card emission stays the visible
beat; this module is the real side-effect underneath.

Defensive shape (mirrors backend/memory/supermemory_store.py):
- Lazy client init only when ``AGENTMAIL_API_KEY`` is set and the SDK imports.
- Hard 2.5s timeout on every network call.
- Every operation returns a documented sentinel on any failure (False / None).
- Never raises into callers. Logged failures are silent at the UI.

When the key or SDK is absent, ``is_available()`` returns False and every
call no-ops back to today's card-only behavior. The demo runs end-to-end
without an AgentMail key.

Tool surface (what callers use):
    is_available()                 -> bool
    draft(to, subject, body)       -> bool   # True on success
    send(to, subject, body)        -> bool   # True on success — only ever
                                              # invoked from inside the
                                              # approve-action handler.

Inbox resolution:
- AGENTMAIL_INBOX_ID env var if set (preferred, explicit).
- Otherwise list inboxes and use the first one returned.
- Cached on first successful resolution so we don't re-list every call.
- If resolution fails (no inboxes, list errors), every operation returns
  False and the system falls back to card-only behavior.

Logging policy: log WHO and WHAT (recipient address, draft/send,
draft_id/message_id) — never the API key, never raw auth material. Body
text is truncated in logs.
"""

from __future__ import annotations

import concurrent.futures
import os
import threading
from typing import Optional

try:
    from agentmail import AgentMail  # type: ignore
    _SDK_AVAILABLE = True
except Exception as e:  # pragma: no cover - env-dependent
    print(f"[agentmail] SDK unavailable ({type(e).__name__}) — email path disabled")
    _SDK_AVAILABLE = False
    AgentMail = None  # type: ignore


# ── Constants ────────────────────────────────────────────────────────────────

AGENTMAIL_TIMEOUT_SECONDS = 2.5

# ── Lazy client + inbox cache ───────────────────────────────────────────────

_client: Optional[object] = None
_client_lock = threading.Lock()
_inbox_id: Optional[str] = None
_inbox_lock = threading.Lock()

# Off-path-only executor. None of these calls happen on the decision hot path.
_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="agentmail"
)


def _get_client() -> Optional[object]:
    """Lazy-init the AgentMail client. Returns None when SDK missing or
    AGENTMAIL_API_KEY unset — that's the documented local-fallback mode."""
    global _client
    with _client_lock:
        if _client is not None:
            return _client
        if not _SDK_AVAILABLE:
            return None
        if not os.getenv("AGENTMAIL_API_KEY"):
            return None
        try:
            _client = AgentMail()  # reads AGENTMAIL_API_KEY from env
            return _client
        except Exception as e:
            print(f"[agentmail] client init failed: {type(e).__name__}")
            return None


def reset_client() -> None:
    """For tests / explicit reconfigurations."""
    global _client, _inbox_id
    with _client_lock:
        _client = None
    with _inbox_lock:
        _inbox_id = None


def is_available() -> bool:
    """Predicate for callers that want to know whether to bother. Cheap —
    SDK + env check only, no network round-trip."""
    if not _SDK_AVAILABLE:
        return False
    if not os.getenv("AGENTMAIL_API_KEY"):
        return False
    return True


# ── Internal timeout wrapper ─────────────────────────────────────────────────


def _with_timeout(fn, *args, **kwargs):
    future = _executor.submit(fn, *args, **kwargs)
    return future.result(timeout=AGENTMAIL_TIMEOUT_SECONDS)


def _safe(fn_name: str, fn, *args, **kwargs):
    try:
        return _with_timeout(fn, *args, **kwargs)
    except concurrent.futures.TimeoutError:
        print(f"[agentmail] {fn_name} timed out after {AGENTMAIL_TIMEOUT_SECONDS}s")
        return None
    except Exception as e:
        print(f"[agentmail] {fn_name} error: {type(e).__name__}")
        return None


# ── Inbox resolution ─────────────────────────────────────────────────────────


def _resolve_inbox_id(client) -> Optional[str]:
    """Pick the inbox to send/draft from. AGENTMAIL_INBOX_ID wins; otherwise
    the first one the user has. Cached per process."""
    global _inbox_id
    with _inbox_lock:
        if _inbox_id is not None:
            return _inbox_id
        env_id = os.getenv("AGENTMAIL_INBOX_ID")
        if env_id:
            _inbox_id = env_id.strip()
            return _inbox_id
        # No explicit inbox — list and take the first.
        resp = _safe("inboxes.list", client.inboxes.list, limit=1)
        if resp is None:
            return None
        # Prefer the explicit attribute; only fall through when it is wholly
        # absent on the response (not when it's present but empty — we must
        # respect "no inboxes" rather than masking it via an `or` cascade).
        if isinstance(resp, dict):
            items = resp.get("inboxes")
            if items is None:
                items = resp.get("items")
        else:
            if hasattr(resp, "inboxes"):
                items = getattr(resp, "inboxes")
            elif hasattr(resp, "items"):
                items = getattr(resp, "items")
            else:
                items = None
        if not items:
            print("[agentmail] no inboxes available — set AGENTMAIL_INBOX_ID or create one")
            return None
        first = items[0]
        # Support both attribute and dict shapes defensively.
        ibid = getattr(first, "inbox_id", None)
        if not ibid and isinstance(first, dict):
            ibid = first.get("inbox_id")
        if not ibid:
            print("[agentmail] inbox list returned an entry without inbox_id")
            return None
        _inbox_id = ibid
        return _inbox_id


# ── Public API ───────────────────────────────────────────────────────────────


def _validate_recipient(to) -> Optional[list[str]]:
    """Coerce `to` into a non-empty list[str]. Returns None when invalid."""
    if not to:
        return None
    if isinstance(to, str):
        return [to.strip()] if to.strip() else None
    if isinstance(to, (list, tuple)):
        cleaned = [str(t).strip() for t in to if str(t).strip()]
        return cleaned or None
    return None


def draft(to, subject: str = "", body: str = "") -> bool:
    """Create a real AgentMail draft. Returns True on success, False on any
    failure (including "AgentMail unavailable"). Idempotent at the SDK level
    (AgentMail assigns its own draft_id per call; we don't dedupe locally).

    Off-path. Called from the action agent's daemon dispatch thread."""
    client = _get_client()
    if client is None:
        return False
    recipients = _validate_recipient(to)
    if recipients is None:
        print("[agentmail] draft skipped: empty/invalid recipient")
        return False
    inbox_id = _resolve_inbox_id(client)
    if not inbox_id:
        return False

    res = _safe(
        "drafts.create",
        client.inboxes.drafts.create,
        inbox_id,
        to=recipients,
        subject=str(subject or ""),
        text=str(body or ""),
    )
    if res is None:
        return False
    draft_id = getattr(res, "draft_id", None) or "?"
    print("[agentmail] provider draft created")
    return True


def send(to, subject: str = "", body: str = "") -> bool:
    """Actually send a message via AgentMail. Returns True on success.

    This function is ONLY ever invoked from the approve-action handler, after
    the existing confirm card has been approved by the user. The action agent
    NEVER calls send() directly — it goes through the confirm gate every
    time.

    Off-path. Called from the inbound bridge handler thread."""
    client = _get_client()
    if client is None:
        return False
    recipients = _validate_recipient(to)
    if recipients is None:
        print("[agentmail] send skipped: empty/invalid recipient")
        return False
    inbox_id = _resolve_inbox_id(client)
    if not inbox_id:
        return False

    res = _safe(
        "messages.send",
        client.inboxes.messages.send,
        inbox_id,
        to=recipients,
        subject=str(subject or ""),
        text=str(body or ""),
    )
    if res is None:
        return False
    msg_id = getattr(res, "message_id", None) or "?"
    print("[agentmail] provider accepted send")
    return True


def send_verified(to, subject: str, body: str, approval_id: str) -> dict:
    """Submit a controlled test message once and read it back by provider ID."""
    recipient = os.getenv("CONTEXT_AGENT_TEST_RECIPIENT", "").strip()
    inbox_id = os.getenv("AGENTMAIL_INBOX_ID", "").strip()
    if not recipient or not inbox_id or not is_available():
        return {"state": "preview", "message": "Email is available as a local preview only."}
    recipients = _validate_recipient(to)
    if recipients != [recipient]:
        return {"state": "rejected", "message": "This app can only send to the configured test recipient."}

    client = _get_client()
    if client is None:
        return {"state": "unavailable", "message": "AgentMail could not be initialized."}
    response = _safe(
        "messages.send", client.inboxes.messages.send, inbox_id,
        to=recipients, subject=subject, text=body, idempotency_key=approval_id,
    )
    message_id = getattr(response, "message_id", None)
    if not isinstance(message_id, str) or not message_id:
        return {"state": "unconfirmed", "message": "Send outcome unknown. Check the provider before retrying."}
    readback = _safe("messages.get", client.inboxes.messages.get, inbox_id, message_id)
    if getattr(readback, "message_id", None) != message_id:
        return {
            "state": "accepted", "message_id": message_id,
            "message": "Provider accepted the send. Readback could not be verified.",
        }
    return {
        "state": "verified", "message_id": message_id,
        "message": "Send verified in the sender inbox. Delivery has not been confirmed.",
    }
