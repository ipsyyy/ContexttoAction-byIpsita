from decision.agent import make_decision
from action.agent import fire as fire_action, fire_fact_check
from memory.belief import note_final_utterance
from memory.retrieval import prefetch as prefetch_retrieval
from memory.extractor import fire as fire_extractor
from bridge import emit_whisper
from voice.tts import speak


def on_utterance_final(text: str) -> None:
    # Background, off the hot path:
    #  - Fast fact-check: Haiku query → Tavily search → Haiku verdict (~1s)
    #  - Action agent (Sonnet): email/calendar drafts only
    #  - Belief update fires from this final utterance (enhancer only)
    #  - Moss retrieval prefetches into shared state
    #  - Claim extractor: Haiku → structured claims → L1 buffer (off-path)
    fire_fact_check(text)
    fire_action(text)
    note_final_utterance(text)
    prefetch_retrieval(text)
    fire_extractor(text)

    # Hot path: decision agent. Reads whatever belief + retrieved facts are
    # current right now; cold state is fine.
    result = make_decision(text)
    if result.get("action") == "whisper":
        content = result.get("content", "")
        if content:
            provenance = result.get("provenance")
            if provenance:
                emit_whisper(content, provenance=provenance)
            else:
                emit_whisper(content)
            speak(content)
            try:
                import main_call
                main_call.push_whisper(content, provenance)
            except Exception:
                pass
            print("[decision] surfaced an alert")
