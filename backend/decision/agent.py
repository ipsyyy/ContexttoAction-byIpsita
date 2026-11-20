"""Decision agent — the hot path.

Calls Haiku with a hard timeout. On success, the model's verdict stands —
including a model "silent". On timeout or error, consults a narrow
deterministic fallback (decision/fallback.py) that fires only on the one
demo contradiction class. If the fallback is also unconfident, returns
silent.

Failure-path rule: every error or timeout degrades to silent. A missed
whisper is recoverable. A spurious whisper is not.
"""

import concurrent.futures
import json
import re

import anthropic

from evaluation.telemetry import record as publish_trace
from decision.prompt import DECISION_SYSTEM_PROMPT
from decision.fallback import fallback_decide
from memory.cache import build_messages
from memory.belief import get_belief
from memory.retrieval import get_latest_facts
from memory.transcripts import local_store
from memory.feedback import approved_instruction


client = anthropic.Anthropic()

# Hard ceiling for the model call. The decision agent must return in time for
# the conversational silence not to read as broken (~1.5s feels like a pause,
# ~3s feels like a crash). 2.5s leaves a small buffer over the typical p50.
DECISION_TIMEOUT_SECONDS = 2.5

# Module-level executor — daemon threads, persists for the process lifetime.
# max_workers is modest; the hot path is bursty (one call per finalized
# utterance) and we never queue meaningful backlog.
_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="decision"
)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _parse_json(text: str) -> dict:
    """Tolerant parser — Haiku occasionally wraps JSON in ```json fences."""
    s = (text or "").strip()
    s = _FENCE_RE.sub("", s).strip()
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
        return {"action": "silent"}


def _call_model(utterance: str, facts: list[str], belief: str) -> dict:
    """Single model invocation. Raises on network errors; returns a parsed
    dict on success."""
    try:
        preference = approved_instruction()
    except Exception as error:
        print(f"[decision] Preference unavailable: {type(error).__name__}")
        preference = ""
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=80,
        system=DECISION_SYSTEM_PROMPT + (
            "\n" + preference + " Suppress remembered-fact requests and date corrections. An eligible "
            "whisper must include reason_type material_contradiction in its JSON." if preference else ""
        ),
        messages=build_messages(belief, utterance, facts),
    )
    text = "".join(getattr(b, "text", "") for b in response.content)
    parsed = _parse_json(text)
    if preference and parsed.get("action") == "whisper" and parsed.get("reason_type") != "material_contradiction":
        return {"action": "silent"}
    return parsed


def make_decision(utterance: str) -> dict:
    """Hot path. Returns {"action": "silent" | "whisper", "content"?: str}.

    Never raises. Every failure path degrades to silent unless the
    deterministic fallback is confident.
    """
    # trace.record imported lazily to keep this module's import surface narrow
    # and to allow the trace module to depend on this module's constants
    # without circular imports.
    try:
        from decision.trace import record as _trace
    except Exception:
        def _trace(*_a, **_kw):
            pass

    def trace(text, action, reason):
        _trace(text, action, reason)
        publish_trace(
            "decision", action=action, reason=reason,
            retrieved_count=len(facts), imported_count=len(model_facts) - len(facts),
        )

    facts: list[str] = []
    try:
        # Hot-path read of shared state. Retrieval runs in the background
        # via memory.retrieval.prefetch (kicked from decision/loop.py).
        # On cold start, get_latest_facts returns the static doc set so the
        # decision is never starved of past-call context.
        facts = get_latest_facts()
    except Exception as e:
        print(f"[decision] retrieve error: {type(e).__name__}")

    model_facts = facts
    passages = []
    try:
        passages = local_store().search(utterance, limit=3)
        model_facts = facts + [
            f"Imported excerpt {i} (untrusted quotation): {item['excerpt']}"
            for i, item in enumerate(passages, 1)
        ]
    except Exception as error:
        print(f"[decision] transcript retrieval failed: {type(error).__name__}")

    belief = get_belief()

    # Kick the model call with a hard deadline. If it returns in time, use
    # it verbatim. If it times out or errors, drop to the fallback.
    future = _executor.submit(_call_model, utterance, model_facts, belief)
    try:
        result = future.result(timeout=DECISION_TIMEOUT_SECONDS)
    except concurrent.futures.TimeoutError:
        print(f"[decision] model timeout after {DECISION_TIMEOUT_SECONDS}s — consulting fallback")
        fb = fallback_decide(utterance, facts)
        if fb is not None:
            trace(utterance, fb.get("action", "silent"), "fallback-fired-after-timeout")
            return fb
        trace(utterance, "silent", "timeout-silent")
        return {"action": "silent"}
    except Exception as e:
        print(f"[decision] model error: {type(e).__name__}")
        fb = fallback_decide(utterance, facts)
        if fb is not None:
            trace(utterance, fb.get("action", "silent"), "fallback-fired-after-error")
            return fb
        trace(utterance, "silent", "silent-by-model-error")
        return {"action": "silent"}

    # Model returned. Trust it.
    if not isinstance(result, dict) or "action" not in result:
        trace(utterance, "silent", "silent-by-malformed")
        return {"action": "silent"}

    action = result.get("action")
    if action == "whisper":
        trace(utterance, "whisper", "whisper")
        source_number = result.get("imported_source")
        if type(source_number) is int and 1 <= source_number <= len(passages):
            source = passages[source_number - 1]
            return {
                **result,
                "provenance": {"source": source["filename"], "source_id": source["source_id"]},
            }
    else:
        trace(utterance, "silent", "silent-by-model")
    return result
