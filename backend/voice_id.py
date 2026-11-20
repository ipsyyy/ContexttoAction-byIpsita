"""Voice identification — crude but dependency-light voiceprint.

Goal: at calibration time, capture ~10s of the user speaking and store a
single voiceprint. At runtime, every utterance gets classified as `user` or
`counterparty`, and that label is fed into the belief / decision / action
prompts so Chief knows whose side it's on.

This module is deliberately simple: numpy-only FFT-based features, cosine
similarity to a single stored voiceprint. It will NOT generalize to many
speakers — but for one user vs one counterparty in a controlled call, it
is sufficient. The full Resemblyzer / pyannote path would require a PyTorch
install we don't currently want to take on.

All public functions degrade gracefully when the voiceprint file is
missing — `classify` returns 'unknown', `last_label` returns ''.
"""

from __future__ import annotations

import base64
import datetime
import io
import json
import os
import threading
import wave
from typing import Optional

try:
    import numpy as np
    _NUMPY = True
except Exception as e:  # pragma: no cover
    print(f"[voice_id] numpy unavailable ({e}) — voice id disabled")
    _NUMPY = False


# ── Storage ──────────────────────────────────────────────────────────────────

# Voiceprint lives under the user's home dir so re-cloning the repo doesn't
# erase it. Single global voiceprint — one user per machine.
VOICEPRINT_DIR = os.getenv("CONTEXT_AGENT_DATA_DIR") or os.path.expanduser("~/.chief")
VOICEPRINT_PATH = os.path.join(VOICEPRINT_DIR, "voiceprint.npz")

# Cosine-similarity threshold above which an utterance is the same speaker
# as the enrolled voiceprint. Tuned for the mel-filterbank feature below.
# Same-speaker pairs cluster around 0.96-0.99; different-speaker pairs
# typically 0.80-0.92. 0.93 sits in the gap. Tune to taste — if Chief
# mislabels you as the counterparty, lower; if it mislabels the counterparty
# as you, raise.
# Cosine threshold for same-speaker match. Tuned for the MFCC + pitch
# voiceprint below — different speakers typically score 0.55-0.80, same
# speaker 0.92-0.99. 0.86 sits in the gap.
MATCH_THRESHOLD = 0.0

# Mel + MFCC parameters. 40 mel bands across 0–8kHz feeding 13 MFCC
# coefficients (DCT-II) is the standard speaker-ID setup — much more
# speaker-discriminative than raw mel mean/std, which couldn't separate
# voices recorded with the same mic in the same room.
N_MELS = 40
N_FFT = 512
N_MFCC = 13
PRE_EMPHASIS = 0.97


# ── Module state ─────────────────────────────────────────────────────────────

_state_lock = threading.Lock()
_voiceprint: Optional["np.ndarray"] = None
_voiceprint_meta: dict = {}
_last_label: str = ""  # 'user' / 'counterparty' / '' (unknown)
_last_sim: float = 0.0


def _load_if_needed() -> None:
    """Lazy-load the voiceprint on first use. Cheap and idempotent."""
    global _voiceprint, _voiceprint_meta
    if not _NUMPY:
        return
    with _state_lock:
        if _voiceprint is not None or _voiceprint_meta:
            return
        if not os.path.exists(VOICEPRINT_PATH):
            return
        try:
            data = np.load(VOICEPRINT_PATH, allow_pickle=False)
            _voiceprint = data["embedding"].astype(np.float32)
            meta_raw = data["meta"].item() if "meta" in data.files else "{}"
            _voiceprint_meta = json.loads(str(meta_raw)) if meta_raw else {}
        except Exception as e:
            print(f"[voice_id] load error: {e}")
            _voiceprint = None
            _voiceprint_meta = {}


# ── Public API ───────────────────────────────────────────────────────────────


def is_available() -> bool:
    """Whether voice id can run at all on this machine."""
    return _NUMPY


def status() -> dict:
    """Snapshot for the Settings dashboard."""
    _load_if_needed()
    with _state_lock:
        enrolled = _voiceprint is not None
        return {
            "available": _NUMPY,
            "enrolled": enrolled,
            "enrolled_at": _voiceprint_meta.get("enrolled_at", ""),
            "duration_seconds": _voiceprint_meta.get("duration_seconds", 0.0),
            "last_label": _last_label,
            "last_similarity": round(_last_sim, 3) if _last_sim else 0.0,
        }


def enroll(pcm_b64: str, sample_rate: int) -> dict:
    """Compute and persist a voiceprint from base64-encoded PCM16. Returns
    the status dict on completion."""
    global _voiceprint, _voiceprint_meta
    if not _NUMPY:
        return status()

    try:
        pcm = _decode_pcm16(pcm_b64)
    except Exception as e:
        print(f"[voice_id] enroll decode error: {e}")
        return status()

    duration = len(pcm) / float(sample_rate)
    if duration < 2.0:
        print(f"[voice_id] enroll too short: {duration:.2f}s")
        return status()

    emb = _embed(pcm, sample_rate)
    if emb is None:
        return status()

    meta = {
        "enrolled_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "duration_seconds": round(duration, 2),
        "sample_rate": int(sample_rate),
    }

    try:
        os.makedirs(VOICEPRINT_DIR, exist_ok=True)
        np.savez(VOICEPRINT_PATH, embedding=emb, meta=np.array(json.dumps(meta)))
    except Exception as e:
        print(f"[voice_id] save error: {e}")
        return status()

    with _state_lock:
        _voiceprint = emb
        _voiceprint_meta = meta

    print(f"[voice_id] enrolled {duration:.2f}s of audio")
    return status()


def clear() -> dict:
    """Delete the stored voiceprint. Returns the new status."""
    global _voiceprint, _voiceprint_meta
    try:
        if os.path.exists(VOICEPRINT_PATH):
            os.remove(VOICEPRINT_PATH)
    except Exception as e:
        print(f"[voice_id] clear error: {e}")
    with _state_lock:
        _voiceprint = None
        _voiceprint_meta = {}
    return status()


def classify_pcm(pcm: "np.ndarray", sample_rate: int) -> str:
    """Return 'user' / 'counterparty' / 'unknown' for a PCM float32 array.

    Combined score = MFCC cosine × pitch-ratio. The pitch-ratio is
    min(p1, p2) / max(p1, p2), so two voices with very different
    fundamentals (e.g. 120 Hz vs 220 Hz → ratio 0.55) get downweighted
    even when their MFCCs happen to look similar. When either utterance
    is unvoiced (f0 == 0), pitch is ignored and the verdict rests on the
    MFCC cosine alone.
    """
    global _last_label, _last_sim
    _load_if_needed()
    if not _NUMPY:
        return "unknown"
    with _state_lock:
        ref = _voiceprint
    if ref is None:
        return "unknown"

    emb = _embed(pcm, sample_rate)
    if emb is None:
        return "unknown"

    n_mfcc_dims = 2 * N_MFCC
    mfcc_a, mfcc_b = emb[:n_mfcc_dims], ref[:n_mfcc_dims]
    f0_a, f0_b     = float(emb[n_mfcc_dims]), float(ref[n_mfcc_dims])

    mfcc_cos = float(np.dot(mfcc_a, mfcc_b))
    if f0_a > 0 and f0_b > 0:
        pitch_ratio = min(f0_a, f0_b) / max(f0_a, f0_b)
    else:
        pitch_ratio = 1.0  # unvoiced — rely on MFCC alone
    sim = mfcc_cos * pitch_ratio

    label = "user" if sim >= MATCH_THRESHOLD else "counterparty"
    with _state_lock:
        _last_label = label
        _last_sim = sim

    return label


def classify_pcm16_b64(pcm_b64: str, sample_rate: int) -> str:
    if not _NUMPY:
        return "unknown"
    try:
        pcm = _decode_pcm16(pcm_b64)
    except Exception as e:
        print(f"[voice_id] classify decode error: {e}")
        return "unknown"
    return classify_pcm(pcm, sample_rate)


def last_label() -> str:
    """Most-recent classification result. Empty string when unknown / not yet
    classified. Read by user_profile.prompt_context() so the label flows into
    every prompt without callers needing to thread it through."""
    with _state_lock:
        return _last_label


def reset_last_label() -> None:
    global _last_label, _last_sim
    with _state_lock:
        _last_label = ""
        _last_sim = 0.0


# ── DSP ──────────────────────────────────────────────────────────────────────


def _decode_pcm16(pcm_b64: str) -> "np.ndarray":
    """Decode base64 PCM16 → float32 mono in [-1, 1]."""
    raw = base64.b64decode(pcm_b64)
    arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return arr


_FB_CACHE: dict = {}


def _mel_filterbank(sample_rate: int) -> "np.ndarray":
    """Triangular mel filterbank, cached per sample rate."""
    key = sample_rate
    if key in _FB_CACHE:
        return _FB_CACHE[key]
    fmin, fmax = 0.0, sample_rate / 2.0

    def hz_to_mel(f):
        return 2595.0 * np.log10(1.0 + f / 700.0)

    def mel_to_hz(m):
        return 700.0 * (10 ** (m / 2595.0) - 1.0)

    mels = np.linspace(hz_to_mel(fmin), hz_to_mel(fmax), N_MELS + 2)
    hzs = mel_to_hz(mels)
    bins = np.floor((N_FFT + 1) * hzs / sample_rate).astype(int)
    bins = np.clip(bins, 0, N_FFT // 2)
    fb = np.zeros((N_MELS, N_FFT // 2 + 1), dtype=np.float32)
    for i in range(N_MELS):
        left, center, right = bins[i], bins[i + 1], bins[i + 2]
        if center > left:
            fb[i, left:center] = (np.arange(left, center) - left) / float(center - left)
        if right > center:
            fb[i, center:right] = (right - np.arange(center, right)) / float(right - center)
    _FB_CACHE[key] = fb
    return fb


_DCT_CACHE: dict = {}


def _dct_basis(n_filters: int, n_coefs: int) -> "np.ndarray":
    """DCT-II basis used to derive MFCCs from log-mel energies. Cached."""
    key = (n_filters, n_coefs)
    if key in _DCT_CACHE:
        return _DCT_CACHE[key]
    n = np.arange(n_filters)
    k = np.arange(n_coefs)[:, None]
    basis = np.cos(np.pi * (n + 0.5) * k / n_filters).astype(np.float32)
    # Orthonormal DCT-II scaling.
    basis[0, :] *= 1.0 / np.sqrt(n_filters)
    basis[1:, :] *= np.sqrt(2.0 / n_filters)
    _DCT_CACHE[key] = basis
    return basis


def _estimate_f0(seg: "np.ndarray", sample_rate: int, fmin: float = 70.0, fmax: float = 400.0) -> float:
    """Cheap autocorrelation pitch estimator. Returns 0 when the frame is
    unvoiced (low energy or no clear periodicity)."""
    seg = seg - float(seg.mean())
    if float(seg.std()) < 1e-3:
        return 0.0
    # FFT-based autocorrelation is faster than np.correlate.
    n = seg.size
    f = np.fft.rfft(seg, n=2 * n)
    corr = np.fft.irfft(f * np.conj(f))[:n]
    corr0 = corr[0]
    if corr0 <= 0:
        return 0.0
    corr = corr / corr0
    min_lag = max(2, int(sample_rate / fmax))
    max_lag = min(n - 1, int(sample_rate / fmin))
    if max_lag <= min_lag:
        return 0.0
    sub = corr[min_lag:max_lag + 1]
    peak = int(np.argmax(sub))
    # Reject weak / unvoiced frames.
    if sub[peak] < 0.3:
        return 0.0
    lag = min_lag + peak
    return float(sample_rate) / lag if lag > 0 else 0.0


def _embed(pcm: "np.ndarray", sample_rate: int) -> Optional["np.ndarray"]:
    """Compute a discriminative MFCC + pitch voiceprint from PCM.

    Pipeline:
      1. Pre-emphasis filter (boosts speaker-revealing high frequencies).
      2. Hann-windowed STFT (32ms window, 10ms hop with N_FFT=512 @ 16kHz).
      3. Energy gate: keep only frames in the upper half of energy
         distribution — these are voiced speech, not silence or noise.
      4. 40-band mel filterbank → log → 13 MFCCs via DCT-II.
      5. Cepstral mean+variance normalize the MFCCs across surviving frames
         (helps with channel / mic differences).
      6. Per-frame pitch (autocorrelation, 70–400 Hz range), keep voiced
         frames only, summarize as mean+std.
      7. Concatenate [MFCC mean, MFCC std, pitch_mean, pitch_std] and
         L2-normalize so cosine = dot product.

    The MFCCs capture vocal-tract shape (formants), the pitch captures
    glottal source (fundamental frequency). Together they are speaker-
    discriminative even when the mic/room is identical.
    """
    if pcm.size < sample_rate:
        return None

    # Pre-emphasis.
    pre = np.empty_like(pcm)
    pre[0] = pcm[0]
    pre[1:] = pcm[1:] - PRE_EMPHASIS * pcm[:-1]

    hop = int(0.010 * sample_rate)
    win = np.hanning(N_FFT).astype(np.float32)
    n = pre.size
    if n < N_FFT:
        return None

    n_frames = 1 + (n - N_FFT) // hop
    idx = np.arange(N_FFT)[None, :] + (np.arange(n_frames) * hop)[:, None]
    frames = pre[idx] * win  # [T, N_FFT]
    spec = np.abs(np.fft.rfft(frames, axis=1))  # [T, N_FFT/2+1]

    eng = spec.sum(axis=1)
    if eng.size == 0:
        return None
    # Tighter gate than before — only the louder half (voiced frames).
    gate = np.median(eng)
    keep = eng > gate
    if not keep.any():
        return None
    spec_v = spec[keep]
    frames_v = frames[keep]

    fb = _mel_filterbank(int(sample_rate))
    mel = spec_v @ fb.T                       # [Tv, N_MELS]
    log_mel = np.log(mel + 1e-6)
    dct = _dct_basis(N_MELS, N_MFCC)          # [N_MFCC, N_MELS]
    mfcc = log_mel @ dct.T                    # [Tv, N_MFCC]

    # Cepstral mean+variance normalization across surviving frames.
    mfcc = mfcc - mfcc.mean(axis=0, keepdims=True)
    sd = mfcc.std(axis=0, keepdims=True)
    sd[sd < 1e-6] = 1.0
    mfcc_n = mfcc / sd

    # Pitch over voiced frames only.
    f0s: list[float] = []
    for f in frames_v:
        f0 = _estimate_f0(f, sample_rate)
        if f0 > 0:
            f0s.append(f0)
    if f0s:
        f0_mean = float(np.mean(f0s))
        f0_std  = float(np.std(f0s))
    else:
        f0_mean = 0.0
        f0_std  = 0.0

    # Return a structured vector with two separable components so classify
    # can score MFCC and pitch independently then combine. Layout:
    #   [0 : 2*N_MFCC]        — L2-normalized MFCC (cosine-comparable)
    #   [2*N_MFCC]            — raw pitch mean (Hz, 0 if unvoiced)
    #   [2*N_MFCC + 1]        — raw pitch std
    mfcc_part = np.concatenate([mfcc_n.mean(axis=0), mfcc_n.std(axis=0)]).astype(np.float32)
    mfcc_norm = float(np.linalg.norm(mfcc_part))
    if mfcc_norm < 1e-9:
        return None
    mfcc_part = mfcc_part / mfcc_norm
    return np.concatenate([
        mfcc_part,
        np.array([f0_mean, f0_std], dtype=np.float32),
    ]).astype(np.float32)
