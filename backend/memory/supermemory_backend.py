"""Supermemory persistence adapter for the L1 claim store.

Claims serialize as JSON in document `content`; provenance and entity-id
travel as `container_tags` so they can filter searches without parsing.

Degradation is the contract:
- No SDK installed       -> backend is None, ClaimStore stays in-memory only.
- No SUPERMEMORY_API_KEY -> backend is None, ClaimStore stays in-memory only.
- SDK error at runtime   -> logged, swallowed; in-memory state is authoritative.

This adapter is NEVER touched on the decision hot path. It is consulted only
by hydrate_for_entity (called from start_conversation, off-path) and persist
(called from consolidate at conversation end, off-path).
"""

from __future__ import annotations

from memory.supermemory_client import compatible_client

import json
import os
import threading
from dataclasses import asdict
from typing import Iterable, Optional

try:
    from supermemory import Supermemory  # type: ignore
    _SDK_AVAILABLE = True
except Exception as e:  # pragma: no cover - env-dependent
    print(f"[supermemory] SDK unavailable ({type(e).__name__} (HTTP {getattr(e, 'status_code', None)})) - persistence disabled")
    _SDK_AVAILABLE = False
    Supermemory = None  # type: ignore


# Container tag conventions. The global tag scopes everything Chief writes
# (so we don't collide with other users of the project's Supermemory).
CONTAINER_GLOBAL = "chief:claims"
CONTAINER_ENTITY_PREFIX = "chief:entity:"
CONTAINER_ATTR_PREFIX = "chief:attr:"
CONTAINER_SOURCE_PREFIX = "chief:src:"


class SupermemoryBackend:
    """Thin write-after adapter. One Supermemory document per claim."""

    def __init__(self, client: object) -> None:
        self._client = client
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> "Optional[SupermemoryBackend]":
        if not _SDK_AVAILABLE:
            return None
        if not os.getenv("SUPERMEMORY_API_KEY"):
            print("[supermemory] SUPERMEMORY_API_KEY not set - persistence disabled")
            return None
        try:
            return cls(compatible_client(Supermemory()))
        except Exception as e:
            print(f"[supermemory] init failed: {type(e).__name__} (HTTP {getattr(e, 'status_code', None)})")
            return None

    # ── persist (write-after, at conversation end) ─────────────────────────

    def persist(self, claims: Iterable[object]) -> int:
        """Push each claim as its own Supermemory document. Returns the
        number actually pushed. Errors are logged and swallowed."""
        pushed = 0
        for c in claims:
            try:
                payload = self._serialize(c)
                tags = self._tags_for(c)
            except Exception as e:
                print(f"[supermemory] serialize error: {type(e).__name__} (HTTP {getattr(e, 'status_code', None)})")
                continue
            try:
                self._client.add(content=payload, container_tags=tags)  # type: ignore[attr-defined]
                pushed += 1
            except Exception as e:
                print(f"[supermemory] persist error: {type(e).__name__} (HTTP {getattr(e, 'status_code', None)})")
        return pushed

    # ── hydrate (at conversation start, off-path) ───────────────────────────

    def hydrate_for_entity(self, entity_id: str) -> list:
        """Return all claims previously stored for the entity. Empty list on
        any failure - caller must degrade. Late-imports Claim to avoid a
        cycle with memory.claims."""
        from memory.claims import Claim  # local import: avoid module cycle
        if not entity_id:
            return []
        try:
            results = self._client.search.documents(  # type: ignore[attr-defined]
                q="claim",
                container_tags=[f"{CONTAINER_ENTITY_PREFIX}{entity_id}"],
            )
        except Exception as e:
            print(f"[supermemory] hydrate search error: {type(e).__name__} (HTTP {getattr(e, 'status_code', None)})")
            return []
        docs = self._extract_docs(results)
        out: list = []
        for doc in docs:
            content = self._content_of(doc)
            if not content:
                continue
            try:
                data = json.loads(content)
                out.append(Claim(**data))
            except Exception:
                continue
        return out

    # ── helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _serialize(claim: object) -> str:
        if hasattr(claim, "__dataclass_fields__"):
            return json.dumps(asdict(claim))  # type: ignore[arg-type]
        if isinstance(claim, dict):
            return json.dumps(claim)
        raise TypeError(f"unsupported claim type: {type(claim)!r}")

    @staticmethod
    def _tags_for(claim: object) -> list[str]:
        get = (lambda k: getattr(claim, k, None)) if hasattr(claim, "__dataclass_fields__") \
              else (lambda k: claim.get(k))  # type: ignore[union-attr]
        tags = [CONTAINER_GLOBAL]
        e = get("entity_id")
        a = get("attribute")
        s = get("source")
        if e:
            tags.append(f"{CONTAINER_ENTITY_PREFIX}{e}")
        if a:
            tags.append(f"{CONTAINER_ATTR_PREFIX}{a}")
        if s:
            tags.append(f"{CONTAINER_SOURCE_PREFIX}{s}")
        return tags

    @staticmethod
    def _extract_docs(results: object) -> list:
        # The Supermemory SDK returns either an object with a .results
        # attribute or a dict. Handle both defensively.
        if results is None:
            return []
        if hasattr(results, "results"):
            return list(getattr(results, "results") or [])
        if isinstance(results, dict):
            return list(results.get("results") or [])
        return []

    @staticmethod
    def _content_of(doc: object) -> str:
        if hasattr(doc, "content"):
            return getattr(doc, "content") or ""
        if isinstance(doc, dict):
            return doc.get("content", "") or ""
        return ""


# ── Default singleton backend ────────────────────────────────────────────────

_default_backend: Optional[SupermemoryBackend] = None
_default_lock = threading.Lock()


def get_default_backend() -> Optional[SupermemoryBackend]:
    """Lazy-initialize a process-wide backend. Returns None when SDK or key
    is missing - callers must treat None as 'persistence disabled'."""
    global _default_backend
    with _default_lock:
        if _default_backend is None:
            _default_backend = SupermemoryBackend.from_env()
        return _default_backend


def reset_default_backend() -> None:
    """For tests."""
    global _default_backend
    with _default_lock:
        _default_backend = None
