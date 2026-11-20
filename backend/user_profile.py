"""User profile — env-backed name + speaker context for prompts.

`USER_NAME` is configured from the Settings dashboard and persisted in `.env`.
The current speaker label comes from voice_id.last_label() and reflects the
most recently classified utterance. Both are stitched into a single
preamble that the belief / decision / action prompts drop in unconditionally
— when neither is set, the preamble is the empty string.
"""

import os
from contextvars import ContextVar

utterance_speaker = ContextVar("utterance_speaker", default=None)


def user_name() -> str:
    return (os.getenv("USER_NAME") or "").strip()


def _speaker_label() -> str:
    """Lazy import so the rest of Chief works if voice_id can't load (e.g.
    numpy missing). Returns '' for unknown."""
    explicit = utterance_speaker.get()
    if explicit is not None:
        return explicit
    try:
        from voice_id import last_label
        return last_label() or ""
    except Exception:
        return ""


def prompt_context() -> str:
    """Multi-line preamble naming the user and the current speaker.

    Written unambiguously so the model can't conflate "the user" with the
    speaker. The principal (whom Chief works for) is named on one line; the
    current speaker is described on a separate line that explicitly says
    whether it's the principal or someone else.
    """
    name = user_name()
    label = _speaker_label()
    lines: list[str] = []
    if name:
        lines.append(f"Chief's principal (the user Chief works for) is {name}.")

    if label == "user":
        if name:
            lines.append(f"The current utterance was spoken by {name} (the principal himself).")
        else:
            lines.append("The current utterance was spoken by the principal (the user Chief works for).")
    elif label == "counterparty":
        if name:
            lines.append(f"The current utterance was spoken by THE COUNTERPARTY (i.e. someone other than {name}, the other side of the call).")
        else:
            lines.append("The current utterance was spoken by THE COUNTERPARTY (i.e. NOT the principal — the other side of the call).")

    if not lines:
        return ""
    return "\n".join(lines) + "\n\n"


# Back-compat alias — earlier callers used `user_preamble`. Keep the name
# working but route to the richer prompt_context so the speaker label flows
# everywhere without each caller having to update.
def user_preamble() -> str:
    return prompt_context()
