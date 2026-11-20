"""Persistent WebSocket bridge to Electron.

Python is the client, Electron the server on :8765. One connection carries:
- Outbound (Python -> Electron): whisper / action_card events
- Inbound  (Electron -> Python): stt_partial / stt_final / inject_canned

Reconnects silently on disconnect. Handlers run in their own threads so they
can call `asyncio.run` (e.g. for Moss retrieval) without nesting event loops.
"""

import asyncio
import json
import os
import threading
import secrets
from typing import Callable

WS_URL = f"ws://127.0.0.1:{os.getenv('CONTEXT_AGENT_BRIDGE_PORT', '8765')}"

_loop: asyncio.AbstractEventLoop | None = None
_outbound: asyncio.Queue | None = None
_handlers: dict[str, Callable[[dict], None]] = {}
_started = False
_lock = threading.Lock()


def on(event_type: str, handler: Callable[[dict], None]) -> None:
    _handlers[event_type] = handler


def emit(event: dict) -> None:
    if _loop is None or _outbound is None:
        return
    _loop.call_soon_threadsafe(_outbound.put_nowait, json.dumps(event))


def emit_whisper(content: str, provenance: dict | None = None) -> None:
    """Emit a whisper to the orb.

    When `provenance` is not supplied, auto-look-up via the active briefing —
    surfaces the source + entity of the most-likely past-call fact that
    backed this whisper. The orb renders this as a dim `↳ memory · …` line
    under the whisper text, so the audience can SEE the contradiction-catch
    drawing on memory.
    """
    if provenance is None:
        provenance = _lookup_provenance(content)
    payload: dict = {"type": "whisper", "content": content}
    if provenance:
        payload["provenance"] = provenance
    emit(payload)


def _lookup_provenance(whisper_content: str) -> dict | None:
    """Best-effort match: look at the active briefing's claims, pick the one
    whose value/source overlaps the whisper most. Returns the rendering
    dict or None when there's nothing usable. Never raises."""
    try:
        from memory.conversation import get_active_briefing  # late import: cycle-safe
        briefing = get_active_briefing()
        if briefing is None or not briefing.claims:
            return None
        text = (whisper_content or "").lower()
        if not text:
            return None
        best = None
        best_score = 0
        for c in briefing.claims:
            value = str(getattr(c, "value", "") or "").lower()
            score = 0
            # Strong signal: the claim's value appears verbatim in the whisper.
            if value and value in text:
                score += 3
            # Weak signals: tokens from the claim source / attribute leak through.
            for tok in (str(getattr(c, "attribute", "") or "")).lower().split("_"):
                if tok and len(tok) > 3 and tok in text:
                    score += 1
            if score > best_score:
                best_score = score
                best = c
        if best is None:
            return None
        date = (getattr(best, "timestamp", "") or "").split("T", 1)[0] or "?"
        source = getattr(best, "source", "") or ""
        # Source format is "<kind>:<id>" — render the id segment for readability.
        source_label = source.split(":", 1)[1] if ":" in source else (source or "memory")
        entity_hint = (getattr(briefing, "counterparty_hint", "") or "").strip()
        return {
            "source": source_label,
            "date": date,
            "entity": entity_hint,
        }
    except Exception as e:
        print(f"[bridge] provenance lookup error: {type(e).__name__}")
        return None


def emit_voice_status(status: dict) -> None:
    emit({"type": "voice_status", **status})


def emit_counterparty_status(status: dict) -> None:
    emit({"type": "counterparty_status", **status})


def emit_retrieval_status(status: dict) -> None:
    emit({"type": "retrieval_status", **status})


def emit_phone_status(status: dict) -> None:
    emit({"type": "phone_status", **status})


def emit_main_call_status(status: dict) -> None:
    emit({"type": "main_call_status", **status})


def emit_memories_list(items: list, available: bool, error: str = "") -> None:
    emit({
        "type": "memories_list",
        "available": available,
        "items": items,
        "error": error,
    })


_email_approvals = {}
_email_cards = {}
_email_approval_lock = threading.Lock()


def consume_email_approval(approval_id):
    if not isinstance(approval_id, str):
        return None
    with _email_approval_lock:
        return _email_approvals.pop(approval_id, None)


def emit_action_card(name: str, inputs: dict, requires_approval: bool = False) -> None:
    approval_id = None
    email_preview_only = False
    if name in {"draft_email", "send_email"}:
        from action.email_agentmail import is_available

        email_preview_only = not (
            is_available() and os.getenv("AGENTMAIL_INBOX_ID") and os.getenv("CONTEXT_AGENT_TEST_RECIPIENT")
        )
        if requires_approval and not email_preview_only:
            exact_inputs = {key: value for key, value in inputs.items() if key != "end_of_call"}
            fingerprint = (name, json.dumps(exact_inputs, sort_keys=True))
            with _email_approval_lock:
                approval_id = _email_cards.get(fingerprint)
                if approval_id is None:
                    if inputs.get("end_of_call"):
                        return
                    approval_id = secrets.token_hex(24)
                    if len(_email_cards) >= 200:
                        oldest = next(iter(_email_cards))
                        _email_approvals.pop(_email_cards.pop(oldest), None)
                    _email_cards[fingerprint] = approval_id
                    _email_approvals[approval_id] = (name, exact_inputs)
                elif approval_id not in _email_approvals:
                    return
    if approval_id and os.getenv("CONTEXT_AGENT_DESKTOP") == "1":
        from memory.transcripts import local_store
        from memory.workspace import track_orb_draft

        action_id = track_orb_draft(local_store(), approval_id, exact_inputs)
        with _email_approval_lock:
            if approval_id in _email_approvals:
                _email_approvals[approval_id][1]["workspace_action_id"] = action_id
    from evaluation.telemetry import record as publish_trace

    publish_trace("action", event_id=approval_id, tool=name if name in {"draft_email", "send_email"} else "other",
                  requires_approval=requires_approval)
    emit({
        "type": "action_card",
        "approval_id": approval_id,
        "email_preview_only": email_preview_only,
        "name": name,
        "inputs": inputs,
        "requires_approval": requires_approval,
    })


def start() -> None:
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_run, daemon=True).start()


def _run() -> None:
    global _loop, _outbound
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    _outbound = asyncio.Queue()
    _loop.run_until_complete(_bridge_forever())


async def _bridge_forever() -> None:
    import websockets
    backoff = 0.5
    while True:
        try:
            token = os.getenv("CONTEXT_AGENT_BRIDGE_TOKEN")
            url = f"{WS_URL}?token={token}" if token else WS_URL
            async with websockets.connect(url, max_size=2**24) as ws:
                backoff = 0.5
                await asyncio.gather(_send_loop(ws), _recv_loop(ws))
        except Exception:
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.5, 5.0)


async def _send_loop(ws) -> None:
    while True:
        msg = await _outbound.get()
        await ws.send(msg)


async def _recv_loop(ws) -> None:
    async for msg in ws:
        try:
            event = json.loads(msg)
        except Exception:
            continue
        handler = _handlers.get(event.get("type"))
        if handler is None:
            continue
        # Fire in a thread so handlers can use asyncio.run (e.g. Moss) freely.
        threading.Thread(target=_safe_call, args=(handler, event), daemon=True).start()


def _safe_call(handler: Callable[[dict], None], event: dict) -> None:
    try:
        handler(event)
    except Exception as e:
        print(f"[bridge] handler error for {event.get('type')!r}: {type(e).__name__}")
