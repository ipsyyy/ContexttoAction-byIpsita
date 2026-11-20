"""Belief updater — streaming Haiku, debounced.

STT partials fire every 20-100ms. Firing a Claude call on each will instantly
exhaust RPM. We debounce: schedule an update when 1.5s has elapsed since the
last fire OR the text has grown by 10+ words. A trailing timer ensures the last
partial in a burst still lands.
"""

import datetime
import threading
import time

import anthropic

from user_profile import user_preamble

client = anthropic.Anthropic()

BELIEF_SYSTEM_PROMPT = """
You maintain a running belief about what is happening in the current conversation.

Your belief is a single short paragraph: who is speaking, what they want, what the current moment is about, what tension or question is live right now.

Update it on each new utterance. Overwrite the previous belief entirely.
Output only the updated belief. No explanation.
""".strip()


current_belief: str = "Conversation has not started."
_belief_lock = threading.Lock()

DEBOUNCE_INTERVAL = 1.5  # seconds
WORD_DELTA = 10

_debounce_lock = threading.Lock()
_last_run_at: float = 0.0
_last_word_count: int = 0
_pending_text: str = ""
_pending_timer: threading.Timer | None = None


def update_belief(text: str) -> None:
    global current_belief
    if not text.strip():
        return
    try:
        with client.messages.stream(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=BELIEF_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"{user_preamble()}"
                        f"Today: {datetime.date.today().isoformat()}\n\n"
                        f"Previous belief:\n{current_belief}\n\n"
                        f"New utterance (may be partial):\n{text}"
                    ),
                }
            ],
        ) as stream:
            new_belief = stream.get_final_text()
        if new_belief:
            with _belief_lock:
                current_belief = new_belief.strip()
    except Exception as e:
        print(f"[belief] update error: {type(e).__name__}")


def on_partial_utterance(text: str) -> None:
    """Debounced: fires when DEBOUNCE_INTERVAL has elapsed OR text grew by WORD_DELTA+."""
    global _last_run_at, _last_word_count, _pending_text, _pending_timer
    if not text.strip():
        return

    with _debounce_lock:
        _pending_text = text
        now = time.monotonic()
        word_count = len(text.split())
        elapsed = now - _last_run_at
        grew_a_lot = (word_count - _last_word_count) >= WORD_DELTA

        if elapsed >= DEBOUNCE_INTERVAL or grew_a_lot or _last_run_at == 0.0:
            _last_run_at = now
            _last_word_count = word_count
            if _pending_timer is not None:
                _pending_timer.cancel()
                _pending_timer = None
            threading.Thread(target=update_belief, args=(text,), daemon=True).start()
        else:
            # Trailing timer so the last partial in a burst still lands.
            if _pending_timer is not None:
                _pending_timer.cancel()
            delay = max(0.05, DEBOUNCE_INTERVAL - elapsed)
            _pending_timer = threading.Timer(delay, _trailing_fire)
            _pending_timer.daemon = True
            _pending_timer.start()


def _trailing_fire() -> None:
    global _last_run_at, _last_word_count
    with _debounce_lock:
        text = _pending_text
        _last_run_at = time.monotonic()
        _last_word_count = len(text.split())
    threading.Thread(target=update_belief, args=(text,), daemon=True).start()


def note_final_utterance(text: str) -> None:
    """Update belief from a finalized utterance, in a background daemon thread.

    A finalized utterance is the strongest belief signal we get, and previously
    it was discarded for belief purposes (only partials updated belief). We
    bypass the partial-debouncer here because finals are rare (one per
    utterance) and never need rate-limiting.

    Belief is an ENHANCER of the decision agent's context, never a
    precondition. The decision agent reads whatever belief is current at call
    time and must never block waiting for an update. A cold belief
    ("Conversation has not started.") is a fully supported state — the
    decision agent can fire correctly without belief context, carried by
    retrieval + prompt alone.
    """
    if not text.strip():
        return
    threading.Thread(target=update_belief, args=(text,), daemon=True).start()


def get_belief() -> str:
    with _belief_lock:
        return current_belief


def reset_belief() -> None:
    """For tests / new-call boundaries."""
    global current_belief, _last_run_at, _last_word_count
    with _belief_lock:
        current_belief = "Conversation has not started."
    with _debounce_lock:
        _last_run_at = 0.0
        _last_word_count = 0
