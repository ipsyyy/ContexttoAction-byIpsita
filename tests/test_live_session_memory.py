from threading import Event

from memory.chat import answer_question
from memory.transcripts import local_store


def test_stopped_live_session_enters_shared_memory(tmp_path, monkeypatch):
    from scripts import live_loop

    monkeypatch.setenv("CONTEXT_AGENT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(live_loop, "_start_conversation_bg", lambda *_: None)
    recorded = Event()
    saved = Event()

    def save(conversation_id, rows):
        live_loop.save_session(conversation_id, rows)
        saved.set()

    monkeypatch.setattr(live_loop, "_end_conversation_bg", save)
    monkeypatch.setattr(live_loop, "transcribe_pcm16_b64", lambda *_: "Workshop uses a purple invoice router.")
    monkeypatch.setattr(live_loop, "on_utterance_final", lambda *_: recorded.set())
    try:
        live_loop.handle_resume({})
        live_loop.handle_audio_utterance({"audio": "AA=="})
        assert recorded.wait(3)
    finally:
        live_loop.handle_pause({})
    assert saved.wait(3)
    store = local_store()
    result = answer_question(store, "What purple invoice router was discussed?")
    source = result["sources"][0]
    assert source["filename"].startswith("Live session-")
    assert "Workshop uses a purple invoice router." in source["excerpt"]
    assert live_loop._start_conv_if_needed("Late result", live_loop._mic_generation - 1) is False
