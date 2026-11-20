"""L1 — entity-keyed claim store with provenance.

The atomic unit of memory is a CLAIM, not a transcript. A claim is one
specific (entity, attribute, value) triple with a source, a timestamp, and a
confidence. Claims are what the contradiction-catch and external-source
whispers in the demo are built on top of.

Provenance integrity is non-negotiable:
- A claim with no source is not eligible to trigger anything.
- A claim whose entity could not be resolved to a canonical id is not
  eligible to trigger anything.

The store is in-memory for the live conversation and optionally persisted
through a backend adapter (memory/supermemory_backend.py). The hot path
NEVER reads through the backend — the read-live/write-after model means we
hydrate the store from the backend at conversation start, freeze a snapshot,
and the decision component reads only the frozen snapshot. Mid-conversation
writes go to a buffer and only flush to the backend at conversation end.

Nothing in this module touches Anthropic or runs on the decision hot path.
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Optional, Tuple

# Sentinel returned by resolve_entity() when the hint can't be matched. A
# claim with this entity_id is never eligible to influence a whisper.
UNRESOLVED_ENTITY = "entity:unresolved"


@dataclass(frozen=True)
class Claim:
    """One asserted fact about one entity. Frozen so it's safe to hand into
    the immutable conversation snapshot."""

    entity_id: str
    attribute: str
    value: Any
    source: str           # "<source_type>:<source_id>" — e.g. "prior_call:apr15"
    timestamp: str        # ISO-8601 of when the claim was asserted
    conversation_id: Optional[str] = None
    confidence: float = 1.0

    def is_eligible(self) -> bool:
        """A claim that's missing a source or whose entity is unresolved is
        NOT whisper-eligible. This is the provenance integrity rule the
        decision layer enforces before letting any claim shape a whisper."""
        return bool(self.source) and self.entity_id != UNRESOLVED_ENTITY

    def provenance(self) -> str:
        """Human-readable provenance string for whisper content."""
        return f"{self.source} @ {self.timestamp}"


# ── Entity resolution ────────────────────────────────────────────────────────
# Deterministic alias table. Hand-authored for the demo. NEVER guesses an
# identity — unknown hints return UNRESOLVED_ENTITY. A real system would back
# this with a clustering / coref step; for hackathon, fixtures + register_alias
# at startup are sufficient.


@dataclass(frozen=True)
class EntityAlias:
    canonical_id: str
    aliases: Tuple[str, ...]   # case-insensitive matches


_ALIAS_LOCK = threading.Lock()
_ALIAS_TABLE: dict[str, EntityAlias] = {
    "person:marcus_chen": EntityAlias(
        canonical_id="person:marcus_chen",
        aliases=(
            "marcus", "marcus chen", "chen", "m. chen", "marcus c.",
            "vanta", "vanta ai", "marcus chen (vanta)", "marcus chen vanta ai",
        ),
    ),
    "person:sarah_kim": EntityAlias(
        canonical_id="person:sarah_kim",
        aliases=(
            "sarah", "sarah kim", "s. kim", "kim",
            "drifter", "drifter labs", "sarah kim (drifter)", "sarah kim drifter labs",
        ),
    ),
    "person:james_okafor": EntityAlias(
        canonical_id="person:james_okafor",
        aliases=(
            "james", "james okafor", "okafor", "j. okafor",
            "meridian", "meridian health", "james okafor (meridian)",
        ),
    ),
    "person:priya_sharma": EntityAlias(
        canonical_id="person:priya_sharma",
        aliases=(
            "priya", "priya sharma", "sharma", "p. sharma",
            "the planner", "wedding planner",
        ),
    ),
    "person:dr_adeyemi": EntityAlias(
        canonical_id="person:dr_adeyemi",
        aliases=(
            "doctor", "dr", "dr.", "doc",
            "adeyemi", "dr adeyemi", "dr. adeyemi",
            "my doctor", "the doctor",
        ),
    ),
}


def resolve_entity(hint: Optional[str]) -> str:
    """Map a name / alias / canonical id to a canonical entity id.

    Returns UNRESOLVED_ENTITY when there is no deterministic match. NEVER
    guesses. Provenance integrity requires that an unresolved entity can
    never trigger a whisper.
    """
    if not hint:
        return UNRESOLVED_ENTITY
    h = hint.strip().lower()
    if not h:
        return UNRESOLVED_ENTITY
    with _ALIAS_LOCK:
        # Canonical-id passthrough: only if the id is actually registered.
        for canonical, _entry in _ALIAS_TABLE.items():
            if canonical.lower() == h:
                return canonical
        # Alias match.
        for canonical, entry in _ALIAS_TABLE.items():
            if h in (a.lower() for a in entry.aliases):
                return canonical
    return UNRESOLVED_ENTITY


def register_alias(canonical_id: str, aliases: Iterable[str]) -> None:
    """Register or extend an alias entry. Used by L4 setup / fixture loaders.
    Idempotent — calling twice with the same canonical replaces the entry."""
    with _ALIAS_LOCK:
        _ALIAS_TABLE[canonical_id] = EntityAlias(
            canonical_id=canonical_id,
            aliases=tuple(a.lower() for a in aliases if a),
        )


def known_entities() -> Tuple[str, ...]:
    with _ALIAS_LOCK:
        return tuple(_ALIAS_TABLE.keys())


def all_aliases() -> dict[str, Tuple[str, ...]]:
    """Snapshot of the alias table — canonical_id → tuple of alias strings.
    Used by auto-detection in the live loop to scan utterances for known
    counterparties without exposing the lock to callers."""
    with _ALIAS_LOCK:
        return {c: tuple(e.aliases) for c, e in _ALIAS_TABLE.items()}


# ── Claim store ──────────────────────────────────────────────────────────────


class ClaimStore:
    """In-memory claim store with optional persistence backend.

    The store has two surfaces:

    - The LIVE store (`_claims`) is what get_claims / find_contradictions read.
      It is hydrated from the backend at conversation start and never written
      to mid-conversation.

    - The BUFFER (`_buffer`) holds claims learned during a live conversation.
      It is invisible to get_claims (so the decision sees a stable world model)
      until `consolidate()` runs at conversation end, which moves the buffer
      into the live store and pushes to the backend.

    Nothing in this class touches the network on the read path. The backend
    is consulted only by hydrate_from_backend() and consolidate(), which the
    caller chooses when to invoke — and only off the hot path.
    """

    def __init__(self, backend: Optional[Any] = None) -> None:
        self._claims: list[Claim] = []
        self._buffer: list[Claim] = []
        self._lock = threading.Lock()
        self._backend = backend

    # ── writes ──────────────────────────────────────────────────────────────

    def add_claim(self, claim: Claim, *, persist: bool = True) -> None:
        """Add a claim. `persist=True` (the default) writes to the live store
        and is used during ingest / setup. `persist=False` writes to the
        conversation buffer and is used for any claim learned during a live
        conversation — consolidate() flushes the buffer at conversation end."""
        with self._lock:
            if persist:
                self._claims.append(claim)
            else:
                self._buffer.append(claim)

    def hydrate(self, claims: Iterable[Claim]) -> int:
        """Bulk-load into the live store. Returns count added."""
        added = 0
        with self._lock:
            for c in claims:
                self._claims.append(c)
                added += 1
        return added

    def hydrate_from_backend(self, entity_id: str) -> int:
        """Pull claims for an entity from the persistence backend into the
        live store. Returns count added. Off the hot path — only called from
        start_conversation. Backend errors degrade to zero hydrated claims."""
        if self._backend is None:
            return 0
        try:
            fetched = self._backend.hydrate_for_entity(entity_id)
        except Exception as e:
            print(f"[claims] backend hydrate error for {entity_id}: {e}")
            return 0
        return self.hydrate(fetched)

    # ── reads ───────────────────────────────────────────────────────────────

    def get_claims(self, entity_id: str, attribute: Optional[str] = None) -> list[Claim]:
        """Return live claims for an entity, most recent first. Buffered
        claims are NOT visible here — by design. Use `buffered()` for those."""
        with self._lock:
            results = [
                c for c in self._claims
                if c.entity_id == entity_id
                and (attribute is None or c.attribute == attribute)
            ]
        return sorted(results, key=lambda c: c.timestamp, reverse=True)

    def find_contradictions(
        self, entity_id: str, attribute: str, new_value: Any
    ) -> list[Claim]:
        """Prior claims (entity, attribute) whose value DISAGREES with
        new_value, most recent first, full provenance carried.

        Hard rule: UNRESOLVED_ENTITY ALWAYS returns []. The decision layer
        must not be able to fire on a claim whose entity is unknown."""
        if entity_id == UNRESOLVED_ENTITY:
            return []
        prior = self.get_claims(entity_id, attribute)
        return [c for c in prior if c.value != new_value and c.is_eligible()]

    def snapshot(self) -> Tuple[Claim, ...]:
        """Immutable tuple of the current live store. Used by L3 to freeze a
        conversation-start view that the decision layer reads from."""
        with self._lock:
            return tuple(self._claims)

    def buffered(self) -> Tuple[Claim, ...]:
        """Claims pending consolidation."""
        with self._lock:
            return tuple(self._buffer)

    # ── consolidation (write-after) ─────────────────────────────────────────

    def consolidate(self) -> Tuple[Claim, ...]:
        """Move buffered claims into the live store and push to the backend
        if one is configured. Returns the consolidated tuple.

        Called only at conversation end. Backend failures are logged; the
        claims remain in the in-memory store so the next consolidation can
        retry persistence (manually) or so the demo just keeps working."""
        with self._lock:
            consolidated = tuple(self._buffer)
            self._claims.extend(consolidated)
            self._buffer.clear()
        if consolidated and self._backend is not None:
            try:
                self._backend.persist(consolidated)
            except Exception as e:
                print(
                    f"[claims] backend persist failed; "
                    f"{len(consolidated)} claims remain in-memory: {e}"
                )
        return consolidated

    def reset(self) -> None:
        """For tests / new sessions. Drops live AND buffered state."""
        with self._lock:
            self._claims.clear()
            self._buffer.clear()


# ── Default singleton store ─────────────────────────────────────────────────
# Lazy-init so tests that don't touch the backend pay no Supermemory cost.

_default_store: Optional[ClaimStore] = None
_default_lock = threading.Lock()


class _SupermemoryStoreAdapter:
    """Default backend: delegates to memory.supermemory_store.

    Exists so the ClaimStore's backend-injection seam is preserved (tests
    can still hand in any object with `persist` and `hydrate_for_entity`),
    while production code routes through the durable supermemory_store
    facade — which carries the 2.5s timeout, env-key check, and the
    documented degrade-to-sentinel contract.
    """

    def hydrate_for_entity(self, entity_id: str) -> list:
        from memory.supermemory_store import get_claims  # late import: cycle-safe
        return get_claims(entity_id)

    def persist(self, claims) -> int:
        from memory.supermemory_store import consolidate
        return consolidate(claims)


def get_default_store() -> ClaimStore:
    global _default_store
    with _default_lock:
        if _default_store is None:
            _default_store = ClaimStore(backend=_SupermemoryStoreAdapter())
        return _default_store


def reset_default_store() -> None:
    """For tests. Drops the singleton so the next call rebuilds it."""
    global _default_store
    with _default_lock:
        _default_store = None


def claim_to_dict(c: Claim) -> dict:
    return asdict(c)
