from memory.past_calls import TRIGGER_UTTERANCE
from ears.hooks import on_utterance_final


def inject_canned_utterance() -> None:
    print(f"[fallback] injecting: {TRIGGER_UTTERANCE}")
    on_utterance_final(TRIGGER_UTTERANCE)
