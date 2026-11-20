"""Phase 6 — hang-up flush.

The pitch promise: "by the time you hang up, everything that needed to happen
already has." This module is the resolution of that promise. It records every
action card emitted during a conversation and, on call end, replays anything
still pending or approved-but-not-executed via the EXISTING
``bridge.emit_action_card`` path. Idempotent across multiple ``end_call``
invocations.

Design rules baked in:

- ADDITIVE. ``record_card`` is called alongside the existing emission paths;
  it does not change when or how cards are emitted. The action agent's
  behavior is unchanged when this module is unused.
- OFF the decision hot path. All call sites are inside the action agent's
  daemon threads or in inbound bridge handlers.
- NEVER RAISE. Every public function catches and logs. A failed end-of-call
  flush is invisible and recoverable; an exception into the live runtime is
  fatal on stage.
- NO NEW CARD TYPE. ``end_call`` re-emits through ``emit_action_card`` with
  the same shape; an ``end_of_call: true`` flag is sprinkled into the
  ``inputs`` dict so consumers can tell replays apart if they want.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional


# ── States ──────────────────────────────────────────────────────────────────
# A queued action moves through one of two terminal flows:
#   PENDING  →  APPROVED  →  EXECUTED
#   PENDING  →  DISMISSED
# An end_of_call sweep additionally moves PENDING / APPROVED → FLUSHED. The
# sweep marks state BEFORE emitting so a re-entry mid-flush is idempotent.

PENDING = "pending"
APPROVED = "approved"
EXECUTED = "executed"
DISMISSED = "dismissed"
FLUSHED = "flushed"

# States that should be replayed at call end.
_FLUSHABLE = {PENDING, APPROVED}


@dataclass
class _QueuedAction:
    id: str
    name: str
    inputs: dict
    requires_approval: bool
    state: str = PENDING
    # When two cards share the same name+inputs (e.g., two identical "draft a
    # follow-up" calls) we still need to disambiguate by recency at match
    # time. The list is ordered by insertion, and matchers walk it in
    # reverse — so this isn't a tiebreaker field, but reverse-iteration order
    # is the implicit tiebreaker. Documented here so future-you doesn't
    # change the iteration direction.
    seq: int = 0


_queue: list[_QueuedAction] = []
_lock = threading.Lock()
_counter: int = 0


# ── Recording (called from action/agent.py emission sites) ──────────────────


def record_card(name: str, inputs: dict, requires_approval: bool) -> str:
    """Record a card that was just emitted. Returns the assigned id.

    Additive — does not alter emission. Never raises."""
    try:
        global _counter
        with _lock:
            _counter += 1
            aid = uuid.uuid4().hex[:12]
            _queue.append(
                _QueuedAction(
                    id=aid,
                    name=name,
                    inputs=dict(inputs or {}),
                    requires_approval=bool(requires_approval),
                    seq=_counter,
                )
            )
        return aid
    except Exception as e:
        print(f"[hangup] record_card error: {e}")
        return ""


# ── State transitions (called from approve / dismiss bridge handlers) ───────


def _find_latest(name: str, inputs: dict, states: set[str]):
    """Locate the most recently recorded action matching name + inputs and
    one of the given states. Caller must hold ``_lock``. Returns the entry
    or None."""
    target = dict(inputs or {})
    for a in reversed(_queue):
        if a.state in states and a.name == name and a.inputs == target:
            return a
    return None


def mark_approved(name: str, inputs: dict) -> bool:
    """User approved a card. Returns True when the queue had a pending
    entry to update, False otherwise. Never raises."""
    try:
        with _lock:
            a = _find_latest(name, inputs, {PENDING})
            if a is None:
                return False
            a.state = APPROVED
            return True
    except Exception as e:
        print(f"[hangup] mark_approved error: {e}")
        return False


def mark_dismissed(name: str, inputs: dict) -> bool:
    """User explicitly dismissed a card. Dismissed entries are NEVER
    replayed at end_call — a dismiss is a user decision and is respected."""
    try:
        with _lock:
            a = _find_latest(name, inputs, {PENDING, APPROVED})
            if a is None:
                return False
            a.state = DISMISSED
            return True
    except Exception as e:
        print(f"[hangup] mark_dismissed error: {e}")
        return False


def mark_executed(name: str, inputs: dict) -> bool:
    """Mark a card as executed. Used by ``auto`` tools that fire immediately
    on emission AND by ``send_email`` after the approve-and-execute path
    completes. Executed cards are not replayed."""
    try:
        with _lock:
            a = _find_latest(name, inputs, {PENDING, APPROVED})
            if a is None:
                return False
            a.state = EXECUTED
            return True
    except Exception as e:
        print(f"[hangup] mark_executed error: {e}")
        return False


# ── End-of-call flush ───────────────────────────────────────────────────────


def end_call() -> int:
    """Replay any PENDING or APPROVED actions as a final end-of-call sweep.

    Reuses ``bridge.emit_action_card`` — no new card type, no new bridge
    method. Each replayed card carries ``inputs.end_of_call = True`` so
    consumers can distinguish a replay from a live emission.

    Marks entries FLUSHED *before* emitting so a re-entry mid-flush is
    idempotent. A second call to end_call() with no new recordings is a
    clean no-op.

    Failure model: any per-card emit failure is logged and skipped; the
    sweep continues. The whole function returns the count actually emitted
    and never raises. If the bridge is silently disconnected, emit is a
    no-op, the count reflects how many we attempted, and the live session
    has already ended so we have nothing to recover.
    """
    try:
        with _lock:
            to_flush = [a for a in _queue if a.state in _FLUSHABLE]
            for a in to_flush:
                a.state = FLUSHED

        if not to_flush:
            print("[hangup] end_call: queue empty / nothing to flush")
            return 0

        # Late-import so this module remains importable in unit tests that
        # patch the bridge before the action agent imports it.
        from bridge import emit_action_card

        flushed = 0
        for a in to_flush:
            try:
                payload = dict(a.inputs)
                payload.setdefault("end_of_call", True)
                emit_action_card(a.name, payload, requires_approval=a.requires_approval)
                flushed += 1
            except Exception as e:
                print(f"[hangup] flush emit error for {a.name}: {e}")

        print(f"[hangup] end_call flushed {flushed} action(s)")
        return flushed
    except Exception as e:
        print(f"[hangup] end_call error: {e}")
        return 0


# ── Test / debug helpers ────────────────────────────────────────────────────


def reset() -> None:
    """For tests / explicit session boundaries. Drops the queue."""
    global _counter
    with _lock:
        _queue.clear()
        _counter = 0


def snapshot() -> list[dict]:
    """Deep-copy of the current queue state. For tests and the inevitable
    debug print. Order: insertion order (oldest first)."""
    with _lock:
        return [
            {
                "id": a.id,
                "name": a.name,
                "inputs": dict(a.inputs),
                "state": a.state,
                "requires_approval": a.requires_approval,
                "seq": a.seq,
            }
            for a in _queue
        ]


def pending_count() -> int:
    with _lock:
        return sum(1 for a in _queue if a.state in _FLUSHABLE)
