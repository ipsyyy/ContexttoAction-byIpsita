"""ElevenLabs TTS — speaks whisper content aloud via afplay.

Mutes the mic in the Electron renderer before playback and unmutes after
a short buffer, preventing the orb from transcribing its own voice.
"""

import subprocess
import tempfile
import threading
import time
import os

from elevenlabs.client import ElevenLabs
from bridge import emit

_client = ElevenLabs()

VOICE_ID = "cjVigY5qzO86Huf0OWal"  # Eric — smooth, trustworthy, made for agentic use
MODEL_ID = "eleven_turbo_v2_5"


def speak(text: str) -> None:
    """Fire-and-forget: generate audio and play it. Non-blocking."""
    if not os.getenv("ELEVENLABS_API_KEY"):
        return
    threading.Thread(target=_speak_blocking, args=(text,), daemon=True).start()


def _speak_blocking(text: str) -> None:
    tmp_path = None
    try:
        audio_iter = _client.text_to_speech.convert(
            voice_id=VOICE_ID,
            text=text,
            model_id=MODEL_ID,
            output_format="mp3_44100_128",
        )
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            for chunk in audio_iter:
                f.write(chunk)
            tmp_path = f.name

        emit({"type": "mute_mic"})
        subprocess.run(["afplay", tmp_path])
        time.sleep(0.3)  # let mic ring-out settle before reopening
    except Exception as e:
        print(f"[tts] error: {type(e).__name__}")
    finally:
        emit({"type": "unmute_mic"})
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
