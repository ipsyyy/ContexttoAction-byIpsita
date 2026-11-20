"""Check configured speech, cited Claude chat and trace readback with synthetic data."""

import base64
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import wave

from dotenv import load_dotenv

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))
load_dotenv(REPO / ".env")

from decision.agent import make_decision
from ears.stt_elevenlabs import transcribe_pcm16_b64
from evaluation.telemetry import client, record
from memory.chat import answer_question
from memory.transcripts import TranscriptStore

EXPECTED = "purple invoice router"


def check_speech(directory):
    aiff = directory / "sample.aiff"
    wav = directory / "sample.wav"
    subprocess.run(["say", "-o", str(aiff), f"The workshop used a {EXPECTED}."], check=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1",
                    str(aiff), str(wav)], check=True)
    with wave.open(str(wav), "rb") as audio:
        pcm = base64.b64encode(audio.readframes(audio.getnframes())).decode("ascii")
        transcript = transcribe_pcm16_b64(pcm, audio.getframerate())
    if EXPECTED not in transcript.lower():
        raise RuntimeError("Speech provider did not return the expected synthetic phrase.")
    print("PASS ElevenLabs synthetic speech", flush=True)


def check_chat(directory):
    store = TranscriptStore(directory / "smoke.sqlite3")
    store.import_text("workshop.txt", (REPO / "tests/fixtures/workshop.txt").read_text())
    result = answer_question(store, "What invoice router did Ada describe?", use_model=True)
    if result["mode"] != "claude" or EXPECTED not in result["answer"].lower() or not result["sources"]:
        raise RuntimeError("Claude did not return the expected cited synthetic answer.")
    print("PASS Claude cited synthetic answer", flush=True)
    return result


def check_decisions(directory):
    os.environ["CONTEXT_AGENT_DATA_DIR"] = str(directory)
    store = TranscriptStore(directory / "transcripts.sqlite3")
    store.import_text("verified-workshop.txt", (
        "[00:02] Ada: The current Purple Invoice Router threshold is 70 percent. "
        "Invoices below it must go to a reviewer."
    ))
    silent = make_decision("It is good to see everyone again.")
    if silent.get("action") != "silent":
        raise RuntimeError("Expected silence for synthetic small talk.")
    print("PASS Claude live decision stayed silent on small talk", flush=True)
    alert = make_decision("The current Purple Invoice Router threshold is 95 percent.")
    provenance = alert.get("provenance", {})
    if alert.get("action") != "whisper" or provenance.get("source") != "verified-workshop.txt":
        raise RuntimeError(f"Expected a sourced alert, got {alert.get('action')}, citation present: {bool(provenance)}.")
    print("PASS Claude live decision cited the imported contradiction", flush=True)


def check_trace(result):
    trace_id = record("chat", mode="claude", count=len(result["sources"]), ok=True)
    if not trace_id:
        raise RuntimeError("Tracing requires explicit opt-in and a configured LangSmith API key.")
    for _ in range(8):
        try:
            run = client().read_run(trace_id)
        except Exception:
            time.sleep(1)
            continue
        if run.inputs or run.outputs != {"mode": "claude", "count": len(result["sources"]), "ok": True}:
            raise RuntimeError("Hosted trace did not contain the expected redacted metadata.")
        print(f"PASS LangSmith readback {trace_id}", flush=True)
        return
    raise RuntimeError("The hosted trace could not be read back.")


def main():
    with TemporaryDirectory() as temp:
        directory = Path(temp)
        failures = []
        result = None
        for check, key in ((check_speech, "ELEVENLABS_API_KEY"), (check_chat, "ANTHROPIC_API_KEY")):
            if not os.getenv(key):
                print(f"BLOCKED {check.__name__}: missing {key}", flush=True)
                failures.append(check.__name__)
                continue
            try:
                check_result = check(directory)
                if check_result:
                    result = check_result
            except Exception as error:
                print(f"FAIL {check.__name__}: {error if isinstance(error, RuntimeError) else type(error).__name__}")
                failures.append(check.__name__)
        if result:
            try:
                check_decisions(directory)
            except RuntimeError as error:
                print(f"FAIL live decision: {error}")
                failures.append("decision")
        if result and os.getenv("LANGSMITH_API_KEY") and os.getenv("CONTEXT_AGENT_TRACE_ENABLED") == "1":
            try:
                check_trace(result)
            except RuntimeError as error:
                print(f"FAIL LangSmith: {error}")
                failures.append("trace")
        else:
            print("BLOCKED LangSmith: needs a cited Claude result, a key and tracing opt-in.")
            failures.append("trace")
        return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
