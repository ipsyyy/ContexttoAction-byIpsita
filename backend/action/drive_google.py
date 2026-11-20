"""Google Drive facade — the single point of contact with the Drive v3 API.

Powers the action agent's ``pull_document`` tool: searches Drive by query
and surfaces the top matches with name + webViewLink. Same defensive shape
as ``email_agentmail`` and ``calendar_google``:

- Lazy service init only when ``token.json`` exists and the SDK imports.
- Auto-refresh on expired tokens.
- Hard 2.5s timeout on every API call.
- Sentinel on any failure (``None`` / ``[]``). Never raises.
- Logs WHEN + WHAT (query, match count). No tokens / auth material.

Read-only: uses the ``drive.readonly`` scope that's already in
``scripts/google_auth.py``. The action agent's ``pull_document`` tool is
described as "find and surface a document matching a description" — this
module is the fulfillment.

Off-path: only invoked from ``agent._execute_auto`` which runs in the
action agent's daemon thread.
"""

from __future__ import annotations

import concurrent.futures
import os
import threading
from typing import Optional

try:
    from google.oauth2.credentials import Credentials  # type: ignore
    from google.auth.transport.requests import Request as GoogleAuthRequest  # type: ignore
    from googleapiclient.discovery import build  # type: ignore
    _SDK_AVAILABLE = True
except Exception as e:  # pragma: no cover - env-dependent
    print(f"[drive] SDK unavailable ({e}) — drive path disabled")
    _SDK_AVAILABLE = False
    Credentials = None  # type: ignore
    GoogleAuthRequest = None  # type: ignore
    build = None  # type: ignore


# ── Constants ────────────────────────────────────────────────────────────────

DRIVE_TIMEOUT_SECONDS = 2.5
SEARCH_RESULT_LIMIT = 5

_PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
_TOKEN_FILE = os.path.join(_PROJECT_ROOT, "token.json")


# ── Lazy service + executor ──────────────────────────────────────────────────

_service: Optional[object] = None
_service_lock = threading.Lock()

_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="drive_google"
)


def _get_service() -> Optional[object]:
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
                "drive", "v3", credentials=creds, cache_discovery=False
            )
            return _service
        except Exception as e:
            print(f"[drive] service init failed: {e}")
            return None


def reset_service() -> None:
    global _service
    with _service_lock:
        _service = None


def is_available() -> bool:
    if not _SDK_AVAILABLE:
        return False
    return os.path.isfile(_TOKEN_FILE)


# ── Internal timeout wrapper ─────────────────────────────────────────────────


def _with_timeout(fn, *args, **kwargs):
    future = _executor.submit(fn, *args, **kwargs)
    return future.result(timeout=DRIVE_TIMEOUT_SECONDS)


def _safe(fn_name: str, fn, *args, **kwargs):
    try:
        return _with_timeout(fn, *args, **kwargs)
    except concurrent.futures.TimeoutError:
        print(f"[drive] {fn_name} timed out after {DRIVE_TIMEOUT_SECONDS}s")
        return None
    except Exception as e:
        print(f"[drive] {fn_name} error: {e}")
        return None


# ── Public API ───────────────────────────────────────────────────────────────


def search(query: str, limit: int = SEARCH_RESULT_LIMIT) -> Optional[list[dict]]:
    """Search Drive for documents matching `query`.

    Returns a list of ``{id, name, mimeType, webViewLink, modifiedTime}``
    dicts ordered by ``modifiedTime`` (newest first). Returns:

    - ``None`` on any failure (auth absent, parse error, timeout, API error)
      — caller should preserve stub fallback.
    - ``[]`` when the call succeeded but Drive returned no matches.
    - non-empty list on a successful hit.

    Hits both ``fullText`` and ``name`` so descriptions like "Q2 deck" find
    a file named "Q2 board deck" even if the body wouldn't match, and a
    full-text query like "runway 18 months" finds a doc that mentions it.
    """
    service = _get_service()
    if service is None:
        return None
    q_raw = (query or "").strip()
    if not q_raw:
        return None

    # Drive query syntax: escape backslashes then single quotes.
    q_esc = q_raw.replace("\\", "\\\\").replace("'", "\\'")
    drive_q = (
        f"(fullText contains '{q_esc}' or name contains '{q_esc}') "
        f"and trashed = false"
    )
    try:
        n = max(1, min(int(limit), 25))
    except (TypeError, ValueError):
        n = SEARCH_RESULT_LIMIT

    res = _safe(
        "files.list",
        lambda: service.files().list(  # type: ignore[attr-defined]
            q=drive_q,
            pageSize=n,
            fields="files(id, name, mimeType, webViewLink, modifiedTime)",
            orderBy="modifiedTime desc",
        ).execute(),
    )
    if res is None:
        return None

    files = res.get("files", []) or []
    out: list[dict] = []
    for f in files:
        out.append({
            "id": f.get("id", ""),
            "name": f.get("name", "(untitled)"),
            "mimeType": f.get("mimeType", ""),
            "webViewLink": f.get("webViewLink", ""),
            "modifiedTime": f.get("modifiedTime", ""),
        })
    print(f"[drive] search({q_raw!r}) -> {len(out)} match(es)")
    return out
