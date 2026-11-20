"""ElevenLabs Scribe — utterance-level STT.

Renderer captures mic audio, runs VAD locally, and sends the completed
utterance as base64-encoded PCM16 to the Python backend. This module wraps
the PCM as a WAV and posts it to ElevenLabs.

We use scribe_v1 (the stable Scribe release) — fast, English transcription.
"""

import base64
import io
import os
import wave

try:
    from elevenlabs import ElevenLabs  # type: ignore
    _sdk_available = True
except Exception as e:  # pragma: no cover
    print(f"[elevenlabs] SDK not importable ({type(e).__name__})")
    _sdk_available = False
    ElevenLabs = None  # type: ignore


_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    if not _sdk_available:
        return None
    key = os.getenv("ELEVENLABS_API_KEY")
    if not key:
        print("[elevenlabs] ELEVENLABS_API_KEY not set — STT disabled")
        return None
    _client = ElevenLabs(api_key=key)
    return _client


def _pcm16_to_wav(pcm_bytes: bytes, sample_rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)  # 16-bit
        w.setframerate(sample_rate)
        w.writeframes(pcm_bytes)
    return buf.getvalue()


def transcribe_pcm16_b64(b64: str, sample_rate: int = 16000) -> str:
    """Decode base64 PCM16, wrap as WAV, post to ElevenLabs. Returns transcript text."""
    client = _get_client()
    if client is None:
        return ""
    try:
        pcm = base64.b64decode(b64)
    except Exception as e:
        print(f"[elevenlabs] base64 decode error: {type(e).__name__}")
        return ""

    wav = _pcm16_to_wav(pcm, sample_rate)
    # SDK requires a file-like object with a name so the multipart upload can
    # carry a sane content-type.
    f = io.BytesIO(wav)
    f.name = "utterance.wav"

    try:
        response = client.speech_to_text.convert(
            file=f,
            model_id="scribe_v1",
            language_code="en",
        )
    except Exception as e:
        print(f"[elevenlabs] convert error: {type(e).__name__}")
        return ""

    text = (getattr(response, "text", "") or "").strip()
    return text
