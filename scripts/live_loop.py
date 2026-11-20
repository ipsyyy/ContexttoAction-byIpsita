#!/usr/bin/env python3
"""Live entry point — spawned by Electron's main process.

Initializes Moss, connects to the Electron WS bridge, and routes inbound
stt_partial / stt_final / inject_canned / approve_action events into the
canonical ears hooks.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from dotenv import load_dotenv
load_dotenv()

import asyncio
import time

import threading

import datetime

import bridge
from evaluation.telemetry import record as publish_trace
from memory.retrieval import init_moss
from ears.hooks import on_partial_utterance, on_utterance_final, set_paused
from ears.fallback import inject_canned_utterance
from ears.stt_elevenlabs import transcribe_pcm16_b64


# ── Conversation lifecycle ───────────────────────────────────────────────────
#
# A "conversation" starts on the first utterance after a long silence and ends
# when no utterance has arrived for IDLE_END_SECONDS. start_conversation()
# hydrates Supermemory; end_conversation() consolidates back. The session
# summary written at end_conversation() gives the Dashboard's memory list
# something to accumulate even when no in-call claim extraction is wired up.

# How long of silence ends a conversation session for consolidation. Short
# enough that a natural pause between topics flushes claims to Supermemory
# (so the next utterance benefits from them) but long enough that mid-
# sentence thinking pauses don't accidentally fire.
IDLE_END_SECONDS = 12.0

_conv_lock = threading.Lock()
_conv_active = False
_conv_id: str | None = None
_session_rows: list[str] = []
_mic_generation = 0
_mic_active = False
_idle_timer: threading.Timer | None = None
_counterparty_hint: str | None = (os.getenv("DEFAULT_COUNTERPARTY") or "").strip() or None
# Pre-seeded from .env so the demo opens with a known counterparty
# (e.g. "Marcus Chen") without the user touching Settings. Auto-detection
# from utterances overrides if a different alias appears.


def _start_conv_if_needed(row: str | None = None, generation: int | None = None) -> bool:
    """Call once per utterance. Starts a conversation on the first utterance
    after idle; resets the idle timer on every utterance."""
    global _conv_active, _conv_id, _idle_timer

    with _conv_lock:
        if generation is not None and (not _mic_active or generation != _mic_generation):
            return False
        first_in_session = not _conv_active
        if first_in_session:
            _conv_id = datetime.datetime.now().strftime("session-%Y%m%dT%H%M%S-%f")
            _conv_active = True

        if _idle_timer is not None:
            _idle_timer.cancel()
        _idle_timer = threading.Timer(IDLE_END_SECONDS, _on_idle_end)
        _idle_timer.daemon = True
        _idle_timer.start()
        conv_id = _conv_id
        if row is not None:
            _session_rows.append(row)

    if first_in_session:
        # Off-thread: priming may round-trip to Supermemory.
        hint = _counterparty_hint
        threading.Thread(
            target=_start_conversation_bg, args=(conv_id, hint), daemon=True,
        ).start()
    return True


def _start_conversation_bg(conv_id: str, hint: str | None) -> None:
    try:
        from memory.conversation import start_conversation
        import main_call
        main_call.conversation_started(conv_id, hint or "")
        briefing = start_conversation(counterparty_hint=hint, conversation_id=conv_id)
        _emit_counterparty_status_safe(briefing=briefing)
        try:
            from memory.claims import UNRESOLVED_ENTITY
            main_call.set_briefing(
                entity_id=briefing.entity_id,
                resolved=(briefing.entity_id != UNRESOLVED_ENTITY),
                hydrated_claims=len(briefing.claims),
                hint=hint or "",
            )
        except Exception as e:
            print(f"[live] main_call.set_briefing error: {type(e).__name__}")
    except Exception as e:
        print(f"[live] start_conversation error: {type(e).__name__}")


def _maybe_auto_detect_counterparty(text: str) -> None:
    """Auto-set the counterparty hint when a known alias appears in an
    utterance and no hint is set yet. Lets the demo skip the manual
    Dashboard step — judges just talk and Chief recognizes who they're
    talking to."""
    global _counterparty_hint
    if _counterparty_hint:
        return
    if not text or not text.strip():
        return
    try:
        from memory.claims import all_aliases
        lower = " " + text.lower() + " "
        best_canonical: str | None = None
        best_alias: str = ""
        for canonical, aliases in all_aliases().items():
            for alias in aliases:
                # Use word-boundary-ish matching so "marcus" in "Marcus" hits
                # but "chen" doesn't match "chenille". Aliases are already
                # all-lowercase per register_alias.
                token = " " + alias + " "
                if token in lower and len(alias) > len(best_alias):
                    best_alias = alias
                    best_canonical = canonical
        if best_canonical is None:
            return
        # Use the longest alias as a readable hint.
        from memory.claims import all_aliases as _ag
        readable = max(_ag()[best_canonical], key=len)
        _counterparty_hint = readable
        print("[live] auto-detected a counterparty")

        with _conv_lock:
            conv_id = _conv_id
            active = _conv_active
        if active and conv_id:
            threading.Thread(
                target=_start_conversation_bg, args=(conv_id, readable), daemon=True,
            ).start()
        else:
            _emit_counterparty_status_safe()
    except Exception as e:
        print(f"[live] auto-detect counterparty error: {type(e).__name__}")


def _emit_counterparty_status_safe(briefing=None) -> None:
    """Push the current counterparty + hydration state to the Dashboard."""
    try:
        from memory.claims import UNRESOLVED_ENTITY
        from memory.conversation import get_active_briefing
        b = briefing if briefing is not None else get_active_briefing()
        if b is None:
            status = {
                "hint": _counterparty_hint or "",
                "entity_id": "",
                "resolved": False,
                "hydrated_claims": 0,
                "session_active": False,
            }
        else:
            status = {
                "hint": b.counterparty_hint or "",
                "entity_id": b.entity_id,
                "resolved": b.entity_id != UNRESOLVED_ENTITY,
                "hydrated_claims": len(b.claims),
                "session_active": True,
            }
        bridge.emit_counterparty_status(status)
    except Exception as e:
        print(f"[live] emit counterparty status error: {type(e).__name__}")


def _on_idle_end() -> None:
    finish_session(idle_timer=threading.current_thread())


def finish_session(idle_timer: threading.Thread | None = None) -> None:
    """Detach the session's captured rows before background persistence."""
    global _conv_active, _conv_id
    with _conv_lock:
        if not _conv_active or (idle_timer is not None and idle_timer is not _idle_timer):
            return
        conv_id = _conv_id
        if _idle_timer is not None:
            _idle_timer.cancel()
        _conv_active = False
        _conv_id = None
        rows = tuple(_session_rows)
        _session_rows.clear()
    try:
        import main_call
        main_call.conversation_ended(conv_id)
    except Exception as error:
        print(f"[live] Could not update session state: {type(error).__name__}")
    threading.Thread(target=_end_conversation_bg, args=(conv_id, rows), daemon=True).start()


def save_session(conv_id: str, rows: tuple[str, ...]) -> None:
    """Make finalized live speech searchable in the same library as imports."""
    from memory.transcripts import MAX_BYTES, local_store

    from memory.insights import queue

    store = local_store()

    def save(filename, text):
        source = store.import_text(filename, text)
        queue(source["id"])
    parts = []
    size = 0
    number = 1
    for row in rows:
        encoded = row.encode("utf-8")
        if len(encoded) > MAX_BYTES:
            print("[live] An unusually large utterance exceeded the transcript limit")
            continue
        if size + len(encoded) > MAX_BYTES:
            save(f"Live {conv_id} ({number}).txt", "".join(parts))
            number += 1
            parts, size = [], 0
        parts.append(row)
        size += len(encoded)
    if parts:
        save(f"Live {conv_id} ({number}).txt", "".join(parts))


def _end_conversation_bg(conv_id: str | None, rows: tuple[str, ...] = ()) -> None:
    """Consolidate L1 buffer + push a session-summary claim so the dashboard
    accumulates something visible across sessions. Also fires the Phase 6
    hang-up flush so any pending / approved-but-not-executed action cards
    are replayed before the session closes — the resolution of the second
    demo beat."""
    if conv_id and rows:
        try:
            save_session(conv_id, rows)
        except Exception as error:
            print(f"[live] Local session save failed: {type(error).__name__}")
    try:
        from memory.conversation import end_conversation
        consolidated = end_conversation()
        print(f"[live] consolidated {len(consolidated)} claims for {conv_id}")
    except Exception as e:
        print(f"[live] end_conversation error: {type(e).__name__}")
        consolidated = ()

    # Phase 6 — replay queued action cards. Idempotent + degrades to no-op
    # on any failure; will never raise into this background thread.
    try:
        from action import hangup
        hangup.end_call()
    except Exception as e:
        print(f"[live] hangup.end_call error: {type(e).__name__}")

    # Session summary: take the current belief and persist it as one claim so
    # there's a durable trace of the call even without per-utterance claim
    # extraction. Best-effort.
    try:
        from memory.belief import get_belief, reset_belief
        from memory.claims import Claim
        from memory import supermemory_store
        belief = (get_belief() or "").strip()
        if belief and belief != "Conversation has not started." and supermemory_store.is_available():
            claim = Claim(
                entity_id="session",
                attribute="summary",
                value=belief,
                source=f"live:{conv_id}" if conv_id else "live",
                timestamp=datetime.datetime.now().isoformat(timespec="seconds"),
                conversation_id=conv_id,
                confidence=1.0,
            )
            if supermemory_store.add_claim(claim):
                print(f"[live] wrote session summary for {conv_id}")
        # Reset belief at session boundary so the next call starts fresh.
        reset_belief()
    except Exception as e:
        print(f"[live] session-summary error: {type(e).__name__}")


def handle_stt_partial(event):
    text = event.get("text", "")
    if text:
        on_partial_utterance(text)


def handle_stt_final(event):
    text = event.get("text", "")
    if text:
        print("[stt] finalized typed input")
        _maybe_auto_detect_counterparty(text)
        _start_conv_if_needed()
        on_utterance_final(text)


def handle_audio_utterance(event):
    """Renderer sent a full utterance as base64 PCM16. Classify the speaker
    against the stored voiceprint, then transcribe via ElevenLabs."""
    audio_b64 = event.get("audio", "")
    sample_rate = int(event.get("sample_rate", 16000))
    if not audio_b64:
        return
    with _conv_lock:
        if not _mic_active:
            return
        generation = _mic_generation

    def runner():
        # Speaker classification runs first so the label is set on
        # user_profile.prompt_context() before belief/decision/action read it.
        # Failures degrade to 'unknown' silently — Chief still works without
        # speaker context, just less informed.
        speaker = "unknown"
        try:
            import voice_id

            speaker = voice_id.classify_pcm16_b64(audio_b64, sample_rate)
        except Exception as e:
            print(f"[live] voice_id classify error: {type(e).__name__}")

        text = transcribe_pcm16_b64(audio_b64, sample_rate)
        if text:
            print("[stt] finalized microphone input")
            stamp = datetime.datetime.now().isoformat(timespec="seconds")
            if not _start_conv_if_needed(f"[{stamp}] {speaker}: {text}\n", generation):
                return
            try:
                from action import phone_agentphone
                import main_call
                # Side-call transcript mirror — only writes when a side call
                # is actively dialing/ringing/connected.
                phone_agentphone.append_transcript(speaker, text)
                # Primary-call transcript — always streams.
                main_call.push_utterance(speaker, text)
            except Exception:
                pass
            from user_profile import utterance_speaker
            token = utterance_speaker.set(speaker)
            try:
                on_utterance_final(text)
            finally:
                utterance_speaker.reset(token)

    threading.Thread(target=runner, daemon=True).start()


def handle_pause(_event):
    global _mic_active, _mic_generation
    with _conv_lock:
        _mic_active = False
        _mic_generation += 1
    set_paused(True)
    finish_session()
    print("[live] paused")
    try:
        import main_call
        main_call.set_paused(True)
    except Exception as e:
        print(f"[live] main_call.set_paused error: {type(e).__name__}")


def handle_resume(_event):
    global _mic_active
    with _conv_lock:
        _mic_active = True
    set_paused(False)
    print("[live] resumed")
    try:
        import main_call
        main_call.set_paused(False)
    except Exception as e:
        print(f"[live] main_call.set_paused error: {type(e).__name__}")


def handle_inject_canned(_event):
    if os.getenv("CONTEXT_AGENT_DESKTOP") == "1":
        from memory.past_calls import EXPECTED_WHISPER
        bridge.emit_whisper(EXPECTED_WHISPER, provenance={"source": "Sample alert"})
        return
    _start_conv_if_needed()
    inject_canned_utterance()


def handle_voice_status_request(_event):
    import voice_id
    bridge.emit_voice_status(voice_id.status())


def handle_enroll_voice(event):
    """Settings sent a base64 PCM16 calibration clip. Build a voiceprint
    and emit the new status back to the dashboard."""
    import voice_id
    audio_b64 = event.get("audio", "")
    sample_rate = int(event.get("sample_rate", 16000))

    def runner():
        if not audio_b64:
            bridge.emit_voice_status(voice_id.status())
            return
        new_status = voice_id.enroll(audio_b64, sample_rate)
        bridge.emit_voice_status(new_status)

    threading.Thread(target=runner, daemon=True).start()


def handle_set_counterparty(event):
    """Dashboard told us who is on the call. Update the hint and, if a session
    is already active, re-prime in place so the briefing reflects the new
    identity immediately."""
    global _counterparty_hint
    hint = (event.get("hint") or "").strip() or None
    _counterparty_hint = hint
    print("[live] updated counterparty hint")

    with _conv_lock:
        active = _conv_active
        conv_id = _conv_id

    if active and conv_id:
        # Re-prime in place. start_conversation() overwrites the active briefing.
        threading.Thread(
            target=_start_conversation_bg, args=(conv_id, hint), daemon=True,
        ).start()
    else:
        # No active session yet — just report the staged hint.
        _emit_counterparty_status_safe()


def handle_counterparty_status_request(_event):
    _emit_counterparty_status_safe()


def handle_retrieval_status_request(_event):
    try:
        from memory.retrieval import get_last_query_meta
        bridge.emit_retrieval_status(get_last_query_meta())
    except Exception as e:
        print(f"[live] retrieval_status error: {type(e).__name__}")


def handle_phone_status_request(_event):
    from action import phone_agentphone
    bridge.emit_phone_status(phone_agentphone.get_state())


def handle_main_call_status_request(_event):
    import main_call
    bridge.emit_main_call_status(main_call.get_state())


def handle_main_call_end(_event):
    finish_session()


def handle_main_call_reset(_event):
    import main_call
    main_call.reset_to_idle()


def handle_phone_hangup(_event):
    from action import phone_agentphone
    phone_agentphone.hang_up()


def handle_phone_reset(_event):
    from action import phone_agentphone
    phone_agentphone.reset_idle()


def handle_clear_voice(_event):
    import voice_id
    bridge.emit_voice_status(voice_id.clear())


def handle_list_memories(event):
    """Settings dashboard requested a list of Supermemory docs. Reply with a
    `memories_list` event. Always off the hot path."""
    from memory import supermemory_store

    limit = int(event.get("limit") or 50)

    def runner():
        if not supermemory_store.is_available():
            bridge.emit_memories_list(
                [], available=False,
                error="SUPERMEMORY_API_KEY not set or SDK unavailable",
            )
            return
        items = supermemory_store.list_recent(limit=limit)
        bridge.emit_memories_list(items, available=True)

    threading.Thread(target=runner, daemon=True).start()


def handle_approve_action(event):
    approval_id = event.get("approval_id")
    approved = bridge.consume_email_approval(approval_id)
    if approved is None:
        publish_trace("approval", outcome="missing_or_consumed")
        return
    publish_trace("approval", event_id=approval_id, outcome="approved")
    name, inputs = approved
    from action import email_agentmail, hangup

    action_id = inputs.pop("workspace_action_id", None)
    if action_id:
        from memory.transcripts import local_store
        from memory.workspace import approve_email

        try:
            outcome = approve_email(local_store(), action_id, approval_id)
        except ValueError:
            bridge.emit({"type": "email_outcome", "approval_id": approval_id, "state": "rejected",
                         "message": "This draft changed or was already handled. Review it in Actions."})
            return
        hangup.mark_executed(name, inputs)
        bridge.emit({"type": "email_outcome", "approval_id": approval_id, **outcome})
        return

    hangup.mark_approved(name, inputs)
    outcome = email_agentmail.send_verified(
        to=inputs.get("to"),
        subject=inputs.get("subject") or "",
        body=inputs.get("body") or "",
        approval_id=approval_id,
    )
    publish_trace("execution", event_id=approval_id, state=outcome["state"])
    hangup.mark_executed(name, inputs)
    bridge.emit({"type": "email_outcome", "approval_id": approval_id, **outcome})


def handle_dismiss_action(event):
    """Optional: UI can emit `dismiss_action` to mark a card explicitly
    dismissed. Dismissed entries are never replayed by end_call."""
    approval = bridge.consume_email_approval(event.get("approval_id"))
    if not approval:
        return
    publish_trace("approval", event_id=event["approval_id"], outcome="discarded")
    name, inputs = approval
    from action import hangup
    action_id = inputs.pop("workspace_action_id", None)
    if action_id:
        from memory.transcripts import local_store
        from memory.workspace import cancel_review

        cancel_review(local_store(), action_id, event["approval_id"])
    hangup.mark_dismissed(name, inputs)
    print(f"[dismiss] {name} dismissed")


def handle_end_call(_event):
    """Phase 6 hang-up flush. Replays any pending or approved-pending
    actions through the existing emit_action_card path. Idempotent.

    Wiring point (Electron side, not yet wired):
        // electron/main.js or tray menu: when the user signals call end,
        //   sendToPython({ type: 'end_call' })
    Until then, this handler still works if any other code path emits a
    bridge event of type `end_call`."""
    from action import hangup
    count = hangup.end_call()
    print(f"[hangup] end_call replayed {count} action(s)")


def handle_transcripts(event):
    from memory.transcripts import local_store
    from memory.insights import queue, status

    store = local_store()
    reply = {"type": "transcript_result", "id": event["id"]}
    operation = event.get("operation")
    error_kind = None
    try:
        if operation == "import":
            result = store.import_text(event["filename"], event["text"])
            queue(result["id"])
            result["analysis_state"] = status(store, result["id"])["state"]
        elif operation == "chat":
            from memory.chat import answer_question

            question = event.get("question", "").strip()
            if not question or len(question) > 2000:
                raise ValueError("Enter a question up to 2,000 characters.")
            result = answer_question(store, question, event.get("use_model") is True)
            result["id"] = event["id"]
            mode = result["mode"]
            count = len(result["sources"])
            publish_trace("retrieval", event_id=event["id"], mode=mode, count=count)
            result["trace_id"] = publish_trace("chat", event_id=event["id"], mode=mode, count=count, ok=True)
        elif operation == "feedback":
            from memory.feedback import save_feedback

            result = {"id": save_feedback(
                event.get("kind", "chat"), event["rating"], event.get("source_ids", []),
                event.get("correction", ""), event.get("preference"), reference=event.get("reference"),
            )}
            publish_trace(
                "feedback", event_id=result["id"], rating=event["rating"],
                preference_changed=event.get("preference") is not None,
            )
        elif operation == "email_draft":
            subject = event.get("subject", "").strip()
            body = event.get("body", "").strip()
            if not subject or len(subject) > 200 or not body or len(body) > 8000:
                raise ValueError("Provide a subject and a draft body.")
            sources = [
                source["filename"] for source_id in event.get("source_ids", [])[:8]
                if (source := store.get(source_id))
            ]
            to = os.getenv("CONTEXT_AGENT_TEST_RECIPIENT", "").strip()
            bridge.emit_action_card("draft_email", {
                "to": to, "subject": subject, "body": body, "sources": sources,
            }, requires_approval=True)
            result = {"state": "draft", "review": "Review the draft and its sources in the orb."}
        elif operation in {"workspace", "fact_check", "review_saved", "cancel_review", "approve_saved"}:
            from memory import workspace

            if operation == "workspace":
                result = workspace.workspace_snapshot(store)
            elif operation == "fact_check":
                result = workspace.fact_check(store, event["source_id"], event["insight_id"])
            elif operation == "review_saved":
                result = workspace.review_email(store, event["action_id"], event["recipient"], event["subject"], event["body"])
            elif operation == "cancel_review":
                workspace.cancel_review(store, event["action_id"], event["token"])
                result = True
            else:
                result = workspace.approve_email(store, event["action_id"], event["token"])
        elif operation == "list":
            result = store.list_sources()
        elif operation == "preview":
            result = store.get(event["source_id"])
            if result:
                result["analysis"] = status(store, result["id"])
        elif operation == "retry_analysis":
            result = queue(event["source_id"], retry=True)
        elif operation == "delete":
            result = store.delete(event["source_id"])
        else:
            raise ValueError("Unknown transcript operation.")
        reply.update(ok=True, result=result)
    except ValueError as error:
        error_kind = "ValueError"
        reply.update(ok=False, error=str(error))
    except Exception as error:
        error_kind = "store"
        print(f"[transcripts] Store failed: {type(error).__name__}")
        reply.update(ok=False, error="Could not access the local transcript library.")
    if operation in {"import", "preview", "list", "delete"}:
        publish_trace("transcript", event_id=event["id"], operation=operation, ok=reply["ok"],
                      error_kind=error_kind, duplicate=reply.get("result", {}).get("duplicate") if operation == "import" else None)
    bridge.emit(reply)


if __name__ == "__main__":
    from memory.insights import recover_pending
    from memory.workspace import recover_sending
    recover_pending()
    recover_sending()

    print("[live] initializing Moss...")
    try:
        asyncio.run(init_moss())
    except Exception as e:
        print(f"[live] Moss init skipped: {type(e).__name__}")
    print("[live] registering bridge handlers...")
    bridge.on("transcripts", handle_transcripts)
    bridge.on("stt_partial", handle_stt_partial)
    bridge.on("stt_final", handle_stt_final)
    bridge.on("audio_utterance", handle_audio_utterance)
    bridge.on("pause", handle_pause)
    bridge.on("resume", handle_resume)
    bridge.on("inject_canned", handle_inject_canned)
    bridge.on("approve_action", handle_approve_action)
    bridge.on("dismiss_action", handle_dismiss_action)
    bridge.on("end_call", handle_end_call)
    bridge.on("list_memories", handle_list_memories)
    bridge.on("voice_status_request", handle_voice_status_request)
    bridge.on("enroll_voice", handle_enroll_voice)
    bridge.on("clear_voice", handle_clear_voice)
    bridge.on("set_counterparty", handle_set_counterparty)
    bridge.on("counterparty_status_request", handle_counterparty_status_request)
    bridge.on("retrieval_status_request", handle_retrieval_status_request)
    bridge.on("phone_status_request", handle_phone_status_request)
    bridge.on("phone_hangup", handle_phone_hangup)
    bridge.on("phone_reset", handle_phone_reset)
    bridge.on("main_call_status_request", handle_main_call_status_request)
    bridge.on("main_call_end", handle_main_call_end)
    bridge.on("main_call_reset", handle_main_call_reset)

    # Primary-call listener — every state transition is pushed to the Main
    # Call Dashboard window.
    try:
        import main_call
        main_call.set_listener(bridge.emit_main_call_status)
    except Exception as e:
        print(f"[live] main_call listener wire error: {type(e).__name__}")

    # AgentPhone listener — every state transition is pushed to the live-call
    # dashboard window so the UI follows the call without needing to poll.
    try:
        from action import phone_agentphone
        phone_agentphone.set_listener(bridge.emit_phone_status)
    except Exception as e:
        print(f"[live] phone listener wire error: {type(e).__name__}")
    print("[live] connecting to the workspace bridge")
    bridge.start()
    print("[live] ready. Awaiting events.")

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("[live] shutting down.")
