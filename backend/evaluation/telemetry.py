"""Send allowlisted operational metadata to LangSmith when explicitly enabled."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import lru_cache
import os
from threading import BoundedSemaphore
from uuid import NAMESPACE_URL, uuid4, uuid5

_FIELDS = {
    "transcript": {"operation", "ok", "error_kind", "duplicate"},
    "retrieval": {"count", "mode"},
    "chat": {"mode", "count", "ok"},
    "decision": {"action", "reason", "retrieved_count", "imported_count"},
    "action": {"tool", "requires_approval"},
    "approval": {"outcome"},
    "execution": {"state"},
    "feedback": {"rating", "preference_changed"},
}
_VALUES = {
    "operation": {"import", "list", "preview", "delete"},
    "error_kind": {"ValueError", "store"},
    "mode": {"local", "claude"},
    "action": {"silent", "whisper"},
    "reason": {"fallback-fired-after-timeout", "timeout-silent", "fallback-fired-after-error",
               "silent-by-model-error", "silent-by-malformed", "whisper", "silent-by-model"},
    "tool": {"draft_email", "send_email", "other"},
    "outcome": {"approved", "discarded", "missing_or_consumed"},
    "state": {"preview", "unavailable", "rejected", "unconfirmed", "accepted", "verified"},
    "rating": {"helpful", "unhelpful", "incorrect", "unnecessary", "wrong_action", "wrong_memory", "wrong_attribution"},
}
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="telemetry")
_pending = BoundedSemaphore(32)


@lru_cache(maxsize=1)
def client():
    from langsmith import Client

    return Client(timeout_ms=4000, auto_batch_tracing=False)


def _publish(event, run_id, metadata):
    try:
        now = datetime.now(timezone.utc)
        client().create_run(
            name=f"context-to-action.{event}", run_type="chain", id=run_id,
            inputs={}, outputs=metadata, project_name=os.getenv("LANGSMITH_PROJECT", "context-to-action"),
            start_time=now, end_time=now,
        )
    except Exception as error:
        print(f"[telemetry] LangSmith publish failed: {type(error).__name__}")
    finally:
        _pending.release()


def record(event, *, event_id=None, **fields):
    if os.getenv("CONTEXT_AGENT_TRACE_ENABLED") != "1" or not os.getenv("LANGSMITH_API_KEY"):
        return None
    if event not in _FIELDS:
        return None
    metadata = {}
    for key in _FIELDS[event]:
        value = fields.get(key)
        if key in _VALUES:
            if isinstance(value, str) and value in _VALUES[key]:
                metadata[key] = value
        elif type(value) in (bool, int):
            metadata[key] = value
    run_id = uuid5(NAMESPACE_URL, f"context-to-action:{event}:{event_id or uuid4()}")
    if not _pending.acquire(blocking=False):
        print("[telemetry] LangSmith queue full")
        return None
    _executor.submit(_publish, event, run_id, metadata)
    return str(run_id)
