"""Google Calendar facade — the single point of contact with the Calendar API.

Replaces the print-stub behavior of ``check_calendar`` / ``block_calendar`` in
``backend/action/agent.py`` with real Google Calendar calls when OAuth is set
up. When ``token.json`` / SDK / scope is absent, every operation returns the
documented sentinel and the action agent falls back to today's stub behavior
— the demo still runs end-to-end without Google auth.

Defensive shape (mirrors ``backend/memory/supermemory_store.py`` and
``backend/action/email_agentmail.py``):

- Lazy service init only when ``token.json`` exists and the SDK imports.
- Auto-refresh on expired tokens via ``google.auth.transport.requests``.
- Hard 2.5s timeout on every Calendar API call.
- Sentinel on any failure: ``None`` / ``[]``. Never raises.
- Logs WHEN + WHAT (date, title, event id). Never tokens / auth material.

Off-path: only invoked from ``agent._execute_auto`` which runs in the
action agent's daemon thread.

Calendar scope: ``https://www.googleapis.com/auth/calendar`` is already
in ``scripts/google_auth.py``. After updating credentials, the user must
re-run ``python scripts/google_auth.py`` to regenerate ``token.json`` if
the scope set changes.
"""

from __future__ import annotations

import concurrent.futures
import datetime
import os
import threading
from typing import Optional

try:
    from google.oauth2.credentials import Credentials  # type: ignore
    from google.auth.transport.requests import Request as GoogleAuthRequest  # type: ignore
    from googleapiclient.discovery import build  # type: ignore
    _SDK_AVAILABLE = True
except Exception as e:  # pragma: no cover - env-dependent
    print(f"[calendar] SDK unavailable ({e}) — calendar path disabled")
    _SDK_AVAILABLE = False
    Credentials = None  # type: ignore
    GoogleAuthRequest = None  # type: ignore
    build = None  # type: ignore


# ── Constants ────────────────────────────────────────────────────────────────

CALENDAR_TIMEOUT_SECONDS = 2.5

# Token + credentials live in the project root, alongside ``credentials.json``.
# Both files are gitignored. Resolve from this file's location so the path
# is stable regardless of CWD.
_PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
_TOKEN_FILE = os.path.join(_PROJECT_ROOT, "token.json")


# ── Lazy service + executor ──────────────────────────────────────────────────

_service: Optional[object] = None
_service_lock = threading.Lock()

# Off-path-only executor. None of these calls happen on the decision hot path.
_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="calendar_google"
)


def _get_service() -> Optional[object]:
    """Lazy-init the Calendar v3 service. Returns None when SDK missing,
    token.json missing, or credentials fail to refresh — that's the
    documented stub-fallback mode."""
    global _service
    with _service_lock:
        if _service is not None:
            return _service
        if not _SDK_AVAILABLE:
            return None
        if not os.path.isfile(_TOKEN_FILE):
            return None
        try:
            creds = Credentials.from_authorized_user_file(_TOKEN_FILE)
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(GoogleAuthRequest())
            _service = build(
                "calendar", "v3", credentials=creds, cache_discovery=False
            )
            return _service
        except Exception as e:
            print(f"[calendar] service init failed: {e}")
            return None


def reset_service() -> None:
    """For tests / explicit reconfigurations."""
    global _service
    with _service_lock:
        _service = None


def is_available() -> bool:
    """Predicate for callers that want to know whether to bother. Cheap —
    SDK + file check only, no network round-trip."""
    if not _SDK_AVAILABLE:
        return False
    return os.path.isfile(_TOKEN_FILE)


# ── Internal timeout wrapper ─────────────────────────────────────────────────


def _with_timeout(fn, *args, **kwargs):
    future = _executor.submit(fn, *args, **kwargs)
    return future.result(timeout=CALENDAR_TIMEOUT_SECONDS)


def _safe(fn_name: str, fn, *args, **kwargs):
    try:
        return _with_timeout(fn, *args, **kwargs)
    except concurrent.futures.TimeoutError:
        print(f"[calendar] {fn_name} timed out after {CALENDAR_TIMEOUT_SECONDS}s")
        return None
    except Exception as e:
        print(f"[calendar] {fn_name} error: {e}")
        return None


# ── Date helpers ─────────────────────────────────────────────────────────────


def _parse_date(date_str: str) -> Optional[datetime.datetime]:
    """Tolerant ISO parser. Accepts 'YYYY-MM-DD' (treated as midnight) and
    full ISO 8601 with time. Returns None on parse failure."""
    if not date_str or not isinstance(date_str, str):
        return None
    s = date_str.strip()
    # Strip trailing Z so fromisoformat is happy; reattach naively-UTC.
    if s.endswith("Z"):
        s = s[:-1]
    try:
        return datetime.datetime.fromisoformat(s)
    except ValueError:
        return None


def _iso_z(dt: datetime.datetime) -> str:
    """ISO-8601 with Z suffix — what the Calendar API expects for timeMin/Max."""
    return dt.replace(microsecond=0).isoformat() + "Z"


# ── Public API ───────────────────────────────────────────────────────────────


def check_availability(date_str: str) -> Optional[list[dict]]:
    """List events on / around the given date.

    Returns a list of ``{summary, start, end}`` dicts, ordered by start
    time. Empty list means "no events found" — that's a successful query.
    ``None`` means the call could not be made (auth absent, parse error,
    timeout, API error). Callers should treat ``None`` as "preserve stub
    fallback" — the decision to render anything is theirs.

    Two windowing modes:
      - Pure date ('YYYY-MM-DD'): scans the whole calendar day in UTC.
      - Full datetime: scans a +/- 2 hour window around the moment, useful
        when the model resolves "Thursday at 2pm" into a concrete time.
    """
    service = _get_service()
    if service is None:
        return None
    when = _parse_date(date_str)
    if when is None:
        return None

    if when.hour == 0 and when.minute == 0 and when.second == 0:
        start = when.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + datetime.timedelta(days=1)
    else:
        start = when - datetime.timedelta(hours=2)
        end = when + datetime.timedelta(hours=2)

    res = _safe(
        "events.list",
        lambda: service.events().list(  # type: ignore[attr-defined]
            calendarId="primary",
            timeMin=_iso_z(start),
            timeMax=_iso_z(end),
            singleEvents=True,
            orderBy="startTime",
            maxResults=10,
        ).execute(),
    )
    if res is None:
        return None

    items = res.get("items", []) or []
    out: list[dict] = []
    for ev in items:
        out.append({
            "summary": ev.get("summary", "(untitled)"),
            "start": (ev.get("start") or {}).get("dateTime") or (ev.get("start") or {}).get("date"),
            "end":   (ev.get("end")   or {}).get("dateTime") or (ev.get("end")   or {}).get("date"),
        })
    return out


def block(title: str, date_str: str, duration_minutes: int = 60) -> Optional[str]:
    """Create an event on the primary calendar. Returns the event id on
    success, None on any failure.

    Defensive on duration: non-int or non-positive values default to 60.
    """
    service = _get_service()
    if service is None:
        return None
    when = _parse_date(date_str)
    if when is None:
        return None
    try:
        duration = int(duration_minutes)
    except (TypeError, ValueError):
        duration = 60
    if duration <= 0:
        duration = 60

    end = when + datetime.timedelta(minutes=duration)
    body = {
        "summary": str(title or "Held by Chief"),
        "start": {"dateTime": when.isoformat(), "timeZone": "UTC"},
        "end":   {"dateTime": end.isoformat(),  "timeZone": "UTC"},
    }
    res = _safe(
        "events.insert",
        lambda: service.events().insert(  # type: ignore[attr-defined]
            calendarId="primary", body=body
        ).execute(),
    )
    if res is None:
        return None
    event_id = res.get("id") or "?"
    print(
        f"[calendar] blocked {title!r} @ {when.isoformat()} "
        f"for {duration}m (event_id={event_id})"
    )
    return event_id
