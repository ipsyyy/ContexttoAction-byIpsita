"""AgentPhone facade — the single point of contact with the AgentPhone SDK.

Drives the substance behind the `call_person` action tool. The orb card stays
the visible beat; this module places the real outbound call when the user
approves.

Defensive shape (mirrors backend/action/email_agentmail.py):
- Lazy client init only when ``AGENTPHONE_API_KEY`` is set and the SDK imports.
- Per-operation timeouts via the SDK's own httpx layer.
- Every operation returns a documented sentinel on any failure (False / None).
- Never raises into callers. Logged failures are silent at the UI.

When the key or SDK is absent, ``is_available()`` returns False and every
call no-ops back to a simulated state-machine fallback — useful for demos
without the network round-trip.

Call state is tracked here so the live-call dashboard can read it via
bridge.emit_phone_status. Transitions fire via ``_update_state`` which
invokes the registered listener (live_loop wires this to the bridge).
"""

from __future__ import annotations

import datetime
import os
import re
import threading
import time
from typing import Callable, Optional


# ── SDK import ──────────────────────────────────────────────────────────────

try:
    from agentphone import AgentPhone, AgentPhoneError  # type: ignore
    _SDK_AVAILABLE = True
except Exception as e:  # pragma: no cover - env-dependent
    print(f"[agentphone] SDK unavailable ({e}) — call path runs in simulated mode")
    _SDK_AVAILABLE = False
    AgentPhone = None  # type: ignore
    AgentPhoneError = Exception  # type: ignore


# ── Constants ────────────────────────────────────────────────────────────────

# Default fallback recipient (the demo line). Override via env. E.164.
DEFAULT_NUMBER = os.getenv("AGENTPHONE_DEFAULT_NUMBER", "+16504416068")

# Caller-id (the "from" line). Optional — AgentPhone will pick if unset.
FROM_NUMBER = os.getenv("AGENTPHONE_FROM_NUMBER", "").strip()

# Name we use for the on-demand agent so a second call reuses the agent
# rather than creating a new one each time.
AGENT_NAME = os.getenv("AGENTPHONE_AGENT_NAME", "Chief")

# Voice — leave None for the SDK default. AgentPhone supports a small set of
# voice ids; override via env if your account has a preferred voice.
AGENT_VOICE = os.getenv("AGENTPHONE_VOICE", "") or None

# Default system prompt used when the call_person tool doesn't pass one.
# This is a SIDE CALL placed while Ben is mid-conversation. The agent's job
# is to accomplish ONE specific task and report back. Tone matters: sound
# like a competent human assistant, not a chatbot reading a script.
DEFAULT_SYSTEM_PROMPT = (
    "You are Chief, Ben's AI assistant placing a brief outbound call while "
    "Ben is busy on another call. Your job is to accomplish exactly this "
    "task and bring back a clear answer:\n\n"
    "    {{purpose}}\n\n"
    "How to handle the call:\n"
    "1. Open with a warm, natural greeting. Identify yourself as Chief "
    "   calling on Ben's behalf and state the reason for the call in ONE "
    "   sentence. Then WAIT for them to respond.\n"
    "2. Listen actively. Have a real back-and-forth — confirm details, ask "
    "   one clarifying follow-up if needed, sound human.\n"
    "3. Once you have the answer (or it's clear they can't give you one), "
    "   restate the answer in one sentence so they can correct you, thank "
    "   them, and end the call.\n"
    "4. End the call as soon as you have the answer. The moment they've "
    "   given you what you need (or made clear they can't), restate it in "
    "   one sentence, thank them, and hang up. Do not linger, do not chit-"
    "   chat, do not ask 'anything else'. A short silence after you have "
    "   the answer means it's time to end. Short pauses mid-question while "
    "   they think are fine.\n"
    "5. If you reach a voicemail or auto-attendant, leave a short message "
    "   stating who you are, what you were calling about, and a callback "
    "   number if you have one. Then end the call.\n"
    "6. Do not pitch, upsell, or chit-chat. Be the most efficient human "
    "   assistant they have ever spoken to.\n"
    "7. If asked whether you are an AI or a person, answer honestly."
)

DEFAULT_GREETING = (
    "Hi, this is Chief — Ben's assistant. I'm calling on his behalf about a "
    "quick question. Do you have a moment?"
)

# Allowed call states. Linear progression; once "ended" the call cannot
# transition again.
STATE_IDLE       = "idle"
STATE_DIALING    = "dialing"
STATE_RINGING    = "ringing"
STATE_CONNECTED  = "connected"
STATE_ENDED      = "ended"
STATE_FAILED     = "failed"


# ── Lazy client + cached resources ──────────────────────────────────────────

_client: Optional[object] = None
_client_lock = threading.Lock()
_agent_id: Optional[str] = None
_from_number_id: Optional[str] = None


def _get_client() -> Optional[object]:
    """Lazy-init the AgentPhone client. Returns None when SDK missing or
    AGENTPHONE_API_KEY unset."""
    global _client
    with _client_lock:
        if _client is not None:
            return _client
        if not _SDK_AVAILABLE or AgentPhone is None:
            return None
        key = os.getenv("AGENTPHONE_API_KEY")
        if not key:
            return None
        try:
            # calls.make does real telco work (provisioning + carrier dial)
            # and can take 15-30s on the slow path. The admin calls
            # (agents.list/update, numbers.list) are skipped entirely when
            # AGENTPHONE_AGENT_ID is pinned, so a longer budget here only
            # affects the one endpoint that actually needs it.
            timeout = float(os.getenv("AGENTPHONE_TIMEOUT", "30"))
            _client = AgentPhone(api_key=key, timeout=timeout)
            return _client
        except Exception as e:
            print(f"[agentphone] client init failed: {e}")
            return None


def is_available() -> bool:
    if not _SDK_AVAILABLE:
        return False
    if not os.getenv("AGENTPHONE_API_KEY"):
        return False
    return True


def _resolve_agent_id(client) -> Optional[str]:
    """Find or create the on-demand "Chief" agent, ensure it has a phone
    number attached, then cache the id. AgentPhone refuses outbound calls
    from an agent with no number attached, so we always run the attach step.

    Resolution priority:
      1. Process cache (_agent_id) — set once we've succeeded.
      2. AGENTPHONE_AGENT_ID env var — lets the user pin a known id so
         we never hit agents.list/agents.create on the hot path.
      3. agents.list — find one named AGENT_NAME.
      4. agents.create — create a fresh one.
    """
    global _agent_id
    if _agent_id:
        return _agent_id

    # Pin via env → fast path. Skips agents.list / agents.update /
    # numbers.list / agents.attach_number entirely. Those admin calls
    # occasionally time out at AgentPhone's API layer and silently eat the
    # whole pre-call budget. We assume the agent is already configured —
    # one-time setup via the dashboard or via the slow path below.
    pinned = (os.getenv("AGENTPHONE_AGENT_ID") or "").strip()
    if pinned:
        _agent_id = pinned
        print(f"[agentphone] using pinned agent id={pinned} (fast path)")
        return _agent_id

    # Slow path — no pinned id. Discover, create if missing, configure, attach
    # a number. Each call has a chance to time out; on any timeout we still
    # return the agent id if we managed to get one.
    aid: Optional[str] = None
    try:
        existing = client.agents.list(limit=50)
        items = getattr(existing, "data", None) or getattr(existing, "agents", None) or []
        for a in items:
            name = getattr(a, "name", None) or (a.get("name") if isinstance(a, dict) else None)
            cur  = getattr(a, "id", None)   or (a.get("id")   if isinstance(a, dict) else None)
            if name == AGENT_NAME and cur:
                aid = cur
                print(f"[agentphone] reusing agent {AGENT_NAME!r} id={aid}")
                break
    except Exception as e:
        print(f"[agentphone] agents.list error: {e}")

    if aid is None:
        try:
            kwargs = {
                "name": AGENT_NAME,
                "system_prompt": DEFAULT_SYSTEM_PROMPT,
                "begin_message": DEFAULT_GREETING,
            }
            if AGENT_VOICE:
                kwargs["voice"] = AGENT_VOICE
            agent = client.agents.create(**kwargs)
            aid = getattr(agent, "id", None) or (agent.get("id") if isinstance(agent, dict) else None)
            if aid:
                print(f"[agentphone] created agent {AGENT_NAME!r} id={aid}")
        except Exception as e:
            print(f"[agentphone] agents.create error: {e}")

    if aid is None:
        return None

    # Refresh the agent's prompt + max-silence so the freshest config lands.
    try:
        client.agents.update(
            aid,
            system_prompt=DEFAULT_SYSTEM_PROMPT,
            begin_message=DEFAULT_GREETING,
            max_silence_ms=6000,
            **({"voice": AGENT_VOICE} if AGENT_VOICE else {}),
        )
        print(f"[agentphone] agent {aid} refreshed (prompt/greeting/silence)")
    except Exception as e:
        print(f"[agentphone] agents.update warning: {e}")

    # Attach a phone number. AgentPhone returns 400 on calls.make without one.
    try:
        number_id = _resolve_from_number_id(client) or _any_owned_number_id(client)
        if number_id:
            try:
                client.agents.attach_number(aid, number_id)
                print(f"[agentphone] attached number {number_id} to agent {aid}")
            except Exception as e:
                msg = str(e).lower()
                if "already" not in msg and "attach" not in msg:
                    print(f"[agentphone] attach_number warning: {e}")
        else:
            print("[agentphone] no owned number to attach — outbound calls will fail.")
    except Exception as e:
        print(f"[agentphone] number-attach pipeline error: {e}")

    _agent_id = aid
    return _agent_id


def _any_owned_number_id(client) -> Optional[str]:
    """Fallback when AGENTPHONE_FROM_NUMBER doesn't match any owned line:
    just use the first number we own."""
    try:
        listing = client.numbers.list(limit=10)
        items = getattr(listing, "data", None) or getattr(listing, "numbers", None) or []
        for n in items:
            nid = getattr(n, "id", None) or (n.get("id") if isinstance(n, dict) else None)
            if nid:
                return nid
    except Exception as e:
        print(f"[agentphone] numbers.list (fallback) error: {e}")
    return None


def _resolve_from_number_id(client) -> Optional[str]:
    """Map AGENTPHONE_FROM_NUMBER → its AgentPhone number id. Cached. Returns
    None when no from-number is configured or no match is found (the SDK
    will pick a default in that case)."""
    global _from_number_id
    if _from_number_id is not None or not FROM_NUMBER:
        return _from_number_id
    target = re.sub(r"[^\d+]", "", FROM_NUMBER)
    try:
        listing = client.numbers.list(limit=50)
        items = getattr(listing, "data", None) or getattr(listing, "numbers", None) or []
        for n in items:
            num = getattr(n, "phone_number", None) or (n.get("phone_number") if isinstance(n, dict) else None)
            nid = getattr(n, "id", None) or (n.get("id") if isinstance(n, dict) else None)
            if num and nid and re.sub(r"[^\d+]", "", str(num)) == target:
                _from_number_id = nid
                print(f"[agentphone] using from-number {num} id={nid}")
                return nid
    except Exception as e:
        print(f"[agentphone] numbers.list error: {e}")
    print(f"[agentphone] no number match for {FROM_NUMBER!r} — SDK will pick")
    return None


# ── Call state ───────────────────────────────────────────────────────────────

_state_lock = threading.Lock()
_state: dict = {
    "state": STATE_IDLE,
    "to_number": "",
    "callee_name": "",
    "purpose": "",
    "call_id": "",
    "started_at": "",
    "ended_at": "",
    "transcript": [],
    "result": "",        # post-call one-sentence summary
    "available": False,
}

_listener: Optional[Callable[[dict], None]] = None
_active_poll_stop = threading.Event()


def set_listener(cb: Optional[Callable[[dict], None]]) -> None:
    global _listener
    _listener = cb


def get_state() -> dict:
    with _state_lock:
        s = dict(_state)
        s["transcript"] = list(s["transcript"])
        s["available"] = is_available()
        return s


def _notify() -> None:
    cb = _listener
    if cb is None:
        return
    try:
        cb(get_state())
    except Exception as e:
        print(f"[agentphone] listener error: {e}")


def _update_state(**fields) -> None:
    with _state_lock:
        _state.update(fields)
    _notify()


def append_transcript(speaker: str, text: str) -> None:
    """Mirror an utterance into the live-call transcript. Used by live_loop
    while a call is connected, and by stream_transcript poll thread."""
    if not text.strip():
        return
    entry = {
        "speaker": speaker or "?",
        "text": text.strip(),
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    with _state_lock:
        if _state["state"] not in (STATE_DIALING, STATE_RINGING, STATE_CONNECTED):
            return
        # Dedupe consecutive duplicates (the SDK sometimes re-emits).
        if _state["transcript"] and _state["transcript"][-1].get("text") == entry["text"] \
                and _state["transcript"][-1].get("speaker") == entry["speaker"]:
            return
        _state["transcript"].append(entry)
        if len(_state["transcript"]) > 200:
            _state["transcript"] = _state["transcript"][-200:]
    _notify()


# ── Number normalization ─────────────────────────────────────────────────────

_E164_RE = re.compile(r"^\+?\d{7,15}$")


def normalize_number(raw: str) -> str:
    if not raw:
        return DEFAULT_NUMBER
    digits = re.sub(r"[^\d+]", "", raw)
    if not digits:
        return DEFAULT_NUMBER
    if not digits.startswith("+"):
        if len(digits) == 10:
            digits = "+1" + digits
        elif len(digits) == 11 and digits.startswith("1"):
            digits = "+" + digits
        else:
            digits = "+" + digits
    if not _E164_RE.match(digits):
        return DEFAULT_NUMBER
    return digits


# ── Status mapping (AgentPhone → our state machine) ──────────────────────────

# AgentPhone Call.status values seen in the wild: "queued", "ringing",
# "in_progress" / "active" / "ongoing", "completed", "failed", "no_answer",
# "busy", "canceled". We match defensively by substring.
def _map_status(raw: str) -> str:
    s = (raw or "").lower()
    if not s:
        return STATE_DIALING
    if "ring" in s:
        return STATE_RINGING
    if any(k in s for k in ("in_progress", "in-progress", "ongoing", "active", "connected", "answered")):
        return STATE_CONNECTED
    if any(k in s for k in ("complete", "ended", "done", "hung")):
        return STATE_ENDED
    if any(k in s for k in ("fail", "busy", "no_answer", "no-answer", "canceled", "cancelled")):
        return STATE_FAILED
    return STATE_DIALING


# ── Public API ───────────────────────────────────────────────────────────────


def place_call(to_number: str, purpose: str, callee_name: str = "") -> bool:
    """Place an outbound call via AgentPhone, or simulate when SDK / key
    is unavailable. Idempotent — refuses while a call is already non-terminal,
    *unless* the previous call appears stale (60s+ since it transitioned out
    of dialing/ringing), in which case we force-reset and proceed."""
    import time as _t
    with _state_lock:
        cur = _state["state"]
        if cur in (STATE_DIALING, STATE_RINGING, STATE_CONNECTED):
            started_at = _state.get("started_at") or ""
            stale = False
            if started_at:
                try:
                    t0 = datetime.datetime.fromisoformat(started_at).timestamp()
                    if _t.time() - t0 > 120:  # 2 minutes — no real call should be this slow
                        stale = True
                except Exception:
                    stale = True
            if stale:
                print(f"[agentphone] auto-resetting stale {cur!r} state (started_at={started_at})")
                _state.update({
                    "state": STATE_IDLE,
                    "ended_at": datetime.datetime.now().isoformat(timespec="seconds"),
                })
            else:
                print(f"[agentphone] refusing place_call — already in {cur}")
                return False

    number = normalize_number(to_number)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    _update_state(
        state=STATE_DIALING,
        to_number=number,
        callee_name=callee_name or "",
        purpose=purpose or "",
        call_id="",
        started_at=now,
        ended_at="",
        transcript=[],
        result="",
    )
    _arm_summary()
    print(f"[agentphone] place_call -> {number!r} ({callee_name!r}): {purpose!r}")

    client = _get_client()
    if client is None:
        # Simulated lifecycle so the dashboard still demonstrates the beat.
        threading.Thread(target=_simulate, daemon=True).start()
        return True

    threading.Thread(target=_real_place_call_thread, args=(client, number, purpose, callee_name), daemon=True).start()
    return True


def _real_place_call_thread(client, number: str, purpose: str, callee_name: str) -> None:
    """Place the real call and start the polling + transcript threads."""
    agent_id = _resolve_agent_id(client)
    if not agent_id:
        # The user has a key, so falling back to a fake "connected" state
        # would lie to them AND lock the state machine. Surface the failure
        # instead so the dashboard shows the real outcome and the NEXT call
        # can proceed.
        print("[agentphone] no agent resolved (SDK timeout?) — failing the call")
        _update_state(state=STATE_FAILED, ended_at=datetime.datetime.now().isoformat(timespec="seconds"))
        return

    # Skip numbers.list lookup on the hot path — the agent already has a
    # number attached (set during agent setup), and AgentPhone will use it
    # by default. Only fetch from_number_id when an env var pins it.
    pinned_from_id = (os.getenv("AGENTPHONE_FROM_NUMBER_ID") or "").strip()

    make_kwargs = {
        "agent_id": agent_id,
        "to_number": number,
        "system_prompt": DEFAULT_SYSTEM_PROMPT,
        "initial_greeting": DEFAULT_GREETING,
        "variables": {"purpose": purpose or "follow up on the prior call"},
    }
    if pinned_from_id:
        make_kwargs["from_number_id"] = pinned_from_id

    try:
        call = client.calls.make(**make_kwargs)
    except Exception as e:
        print(f"[agentphone] calls.make error: {e}")
        _update_state(state=STATE_FAILED, ended_at=datetime.datetime.now().isoformat(timespec="seconds"))
        return

    call_id = getattr(call, "id", None) or ""
    raw_status = getattr(call, "status", "") or ""
    print(f"[agentphone] call placed id={call_id} status={raw_status!r}")
    _update_state(call_id=call_id, state=_map_status(raw_status) or STATE_RINGING)

    # Start polling + transcript streaming in parallel daemon threads.
    _active_poll_stop.clear()
    threading.Thread(target=_poll_status, args=(client, call_id), daemon=True).start()
    threading.Thread(target=_stream_transcript, args=(client, call_id), daemon=True).start()


def _poll_status(client, call_id: str) -> None:
    """Poll Call.status until terminal. Updates _state accordingly. Also
    mirrors any transcripts attached to the Call object directly — the
    SDK's SSE stream isn't guaranteed to deliver every turn, but
    Call.transcripts grows on each poll."""
    if not call_id:
        return
    last_mapped = ""
    seen_transcript_ids: set = set()
    consecutive_errors = 0
    while not _active_poll_stop.is_set():
        try:
            call = client.calls.get(call_id)
            raw = getattr(call, "status", "") or ""
            mapped = _map_status(raw)
            if mapped != last_mapped:
                print(f"[agentphone] poll {call_id} raw={raw!r} -> {mapped}")
                last_mapped = mapped
                cur = get_state().get("state")
                # The SSE stream is more authoritative — don't downgrade a
                # SSE-driven "connected" back to "ringing" just because
                # Call.status hasn't refreshed. Only propagate terminal
                # transitions and the initial ringing/dialing observation.
                if mapped in (STATE_ENDED, STATE_FAILED) or cur in (STATE_DIALING,):
                    ended_at = ""
                    if mapped in (STATE_ENDED, STATE_FAILED):
                        ended_at = datetime.datetime.now().isoformat(timespec="seconds")
                    _update_state(state=mapped, **({"ended_at": ended_at} if ended_at else {}))
            # Mirror any transcripts the Call object carries. They accumulate
            # as the call progresses; we dedupe by best-effort id/text.
            ts = getattr(call, "transcripts", None) or []
            for t in ts:
                tid = (
                    getattr(t, "id", None)
                    or (t.get("id") if isinstance(t, dict) else None)
                    or f"{getattr(t, 'speaker', '?')}:{getattr(t, 'text', '?')[:40]}"
                )
                if tid in seen_transcript_ids:
                    continue
                seen_transcript_ids.add(tid)
                spkr_raw = (
                    getattr(t, "speaker", None) or getattr(t, "role", None)
                    or (t.get("speaker") if isinstance(t, dict) else None)
                    or (t.get("role") if isinstance(t, dict) else None)
                    or "agent"
                )
                text = (
                    getattr(t, "text", None) or getattr(t, "content", None)
                    or (t.get("text") if isinstance(t, dict) else None)
                    or (t.get("content") if isinstance(t, dict) else None)
                    or ""
                )
                if text:
                    label = "agent" if str(spkr_raw).lower() in ("agent", "assistant", "bot") else "counterparty"
                    append_transcript(label, str(text))
            consecutive_errors = 0
            if mapped in (STATE_ENDED, STATE_FAILED):
                threading.Thread(target=_summarize_and_whisper, daemon=True).start()
                break
        except Exception as e:
            consecutive_errors += 1
            print(f"[agentphone] calls.get error ({consecutive_errors}): {e}")
            if consecutive_errors >= 5:
                print("[agentphone] giving up on poll after 5 errors")
                _update_state(state=STATE_FAILED, ended_at=datetime.datetime.now().isoformat(timespec="seconds"))
                break
        time.sleep(0.5)


def _stream_transcript(client, call_id: str) -> None:
    """Consume AgentPhone's SSE transcript stream. Events arrive as
    ``{"event": "connected"|"turn"|"ended", "data": {...}}``.

    The SSE stream is also the most reliable signal for state transitions —
    AgentPhone's Call.status sticks at "ringing" until terminal, but the
    SSE "connected" event fires when the counterparty actually picks up.
    """
    if not call_id:
        return
    stream_fn = getattr(getattr(client, "calls", None), "stream_transcript", None)
    if stream_fn is None:
        print("[agentphone] no stream_transcript method on SDK")
        return
    print(f"[agentphone] stream_transcript opened for {call_id}")
    try:
        for event in stream_fn(call_id):
            if _active_poll_stop.is_set():
                break
            kind = (event.get("event") if isinstance(event, dict) else None) or "turn"
            data = (event.get("data") if isinstance(event, dict) else None) or {}
            print(f"[agentphone] sse {call_id} event={kind} data_keys={list(data.keys()) if isinstance(data, dict) else type(data).__name__}")
            if kind == "connected":
                # Counterparty picked up.
                _update_state(state=STATE_CONNECTED)
                continue
            if kind == "ended":
                _update_state(state=STATE_ENDED, ended_at=datetime.datetime.now().isoformat(timespec="seconds"))
                _active_poll_stop.set()
                threading.Thread(target=_summarize_and_whisper, daemon=True).start()
                break
            if kind != "turn":
                continue
            speaker = (data.get("speaker") or data.get("role") or "agent")
            text = data.get("text") or data.get("content") or data.get("transcript") or ""
            if text:
                # First transcript turn implicitly confirms the call is live.
                if get_state().get("state") in (STATE_DIALING, STATE_RINGING):
                    _update_state(state=STATE_CONNECTED)
                label = "agent" if str(speaker).lower() in ("agent", "assistant", "bot") else "counterparty"
                append_transcript(label, str(text))
    except Exception as e:
        print(f"[agentphone] stream_transcript error: {e}")


_summary_lock = threading.Lock()
_summary_fired = False


def _summarize_and_whisper() -> None:
    """When a side call ends, summarize what happened and push it as a
    Chief whisper into the main-call dashboard + speaker. Best-effort; fails
    silently."""
    global _summary_fired
    with _summary_lock:
        if _summary_fired:
            return
        _summary_fired = True

    # Final transcript fetch — SSE/poll often miss trailing turns. Use the
    # dedicated calls.get_transcript endpoint (authoritative; same data the
    # AgentPhone web dashboard shows) and REPLACE our local transcript with
    # it rather than trying to merge — the server is the source of truth.
    call_id = get_state().get("call_id") or ""
    client_obj = _get_client()
    if client_obj is not None and call_id:
        try:
            data = client_obj.calls.get_transcript(call_id)
            print(f"[agentphone] final transcript keys={list(data.keys()) if isinstance(data, dict) else type(data).__name__}")
            turns = []
            if isinstance(data, dict):
                turns = data.get("transcript") or data.get("transcripts") or data.get("turns") or data.get("messages") or []
                if not turns and isinstance(data.get("data"), list):
                    turns = data["data"]
            elif isinstance(data, list):
                turns = data
            fresh: list[dict] = []
            for t in turns or []:
                spkr_raw = (
                    (t.get("speaker") if isinstance(t, dict) else None)
                    or (t.get("role") if isinstance(t, dict) else None)
                    or "agent"
                )
                text = (
                    (t.get("text") if isinstance(t, dict) else None)
                    or (t.get("content") if isinstance(t, dict) else None)
                    or (t.get("message") if isinstance(t, dict) else None)
                    or ""
                )
                if not text:
                    continue
                label = "agent" if str(spkr_raw).lower() in ("agent", "assistant", "bot") else "counterparty"
                fresh.append({
                    "speaker": label,
                    "text": str(text).strip(),
                    "at": (t.get("at") if isinstance(t, dict) else None) or datetime.datetime.now().isoformat(timespec="seconds"),
                })
            if fresh:
                print(f"[agentphone] final transcript: {len(fresh)} turn(s) (was {len(get_state().get('transcript') or [])})")
                with _state_lock:
                    _state["transcript"] = fresh
                _notify()
        except Exception as e:
            print(f"[agentphone] final transcript fetch error: {e}")

    s = get_state()
    transcript = s.get("transcript") or []
    purpose = s.get("purpose") or ""
    callee = s.get("callee_name") or s.get("to_number") or "the contact"
    if not transcript:
        whisper_text = f"Side call to {callee} ended — no transcript captured."
    else:
        try:
            import anthropic
            client = anthropic.Anthropic()
            convo_lines = "\n".join(
                f"{t.get('speaker', '?')}: {t.get('text', '')}" for t in transcript[-30:]
            )
            prompt = (
                f"Chief just placed an outbound SIDE CALL to {callee} on Ben's "
                f"behalf while Ben is mid-conversation on a primary call. "
                f"Original task:\n    {purpose!r}\n\n"
                f"Below is the transcript of the side call. Write ONE sentence "
                f"(under 30 words) that Chief will whisper to Ben so he hears "
                f"the result inline with his primary conversation. RULES:\n"
                f"- Lead with the concrete answer, not 'I spoke with...' or 'The call ended.'\n"
                f"- Include specific numbers, names, dates, or commitments if any.\n"
                f"- If the answer is bad news, say it directly — don't soften.\n"
                f"- If the call was inconclusive, say what Ben still needs to follow up on.\n"
                f"- No preamble, no markdown, no quotes around the summary.\n\n"
                f"Transcript:\n{convo_lines}"
            )
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=80,
                messages=[{"role": "user", "content": prompt}],
            )
            whisper_text = "".join(getattr(b, "text", "") for b in resp.content).strip()
            if not whisper_text:
                whisper_text = f"Side call to {callee} ended."
        except Exception as e:
            print(f"[agentphone] summary error: {e}")
            whisper_text = f"Side call to {callee} ended."

    print(f"[agentphone] post-call summary: {whisper_text!r}")
    # Surface the result on the Side Call dashboard itself, in addition to
    # the orb whisper and the main-call whisper trail.
    _update_state(result=whisper_text)
    try:
        from bridge import emit_whisper
        emit_whisper(whisper_text)
    except Exception as e:
        print(f"[agentphone] emit_whisper error: {e}")
    try:
        import main_call
        main_call.push_whisper(whisper_text)
    except Exception as e:
        print(f"[agentphone] main_call.push_whisper error: {e}")
    try:
        from voice.tts import speak
        speak(whisper_text)
    except Exception:
        pass


def _arm_summary() -> None:
    """Reset the dedupe flag so the next call can fire its own summary."""
    global _summary_fired
    with _summary_lock:
        _summary_fired = False


def hang_up() -> None:
    """End the current call. Idempotent."""
    cur = get_state()["state"]
    if cur in (STATE_IDLE, STATE_ENDED, STATE_FAILED):
        return
    _active_poll_stop.set()
    call_id = get_state().get("call_id") or ""
    _update_state(state=STATE_ENDED, ended_at=datetime.datetime.now().isoformat(timespec="seconds"))
    print("[agentphone] call ended (user)")
    threading.Thread(target=_summarize_and_whisper, daemon=True).start()

    client = _get_client()
    if client is None or not call_id:
        return

    def runner():
        try:
            client.calls.end(call_id)
        except Exception as e:
            print(f"[agentphone] calls.end error: {e}")

    threading.Thread(target=runner, daemon=True).start()


def reset_idle() -> None:
    """Move back to idle from a terminal state so the next call can start."""
    cur = get_state()["state"]
    if cur in (STATE_ENDED, STATE_FAILED):
        _active_poll_stop.set()
        _update_state(
            state=STATE_IDLE,
            to_number="",
            callee_name="",
            purpose="",
            call_id="",
            started_at="",
            ended_at="",
            transcript=[],
        )


# ── Simulated fallback ───────────────────────────────────────────────────────

def _simulate() -> None:
    """No-SDK fallback so the dashboard still demonstrates the beat without
    network. Each phase is brief; the user can hang up at any time."""
    time.sleep(1.2)
    if get_state()["state"] != STATE_DIALING:
        return
    _update_state(state=STATE_RINGING)
    time.sleep(2.0)
    if get_state()["state"] != STATE_RINGING:
        return
    _update_state(state=STATE_CONNECTED)
    append_transcript("counterparty", "Hi, this is the front desk. How can I help?")
