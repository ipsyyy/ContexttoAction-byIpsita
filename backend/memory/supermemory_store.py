"""Supermemory durable store — the system of record for L1 claims and L2
counterparty profiles.

This module is the SINGLE place callers touch Supermemory. Every operation:
  - checks the env key once (lazy client init)
  - wraps the SDK call in a hard 2.5s timeout (concurrent.futures deadline,
    plus the SDK's own httpx timeout as belt-and-suspenders)
  - returns a documented sentinel on any failure ([] / None / False / 0)
  - logs the failure, never raises to callers

It is consulted ONLY off the decision hot path: at conversation-start priming
(reads) and conversation-end consolidation (writes). The decision agent reads
the in-process frozen briefing — it never touches this module directly.

Pure storage. No decision logic; no whisper shaping; no policy. Callers
(L3 priming, L5 consolidation, the L2 profile layer) decide what to do
with the data; this module just persists and retrieves.
"""

from __future__ import annotations

from memory.supermemory_client import compatible_client

import concurrent.futures
import datetime
import json
import os
import threading
from dataclasses import asdict
from typing import Any, Iterable, Optional

try:
    from supermemory import Supermemory  # type: ignore
    _SDK_AVAILABLE = True
except Exception as e:  # pragma: no cover - env-dependent
    print(f"[supermemory] SDK unavailable ({type(e).__name__} (HTTP {getattr(e, 'status_code', None)})) — durable store disabled")
    _SDK_AVAILABLE = False
    Supermemory = None  # type: ignore


# ── Constants ────────────────────────────────────────────────────────────────

# Hard ceiling for every off-path Supermemory call. The brief: a slow
# Supermemory must never stall conversation start beyond this; on timeout
# the caller falls back to local fixtures.
SUPERMEMORY_TIMEOUT_SECONDS = 2.5

# Container-tag scheme. All Chief data is scoped under the global tag so
# we don't collide with other consumers of the same Supermemory project.
TAG_GLOBAL = "chief"
TAG_CLAIM = "chief:claim"
TAG_PROFILE = "chief:profile"
TAG_ENTITY_PREFIX = "chief:entity:"
TAG_ATTR_PREFIX = "chief:attr:"
TAG_SOURCE_PREFIX = "chief:src:"

# Search limit when hydrating an entity's claims at conversation start.
# Capped at 100 — the documented Supermemory API max. Exceeding it returns
# 400 and the call falls through to the empty-list sentinel silently.
HYDRATE_LIMIT = 100


# ── Lazy client + executor ───────────────────────────────────────────────────

_client: Optional[object] = None
_client_lock = threading.Lock()

# Off-path-only executor with a small worker pool. None of these calls happen
# on the decision hot path; sizing keeps memory minimal.
_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="supermemory"
)


def _get_client() -> Optional[object]:
    """Lazy-init the Supermemory client. Returns None when SDK missing or
    SUPERMEMORY_API_KEY unset — that's the documented local-fallback mode."""
    global _client
    with _client_lock:
        if _client is not None:
            return _client
        if not _SDK_AVAILABLE:
            return None
        if not os.getenv("SUPERMEMORY_API_KEY"):
            return None
        try:
            _client = compatible_client(Supermemory())  # reads SUPERMEMORY_API_KEY from env
            return _client
        except Exception as e:
            print(f"[supermemory] client init failed: {type(e).__name__} (HTTP {getattr(e, 'status_code', None)})")
            return None


def reset_client() -> None:
    """For tests / explicit reconfigurations."""
    global _client
    with _client_lock:
        _client = None


def is_available() -> bool:
    """Quick predicate for callers that want to know whether to bother. Cheap;
    just checks SDK + env. Does NOT round-trip the network."""
    if not _SDK_AVAILABLE:
        return False
    if not os.getenv("SUPERMEMORY_API_KEY"):
        return False
    return True


# ── Internal timeout wrapper ─────────────────────────────────────────────────


def _with_timeout(fn, *args, **kwargs):
    """Run `fn(*args, **kwargs)` on the executor with the hard deadline.

    Returns the function's return value on success.
    Raises concurrent.futures.TimeoutError if the deadline is hit.
    Raises the underlying exception if `fn` raised one.
    """
    future = _executor.submit(fn, *args, **kwargs)
    return future.result(timeout=SUPERMEMORY_TIMEOUT_SECONDS)


def _safe(fn_name: str, fn, *args, **kwargs):
    """Run `fn` with timeout + exception handling. Returns the function's
    result or None on any failure (timeout, SDK error, parse error)."""
    try:
        return _with_timeout(fn, *args, **kwargs)
    except concurrent.futures.TimeoutError:
        print(f"[supermemory] {fn_name} timed out after {SUPERMEMORY_TIMEOUT_SECONDS}s")
        return None
    except Exception as e:
        print(f"[supermemory] {fn_name} error: {type(e).__name__} (HTTP {getattr(e, 'status_code', None)})")
        return None


# ── Claim API ────────────────────────────────────────────────────────────────


def _claim_tags(claim) -> list[str]:
    tags = [TAG_GLOBAL, TAG_CLAIM]
    e = _get(claim, "entity_id")
    a = _get(claim, "attribute")
    s = _get(claim, "source")
    if e:
        tags.append(f"{TAG_ENTITY_PREFIX}{e}")
    if a:
        tags.append(f"{TAG_ATTR_PREFIX}{a}")
    if s:
        tags.append(f"{TAG_SOURCE_PREFIX}{s}")
    return tags


def _claim_custom_id(claim) -> str:
    """Deterministic id so re-running the seed is idempotent."""
    e = _get(claim, "entity_id") or "unknown"
    a = _get(claim, "attribute") or "unknown"
    src = _get(claim, "source") or "unknown"
    ts = _get(claim, "timestamp") or ""
    return f"chief:claim:{e}:{a}:{src}:{ts}"


def _claim_payload(claim) -> str:
    if hasattr(claim, "__dataclass_fields__"):
        return json.dumps(asdict(claim))
    if isinstance(claim, dict):
        return json.dumps(claim)
    raise TypeError(f"unsupported claim type: {type(claim)!r}")


def _now_date() -> str:
    """Today, ISO-8601 (YYYY-MM-DD). Centralized so tests can patch."""
    return datetime.date.today().isoformat()


def _claim_metadata(claim) -> dict:
    """Metadata travels alongside container_tags. The `as_of_date` lets
    downstream queries reason about recency (and gives Calendar / action
    code a reliable "today" anchor for relative dates like 'Thursday')."""
    md: dict = {
        "as_of_date": _now_date(),
    }
    for k in ("entity_id", "attribute", "source", "timestamp", "conversation_id"):
        v = _get(claim, k)
        if v is not None and isinstance(v, (str, int, float, bool)):
            md[k] = v
    conf = _get(claim, "confidence")
    if isinstance(conf, (int, float)):
        md["confidence"] = float(conf)
    return md


def add_claim(claim) -> bool:
    """Push one claim to Supermemory. Returns True on success, False on any
    failure (including "store unavailable"). Idempotent via custom_id —
    re-adding the same claim overwrites.

    The current date is always attached as `metadata.as_of_date` so future
    queries can reason about recency without re-parsing the claim payload."""
    client = _get_client()
    if client is None:
        return False
    try:
        payload = _claim_payload(claim)
        tags = _claim_tags(claim)
        cid = _claim_custom_id(claim)
        metadata = _claim_metadata(claim)
    except Exception as e:
        print(f"[supermemory] add_claim serialize error: {type(e).__name__} (HTTP {getattr(e, 'status_code', None)})")
        return False

    res = _safe(
        "add_claim",
        client.add,
        content=payload,
        container_tags=tags,
        custom_id=cid,
        metadata=metadata,
        timeout=SUPERMEMORY_TIMEOUT_SECONDS,
    )
    return res is not None


def consolidate(claims: Iterable) -> int:
    """Batch-push claims to Supermemory at conversation end. Returns the count
    actually pushed. Failures are per-claim — one bad push doesn't abort the
    batch — so consolidation is best-effort but maximally durable.

    Off-path. Never invoked on the decision hot path."""
    pushed = 0
    for c in claims:
        if add_claim(c):
            pushed += 1
    return pushed


def get_claims(entity_id: str, attribute: Optional[str] = None) -> list:
    """Hydrate claims for an entity. Returns a list of Claim dataclasses,
    most recent first. Empty list on any failure or when the store is
    unavailable — caller must degrade.

    Off-path. Called from L3 priming at conversation start, never on the
    decision hot path."""
    from memory.claims import Claim  # local import: avoid module cycle

    client = _get_client()
    if client is None or not entity_id:
        return []

    container_tags = [f"{TAG_ENTITY_PREFIX}{entity_id}"]
    if attribute:
        container_tags.append(f"{TAG_ATTR_PREFIX}{attribute}")

    results = _safe(
        "get_claims",
        client.search.documents,
        q="claim",
        container_tags=container_tags,
        limit=HYDRATE_LIMIT,
        include_full_docs=True,
        timeout=SUPERMEMORY_TIMEOUT_SECONDS,
    )
    if results is None:
        return []

    docs = _extract_docs(results)
    out: list = []
    for d in docs:
        content = _content_of(d)
        if not content:
            continue
        try:
            data = json.loads(content)
            c = Claim(**data)
        except Exception:
            continue
        # Defensive: when querying by attribute, the tag filter should already
        # have narrowed; but content may have richer attribute strings, so
        # also filter here.
        if attribute and getattr(c, "attribute", None) != attribute:
            continue
        out.append(c)
    out.sort(key=lambda c: getattr(c, "timestamp", "") or "", reverse=True)
    return out


def contradiction_query(entity_id: str, attribute: str, new_value: Any) -> list:
    """Return prior claims (entity, attribute) whose value DISAGREES with
    new_value. Pure CRUD — no policy / no eligibility filtering beyond
    value inequality; callers (decision agent) layer their own conservatism.

    Off-path. Off-path. Off-path."""
    if not entity_id or not attribute:
        return []
    prior = get_claims(entity_id, attribute)
    return [c for c in prior if getattr(c, "value", None) != new_value]


# ── Profile API (L2) ─────────────────────────────────────────────────────────


def _profile_tags(entity_id: str) -> list[str]:
    return [TAG_GLOBAL, TAG_PROFILE, f"{TAG_ENTITY_PREFIX}{entity_id}"]


def _profile_custom_id(entity_id: str) -> str:
    return f"chief:profile:{entity_id}"


def get_profile(entity_id: str) -> Optional[dict]:
    """Read the counterparty profile for an entity. Returns None on any
    failure or absence — caller falls back to "no profile" behavior.

    Profile shape (caller-defined; this module is content-agnostic):
        {
          "traits":     [str, ...],
          "open_items": [str, ...],
          "notes":      [{"date": str, "text": str}, ...],
        }
    """
    client = _get_client()
    if client is None or not entity_id:
        return None

    results = _safe(
        "get_profile",
        client.search.documents,
        q="profile",
        container_tags=_profile_tags(entity_id),
        limit=1,
        include_full_docs=True,
        timeout=SUPERMEMORY_TIMEOUT_SECONDS,
    )
    if results is None:
        return None

    docs = _extract_docs(results)
    for d in docs:
        content = _content_of(d)
        if not content:
            continue
        try:
            return json.loads(content)
        except Exception:
            continue
    return None


def put_profile(entity_id: str, profile: dict) -> bool:
    """Upsert the profile document for an entity. Returns True on success.

    `metadata.as_of_date` is stamped on every write so a future read can tell
    when this profile snapshot was last refreshed."""
    client = _get_client()
    if client is None or not entity_id or profile is None:
        return False
    try:
        payload = json.dumps(profile)
    except Exception as e:
        print(f"[supermemory] put_profile serialize error: {type(e).__name__} (HTTP {getattr(e, 'status_code', None)})")
        return False

    res = _safe(
        "put_profile",
        client.add,
        content=payload,
        container_tags=_profile_tags(entity_id),
        custom_id=_profile_custom_id(entity_id),
        metadata={
            "as_of_date": _now_date(),
            "entity_id": entity_id,
            "kind": "profile",
        },
        timeout=SUPERMEMORY_TIMEOUT_SECONDS,
    )
    return res is not None


# ── Dashboard read ───────────────────────────────────────────────────────────


def list_recent(limit: int = 50) -> list[dict]:
    """Return up to `limit` Chief-tagged Supermemory docs as plain dicts for
    the Settings dashboard. Most recent first. Empty list when the store is
    unavailable.

    Off-path. Called from the Electron UI to render the memory list."""
    client = _get_client()
    if client is None:
        return []

    results = _safe(
        "list_recent",
        client.search.documents,
        q="chief",
        container_tags=[TAG_GLOBAL],
        limit=max(1, min(int(limit), HYDRATE_LIMIT)),
        include_full_docs=True,
        timeout=SUPERMEMORY_TIMEOUT_SECONDS,
    )
    if results is None:
        return []

    out: list[dict] = []
    for d in _extract_docs(results):
        item = {
            "id": _attr(d, "id") or _attr(d, "custom_id") or "",
            "custom_id": _attr(d, "custom_id") or "",
            "content": _content_of(d),
            "container_tags": _attr(d, "container_tags") or [],
            "metadata": _attr(d, "metadata") or {},
            "created_at": _attr(d, "created_at") or _attr(d, "createdAt") or "",
            "updated_at": _attr(d, "updated_at") or _attr(d, "updatedAt") or "",
        }
        out.append(item)
    out.sort(
        key=lambda x: (x.get("updated_at") or x.get("created_at") or ""),
        reverse=True,
    )
    return out


def _attr(obj, name):
    if hasattr(obj, name):
        v = getattr(obj, name, None)
        if v is not None:
            return v
    if isinstance(obj, dict):
        return obj.get(name)
    return None


# ── Helpers ──────────────────────────────────────────────────────────────────


def _get(obj, name):
    if hasattr(obj, name):
        return getattr(obj, name, None)
    if isinstance(obj, dict):
        return obj.get(name)
    return None


def _extract_docs(results) -> list:
    if results is None:
        return []
    if hasattr(results, "results"):
        return list(getattr(results, "results") or [])
    if isinstance(results, dict):
        return list(results.get("results") or [])
    return []


def _content_of(doc) -> str:
    if hasattr(doc, "content"):
        return getattr(doc, "content") or ""
    if isinstance(doc, dict):
        return doc.get("content", "") or ""
    return ""
