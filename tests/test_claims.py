"""L1 claim store tests.

Cover:
- Entity resolution (alias hit, alias miss = UNRESOLVED, canonical passthrough,
  register_alias).
- Claim eligibility (provenance integrity: no source / unresolved entity ->
  not eligible).
- Add / get / contradiction queries (most recent first, unresolved entity ->
  never contradicts).
- Absence/cold behavior (empty store inert).
- Buffer / consolidate behavior (read-live/write-after).
- Backend persistence dispatch + failure swallowing.
"""

from memory.claims import (
    Claim,
    ClaimStore,
    UNRESOLVED_ENTITY,
    resolve_entity,
    register_alias,
)


def _claim(**kwargs) -> Claim:
    defaults = dict(
        entity_id="person:marcus_chen",
        attribute="runway_months",
        value=18,
        source="prior_call:apr15",
        timestamp="2026-04-15T10:00:00Z",
        conversation_id="apr15",
        confidence=1.0,
    )
    defaults.update(kwargs)
    return Claim(**defaults)


# ── resolution ───────────────────────────────────────────────────────────────

def test_resolve_known_alias_matches():
    assert resolve_entity("Marcus") == "person:marcus_chen"
    assert resolve_entity("marcus chen") == "person:marcus_chen"
    assert resolve_entity("M. CHEN") == "person:marcus_chen"


def test_resolve_unknown_returns_sentinel():
    assert resolve_entity("Jane Doe") == UNRESOLVED_ENTITY
    assert resolve_entity("") == UNRESOLVED_ENTITY
    assert resolve_entity(None) == UNRESOLVED_ENTITY
    assert resolve_entity("   ") == UNRESOLVED_ENTITY


def test_resolve_canonical_id_passthrough():
    assert resolve_entity("person:marcus_chen") == "person:marcus_chen"


def test_resolve_unregistered_canonical_id_is_unresolved():
    # A canonical-looking id that isn't in the alias table doesn't get
    # auto-blessed. Provenance integrity: never guess identity.
    assert resolve_entity("person:not_real") == UNRESOLVED_ENTITY


def test_register_alias_extends_table():
    register_alias("person:test_alex", ("Alex", "alex doe"))
    assert resolve_entity("alex") == "person:test_alex"
    assert resolve_entity("Alex Doe") == "person:test_alex"  # case-insensitive
    # A name not in the alias list does NOT resolve.
    assert resolve_entity("Alex Smith") == UNRESOLVED_ENTITY


# ── claim eligibility (provenance integrity) ────────────────────────────────

def test_claim_with_source_and_resolved_entity_is_eligible():
    assert _claim().is_eligible()


def test_claim_without_source_not_eligible():
    assert not _claim(source="").is_eligible()


def test_claim_with_unresolved_entity_not_eligible():
    assert not _claim(entity_id=UNRESOLVED_ENTITY).is_eligible()


def test_claim_provenance_string_carries_source_and_timestamp():
    c = _claim(source="prior_call:apr15", timestamp="2026-04-15T10:00:00Z")
    p = c.provenance()
    assert "prior_call:apr15" in p
    assert "2026-04-15T10:00:00Z" in p


# ── add / get ────────────────────────────────────────────────────────────────

def test_add_and_get_claims_most_recent_first():
    store = ClaimStore()
    store.add_claim(_claim(value=18, timestamp="2026-04-15T10:00:00Z"))
    store.add_claim(_claim(value=12, timestamp="2026-05-17T14:00:00Z"))
    results = store.get_claims("person:marcus_chen", "runway_months")
    assert len(results) == 2
    assert results[0].value == 12  # most recent first
    assert results[1].value == 18


def test_get_claims_by_entity_only_returns_all_attributes():
    store = ClaimStore()
    store.add_claim(_claim(attribute="runway_months", value=18))
    store.add_claim(_claim(attribute="arr_usd", value=1_200_000))
    by_entity = store.get_claims("person:marcus_chen")
    assert len(by_entity) == 2


def test_get_claims_unknown_entity_returns_empty():
    store = ClaimStore()
    store.add_claim(_claim())
    assert store.get_claims("person:nobody") == []


# ── contradiction query ─────────────────────────────────────────────────────

def test_find_contradictions_returns_prior_disagreeing_values():
    store = ClaimStore()
    store.add_claim(_claim(value=18, timestamp="2026-04-15T10:00:00Z"))
    store.add_claim(_claim(value=20, timestamp="2026-03-02T10:00:00Z"))
    store.add_claim(_claim(value=18, timestamp="2026-02-01T10:00:00Z"))  # agrees w/ first
    contradictions = store.find_contradictions(
        "person:marcus_chen", "runway_months", 12
    )
    # All three disagree with 12.
    assert len(contradictions) == 3
    # Most recent first.
    assert contradictions[0].timestamp == "2026-04-15T10:00:00Z"


def test_find_contradictions_filters_out_agreements():
    store = ClaimStore()
    store.add_claim(_claim(value=18))
    assert store.find_contradictions("person:marcus_chen", "runway_months", 18) == []


def test_find_contradictions_unresolved_entity_returns_empty():
    """Hard rule: unresolved entity can never trigger via contradiction."""
    store = ClaimStore()
    store.add_claim(_claim())
    assert store.find_contradictions(UNRESOLVED_ENTITY, "runway_months", 12) == []


def test_find_contradictions_drops_ineligible_claims():
    """A claim with no source must not be returned as a contradiction even
    if its value disagrees - provenance integrity."""
    store = ClaimStore()
    store.add_claim(_claim(value=18, source=""))   # ineligible
    contradictions = store.find_contradictions(
        "person:marcus_chen", "runway_months", 12
    )
    assert contradictions == []


def test_find_contradictions_carries_full_provenance():
    store = ClaimStore()
    store.add_claim(
        _claim(value=18, source="prior_call:apr15",
               timestamp="2026-04-15T10:00:00Z", conversation_id="apr15")
    )
    [c] = store.find_contradictions("person:marcus_chen", "runway_months", 12)
    assert c.source == "prior_call:apr15"
    assert c.timestamp == "2026-04-15T10:00:00Z"
    assert c.conversation_id == "apr15"


# ── absence / cold behavior ─────────────────────────────────────────────────

def test_empty_store_is_inert():
    store = ClaimStore()
    assert store.get_claims("person:marcus_chen") == []
    assert store.find_contradictions("person:marcus_chen", "runway_months", 12) == []
    assert store.snapshot() == ()
    assert store.buffered() == ()


def test_reset_clears_both_live_and_buffer():
    store = ClaimStore()
    store.add_claim(_claim())
    store.add_claim(_claim(value=12), persist=False)
    assert store.snapshot() != ()
    assert store.buffered() != ()
    store.reset()
    assert store.snapshot() == ()
    assert store.buffered() == ()


# ── buffer / consolidate (write-after) ──────────────────────────────────────

def test_buffered_claims_invisible_to_live_get_claims():
    """Mid-conversation writes (persist=False) must NOT bleed into the
    snapshot the decision sees."""
    store = ClaimStore()
    store.add_claim(_claim(value=12), persist=False)
    assert store.get_claims("person:marcus_chen") == []
    assert store.buffered()[0].value == 12


def test_consolidate_promotes_buffer_to_live():
    store = ClaimStore()
    store.add_claim(_claim(value=12, timestamp="2026-05-17T14:00:00Z"), persist=False)
    consolidated = store.consolidate()
    assert len(consolidated) == 1
    [c] = store.get_claims("person:marcus_chen")
    assert c.value == 12
    assert store.buffered() == ()


def test_consolidate_returns_empty_on_no_buffer():
    store = ClaimStore()
    assert store.consolidate() == ()


def test_consolidate_pushes_to_backend_when_present():
    pushed: list = []

    class FakeBackend:
        def persist(self, claims):
            pushed.extend(claims)
            return len(pushed)

    store = ClaimStore(backend=FakeBackend())
    store.add_claim(_claim(value=12), persist=False)
    store.consolidate()
    assert len(pushed) == 1
    assert pushed[0].value == 12


def test_consolidate_swallows_backend_errors():
    """Backend failure must not lose claims from the in-memory store."""

    class BrokenBackend:
        def persist(self, claims):
            raise RuntimeError("network down")

    store = ClaimStore(backend=BrokenBackend())
    store.add_claim(_claim(value=12), persist=False)
    consolidated = store.consolidate()
    assert len(consolidated) == 1
    # Claims still in the in-memory live store
    assert len(store.get_claims("person:marcus_chen")) == 1


# ── backend hydrate ─────────────────────────────────────────────────────────

def test_hydrate_from_backend_swallows_errors():
    class BrokenBackend:
        def hydrate_for_entity(self, entity_id):
            raise RuntimeError("offline")
    store = ClaimStore(backend=BrokenBackend())
    n = store.hydrate_from_backend("person:marcus_chen")
    assert n == 0
    assert store.get_claims("person:marcus_chen") == []


def test_hydrate_from_backend_loads_into_live_store():
    expected = [_claim(value=18), _claim(value=20, timestamp="2026-03-02T10:00:00Z")]

    class FakeBackend:
        def hydrate_for_entity(self, entity_id):
            assert entity_id == "person:marcus_chen"
            return expected

    store = ClaimStore(backend=FakeBackend())
    n = store.hydrate_from_backend("person:marcus_chen")
    assert n == 2
    assert len(store.get_claims("person:marcus_chen")) == 2


def test_hydrate_no_backend_no_op():
    store = ClaimStore(backend=None)
    assert store.hydrate_from_backend("person:marcus_chen") == 0
