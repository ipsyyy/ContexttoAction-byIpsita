"""Primary call state — what the Main Call Dashboard reads.

Chief's reason for existing: the user is on a phone call, and Chief listens,
remembers, and acts. This module tracks the state of *that* primary call so
the Main Call Dashboard can render it live — counterparty, duration,
running transcript, Chief's whispers, claim count.

Distinct from `action/phone_agentphone.py`, which tracks SIDE calls Chief
places autonomously while the primary call is in progress.

State lifecycle:
    idle    — no conversation in progress (start of app, or 90s post-end)
    active  — utterances are flowing; the conversation is live
    ended   — idle timer fired; session was consolidated; awaiting next utterance

Updates fire a listener (live_loop wires it to bridge.emit_main_call_status)
so the dashboard updates without polling.
"""

from __future__ import annotations

import datetime
import threading
from uuid import uuid4
from typing import Callable, Optional


# ── Constants ────────────────────────────────────────────────────────────────

# Chief is "active" whenever it is listening to the room. That's the
# default from app boot until either pause (→ "paused") or quit. The 90s
# conversation idle timer still consolidates memory in the background, but
# the dashboard state does NOT drop to anything that reads as "no call" —
# the user is on a call as long as Chief is listening.
STATE_ACTIVE = "active"
STATE_PAUSED = "paused"
# Kept as legal value for compatibility, but no code transitions into it
# anymore. (Used to be the initial state; tests may still reference it.)
STATE_IDLE   = "idle"


# ── State ────────────────────────────────────────────────────────────────────

_state_lock = threading.Lock()
_state: dict = {
    "state": STATE_ACTIVE,
    "hint": "",
    "entity_id": "",
    "resolved": False,
    "hydrated_claims": 0,
    "conversation_id": "",
    "started_at": datetime.datetime.now().isoformat(timespec="seconds"),
    "ended_at": "",
    "transcript": [],      # [{"speaker": str, "text": str, "at": str}, ...]
    "whispers":   [],      # [{"text": str, "at": str}, ...]
}

_listener: Optional[Callable[[dict], None]] = None


def set_listener(cb: Optional[Callable[[dict], None]]) -> None:
    global _listener
    _listener = cb


def get_state() -> dict:
    with _state_lock:
        s = dict(_state)
        # Cap what we expose — the dashboard only needs the tail.
        s["transcript"] = list(s["transcript"])[-80:]
        s["whispers"]   = list(s["whispers"])[-20:]
        return s


def _notify() -> None:
    cb = _listener
    if cb is None:
        return
    try:
        cb(get_state())
    except Exception as e:
        print(f"[main_call] listener error: {e}")


def _update(**fields) -> None:
    with _state_lock:
        _state.update(fields)
    _notify()


# ── Lifecycle hooks (called from live_loop) ─────────────────────────────────


def conversation_started(conversation_id: str, counterparty_hint: str = "") -> None:
    """A new conversation session started. Tag it with the id + counterparty
    hint but DO NOT clobber the running transcript — the dashboard treats
    the listening session as one continuous "call" from app boot."""
    with _state_lock:
        _state["conversation_id"] = conversation_id or ""
        if counterparty_hint:
            _state["hint"] = counterparty_hint
        if _state["state"] != STATE_PAUSED:
            _state["state"] = STATE_ACTIVE
    _notify()


def conversation_ended(conversation_id: str | None = None) -> None:
    """The 90s idle timer fired and we consolidated memory. From the user's
    POV, Chief is still listening — so don't change the visible state.
    We just log the event for observability."""
    # Intentional no-op on dashboard state. Internal consolidation happens
    # elsewhere (live_loop._end_conversation_bg).
    return


def set_paused(paused: bool) -> None:
    """Reflect the global pause/resume in the dashboard."""
    with _state_lock:
        _state["state"] = STATE_PAUSED if paused else STATE_ACTIVE
    _notify()


def reset_to_idle() -> None:
    """Hard reset — fully clear state and start a fresh listening session.
    Wired to the dashboard's "Clear" button so the user can wipe the
    transcript without restarting the app."""
    now = datetime.datetime.now().isoformat(timespec="seconds")
    with _state_lock:
        _state.update({
            "state": STATE_ACTIVE,
            "hint": "",
            "entity_id": "",
            "resolved": False,
            "hydrated_claims": 0,
            "conversation_id": "",
            "started_at": now,
            "ended_at": "",
            "transcript": [],
            "whispers": [],
        })
    _notify()


# ── Per-event hooks ─────────────────────────────────────────────────────────


def push_utterance(speaker: str, text: str) -> None:
    """Append one final-utterance row. Speaker is whatever voice_id classifies
    ('user' / 'counterparty' / '') — defaults to 'speaker' if blank."""
    if not text or not text.strip():
        return
    entry = {
        "speaker": speaker or "speaker",
        "text": text.strip(),
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    with _state_lock:
        if not _state["started_at"]:
            _state["started_at"] = entry["at"]
        _state["transcript"].append(entry)
        if len(_state["transcript"]) > 500:
            _state["transcript"] = _state["transcript"][-500:]
    _notify()


def push_whisper(text: str, provenance: dict | None = None) -> None:
    """Chief said this to the user. The dashboard renders whispers inline with
    the transcript at their timestamp."""
    if not text or not text.strip():
        return
    entry = {
        "id": str(uuid4()),
        "source": (provenance or {}).get("source"),
        "source_id": (provenance or {}).get("source_id"),
        "text": text.strip(),
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    with _state_lock:
        _state["whispers"].append(entry)
        if len(_state["whispers"]) > 100:
            _state["whispers"] = _state["whispers"][-100:]
    _notify()


def set_briefing(entity_id: str, resolved: bool, hydrated_claims: int, hint: str = "") -> None:
    """L3 briefing facts the dashboard surfaces in the header."""
    with _state_lock:
        _state["entity_id"]       = entity_id or ""
        _state["resolved"]        = bool(resolved)
        _state["hydrated_claims"] = int(hydrated_claims or 0)
        if hint:
            _state["hint"] = hint
    _notify()
