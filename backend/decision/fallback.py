"""Deterministic safety-net fallback for make_decision.

WHY THIS EXISTS — read carefully:

This module is a DEMO SAFETY NET, not the real decision logic. It exists for
exactly one purpose: the one demo beat (a present-tense runway-contradiction
whisper) must still fire if the Anthropic call times out, errors, or never
returns. Without this, a slow API roundtrip during the demo means the orb
sits there silent and the audience reads it as broken.

DESIGN RULES — do not relax these:

1. Narrower than the model path. The model fires on any present-tense
   factual contradiction; this only fires on the specific runway-month
   contradiction we have a regex for. If you find yourself adding a second
   rule, stop and ask whether you actually need it for the demo.

2. When in doubt, silent. Every ambiguity returns None (the caller treats
   None as silent). A missed whisper is recoverable on stage. A spurious
   whisper is not.

3. No NL parsing, no embedding lookup, no model call. Plain regex over the
   utterance and the retrieved facts. Cheap, predictable, fully testable
   without the network.

4. Never invoked on the happy path. The caller invokes this only after the
   model has timed out or errored. If the model returns successfully, its
   answer is used as-is — including a model "silent" — without consulting
   the fallback.
"""

import re

# Matches a present-tense assertion of N months of runway in the speaker's
# current utterance. Both "12 months runway" and "12 months of runway".
_RUNWAY_UTTERANCE_RE = re.compile(
    r"\b(\d{1,3})\s*months?\s*(?:of\s+)?runway\b",
    re.IGNORECASE,
)

# Matches the same shape inside a retrieved past-call fact.
_RUNWAY_FACT_RE = re.compile(
    r"\b(?:(\d{1,3})\s*months?\s+runway|runway\s+(\d{1,3})\s*months?)\b",
    re.IGNORECASE,
)

# Plausibility window: anything outside this is almost certainly not a
# real runway statement (probably noise or a misread number).
_MIN_MONTHS = 1
_MAX_MONTHS = 60

# A difference of <2 months is most likely rounding / approximation, not a
# real contradiction. Require a clearer gap before firing.
_MIN_DIFF_MONTHS = 2


def fallback_decide(utterance: str, facts: list[str]) -> dict | None:
    """Return a whisper dict if the narrow runway-contradiction pattern fires,
    else None. None means "not confident — caller should treat as silent."

    Args:
        utterance: the finalized utterance the user just spoke.
        facts: retrieved past-call facts (already filtered by retrieval).

    Returns:
        {"action": "whisper", "content": "..."} or None.
    """
    m = _RUNWAY_UTTERANCE_RE.search(utterance or "")
    if not m:
        return None
    try:
        new_value = int(m.group(1))
    except (TypeError, ValueError):
        return None
    if not (_MIN_MONTHS <= new_value <= _MAX_MONTHS):
        return None

    for fact in facts or []:
        fm = _RUNWAY_FACT_RE.search(fact)
        if not fm:
            continue
        try:
            old_value = int(fm.group(1) or fm.group(2))
        except (TypeError, ValueError):
            continue
        if not (_MIN_MONTHS <= old_value <= _MAX_MONTHS):
            continue
        if abs(old_value - new_value) < _MIN_DIFF_MONTHS:
            continue
        return {
            "action": "whisper",
            "content": (
                f"Past call said {old_value} months runway. "
                f"They just said {new_value}."
            ),
        }
    return None
