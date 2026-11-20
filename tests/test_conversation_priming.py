"""L3 priming + L5 consolidation through supermemory_store.

The contract these tests pin:

- start_conversation() with Supermemory UP populates the briefing with the
  claims the store returns.
- start_conversation() with Supermemory DOWN (or no key) returns an empty
  briefing, and the rest of the system behaves exactly as it does today —
  the decision still reads its static / Moss fallback, the 0/20 invariant
  is unaffected.
- end_conversation() flushes the buffer to supermemory_store.consolidate.
- Mid-conversation, the active briefing's claim list does NOT change just
  because we added to the buffer — write-after.
"""

from unittest.mock import patch

import memory.claims as claims_module
import memory.conversation as conv
import memory.supermemory_store as store
from memory.claims import (
    Claim,
    ClaimStore,
    UNRESOLVED_ENTITY,
    _SupermemoryStoreAdapter,
)


def _claim(**kw) -> Claim:
    defaults = dict(
        entity_id="person:marcus_chen",
        attribute="runway_months",
        value="18",
        source="prior_call:apr15",
        timestamp="2026-04-15T10:00:00",
        conversation_id="apr15",
        confidence=1.0,
    )
    defaults.update(kw)
    return Claim(**defaults)


def setup_function(_fn):
    """Fresh module state for each test."""
    conv.reset_conversation_state()
    claims_module.reset_default_store()
    store.reset_client()


# ── L3 priming round-trip via the default supermemory_store adapter ─────────


def test_start_conversation_hydrates_from_supermemory_store():
    fixture = [
        _claim(value="18", timestamp="2026-04-15T10:00:00"),
        _claim(value="20", timestamp="2026-03-02T10:00:00"),
    ]
    # Patch supermemory_store.get_claims directly — the adapter routes here.
    with patch.object(store, "get_claims", return_value=fixture):
        # Use a fresh store with the production adapter to confirm the
        # adapter actually goes through supermemory_store.
        s = ClaimStore(backend=_SupermemoryStoreAdapter())
        briefing = conv.start_conversation("marcus", store=s)

    assert briefing.entity_id == "person:marcus_chen"
    assert len(briefing.claims) == 2
    assert briefing.claims[0].value in ("18", "20")
    # snapshot_facts must surface these facts to the hot-path reader.
    facts = conv.snapshot_facts()
    assert facts is not None
    assert any("runway_months" in f for f in facts)


def test_start_conversation_supermemory_down_yields_empty_briefing():
    """Hard-down: get_claims returns [] (its degradation sentinel). The
    briefing has no claims and the decision falls back to today's
    Moss / static path — 0/20 parity is preserved by definition."""
    with patch.object(store, "get_claims", return_value=[]):
        s = ClaimStore(backend=_SupermemoryStoreAdapter())
        briefing = conv.start_conversation("marcus", store=s)

    assert briefing.entity_id == "person:marcus_chen"
    assert briefing.claims == ()
    # snapshot_facts returns None so retrieval falls through to its own logic.
    assert conv.snapshot_facts() is None


def test_unresolved_counterparty_skips_store_entirely():
    """Provenance integrity: if we can't deterministically identify the
    counterparty, we don't query Supermemory at all and the briefing is
    inert. The decision behaves as it does today."""
    with patch.object(store, "get_claims") as mock_get:
        s = ClaimStore(backend=_SupermemoryStoreAdapter())
        briefing = conv.start_conversation("nobody-i-know", store=s)
        # Adapter is never called because conversation.start_conversation
        # short-circuits on UNRESOLVED_ENTITY.
        mock_get.assert_not_called()

    assert briefing.entity_id == UNRESOLVED_ENTITY
    assert briefing.claims == ()
    assert conv.snapshot_facts() is None


# ── L5 write-after via supermemory_store.consolidate ────────────────────────


def test_mid_conversation_buffer_invisible_to_active_briefing():
    """Buffered claims must NOT appear in the briefing the decision sees."""
    s = ClaimStore(backend=_SupermemoryStoreAdapter())
    with patch.object(store, "get_claims", return_value=[_claim(value="18")]):
        briefing = conv.start_conversation("marcus", store=s)

    assert len(briefing.claims) == 1
    # Live-write a new claim into the conversation buffer.
    s.add_claim(_claim(value="12", timestamp="2026-05-17T14:00:00"), persist=False)

    # Mid-conversation briefing.claims is unchanged.
    active = conv.get_active_briefing()
    assert active is not None
    assert len(active.claims) == 1
    # Buffer is populated for end-of-conversation consolidation.
    assert len(s.buffered()) == 1


def test_end_conversation_flushes_buffer_through_supermemory_store():
    """consolidate() pushes the buffered claims to supermemory_store and
    moves them into the live in-memory store."""
    s = ClaimStore(backend=_SupermemoryStoreAdapter())
    with patch.object(store, "get_claims", return_value=[]):
        conv.start_conversation("marcus", store=s)
    s.add_claim(_claim(value="12", timestamp="2026-05-17T14:00:00"), persist=False)

    with patch.object(store, "consolidate", return_value=1) as cons:
        consolidated = conv.end_conversation()

    cons.assert_called_once()
    pushed_claims = list(cons.call_args.args[0])
    assert len(pushed_claims) == 1
    assert pushed_claims[0].value == "12"
    assert len(consolidated) == 1
    # The active briefing is cleared.
    assert conv.get_active_briefing() is None


def test_consolidate_failure_does_not_lose_claims_locally():
    """If supermemory_store.consolidate degrades to 0 (or raises), the
    buffered claims still end up in the in-memory live store so the
    session continues."""
    s = ClaimStore(backend=_SupermemoryStoreAdapter())
    with patch.object(store, "get_claims", return_value=[]):
        conv.start_conversation("marcus", store=s)
    s.add_claim(_claim(value="12", timestamp="2026-05-17T14:00:00"), persist=False)

    with patch.object(store, "consolidate", side_effect=RuntimeError("net down")):
        consolidated = conv.end_conversation()

    # The end-of-conversation call still returned the consolidated tuple
    # (the in-memory promotion succeeded; the durable push failed silently).
    assert len(consolidated) == 1
    # And the claim is in the live in-memory store, available for future
    # in-process reads.
    assert len(s.get_claims("person:marcus_chen")) == 1
