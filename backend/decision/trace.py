"""Considered-but-stayed-silent trace.

The decision agent stays silent on most utterances. From the audience's
perspective, that silence is invisible — they can't tell whether Chief
chose not to interrupt or never heard anything. This module records each
decision so the UI can show, at the end of the demo, the things Chief
considered and deliberately declined to surface. Silence becomes visible
evidence of judgment.

Data only — no UI. Threaded ring buffer of the last 50 entries.

Each entry:
    timestamp: float (time.time())
    utterance: str (the finalized text the agent saw)
    action:    "silent" | "whisper"
    reason:    short one-line reason for telemetry / UI grouping
"""

import threading
import time
from collections import deque
from dataclasses import asdict, dataclass

CAPACITY = 50


@dataclass
class TraceEntry:
    timestamp: float
    utterance: str
    action: str
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


_buffer: "deque[TraceEntry]" = deque(maxlen=CAPACITY)
_lock = threading.Lock()


def record(utterance: str, action: str, reason: str) -> None:
    """Append a single decision to the trace ring buffer."""
    entry = TraceEntry(
        timestamp=time.time(),
        utterance=utterance or "",
        action=action,
        reason=reason,
    )
    with _lock:
        _buffer.append(entry)


def get_trace() -> list[TraceEntry]:
    """Return a snapshot of the trace, oldest first."""
    with _lock:
        return list(_buffer)


def get_trace_dicts() -> list[dict]:
    """Snapshot as plain dicts — convenient for the WS bridge / UI layer."""
    with _lock:
        return [e.to_dict() for e in _buffer]


def clear() -> None:
    """Reset the buffer. Useful in tests and at session boundaries."""
    with _lock:
        _buffer.clear()
