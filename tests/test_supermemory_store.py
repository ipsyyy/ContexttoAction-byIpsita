"""Tests for memory.supermemory_store.

Cover:
- env-key absence and SDK absence both return the documented sentinels
  without raising
- happy-path round-trip with the Supermemory client mocked
- 2.5s hard deadline kicks in on slow calls
- as_of_date metadata is attached to every write
- profile API round-trip
- hard-down is INVISIBLE to the decision pipeline (parity with today)
"""

import datetime
import time
from unittest.mock import MagicMock, patch

import memory.supermemory_store as store
from memory.claims import Claim


# ── Helpers ──────────────────────────────────────────────────────────────────


def _mk_claim(value="18 months", entity="person:marcus_chen",
              attribute="runway", source="prior_call:apr15",
              timestamp="2026-04-15T10:00:00") -> Claim:
    return Claim(
        entity_id=entity,
        attribute=attribute,
        value=value,
        source=source,
        timestamp=timestamp,
        conversation_id=None,
        confidence=1.0,
    )


def _make_search_response(claims):
    """Mimic a Supermemory SearchDocumentsResponse with .results = [docs]."""
    import json
    resp = MagicMock()
    docs = []
    for c in claims:
        d = MagicMock()
        d.content = json.dumps({
            "entity_id": c.entity_id,
            "attribute": c.attribute,
            "value": c.value,
            "source": c.source,
            "timestamp": c.timestamp,
            "conversation_id": c.conversation_id,
            "confidence": c.confidence,
        })
        docs.append(d)
    resp.results = docs
    return resp


def setup_function(_fn):
    """Each test starts with a clean module state."""
    store.reset_client()


# ── env / SDK absence: every function degrades silently ─────────────────────


def test_no_api_key_is_unavailable(monkeypatch):
    monkeypatch.delenv("SUPERMEMORY_API_KEY", raising=False)
    store.reset_client()
    assert store.is_available() is False
    assert store.add_claim(_mk_claim()) is False
    assert store.get_claims("person:marcus_chen") == []
    assert store.contradiction_query("person:marcus_chen", "runway", "12") == []
    assert store.get_profile("person:marcus_chen") is None
    assert store.put_profile("person:marcus_chen", {"traits": []}) is False
    assert store.consolidate([_mk_claim(), _mk_claim()]) == 0


def test_empty_entity_id_handled():
    """get_claims / get_profile must not call the network with no entity."""
    with patch.object(store, "_get_client", return_value=MagicMock()) as gc:
        assert store.get_claims("") == []
        assert store.get_profile("") is None
        # _get_client may have been called, but the SDK methods must not have.
        gc.return_value.search.documents.assert_not_called()


# ── happy-path round-trips ──────────────────────────────────────────────────


def test_add_claim_pushes_with_tags_and_metadata():
    fake = MagicMock()
    fake.add.return_value = MagicMock()  # ok
    with patch.object(store, "_get_client", return_value=fake):
        ok = store.add_claim(_mk_claim())
    assert ok is True
    fake.add.assert_called_once()
    kwargs = fake.add.call_args.kwargs
    assert "claim" in kwargs.get("content", "") or "18 months" in kwargs.get("content", "")
    assert store.TAG_CLAIM in kwargs["container_tags"]
    assert any(t.startswith(store.TAG_ENTITY_PREFIX) for t in kwargs["container_tags"])
    assert kwargs["metadata"]["as_of_date"] == datetime.date.today().isoformat()
    assert kwargs["metadata"]["entity_id"] == "person:marcus_chen"
    assert kwargs["metadata"]["attribute"] == "runway"
    assert kwargs["timeout"] == store.SUPERMEMORY_TIMEOUT_SECONDS


def test_get_claims_round_trips():
    fake = MagicMock()
    fake.search.documents.return_value = _make_search_response([
        _mk_claim(value="18 months", timestamp="2026-04-15T10:00:00"),
        _mk_claim(value="20 months", timestamp="2026-03-02T10:00:00"),
    ])
    with patch.object(store, "_get_client", return_value=fake):
        out = store.get_claims("person:marcus_chen", attribute="runway")
    assert len(out) == 2
    # Sorted most-recent-first by timestamp.
    assert out[0].timestamp.startswith("2026-04")
    assert out[1].timestamp.startswith("2026-03")
    assert out[0].value == "18 months"


def test_contradiction_query_filters_by_value():
    fake = MagicMock()
    fake.search.documents.return_value = _make_search_response([
        _mk_claim(value="18 months"),
        _mk_claim(value="12 months"),
    ])
    with patch.object(store, "_get_client", return_value=fake):
        contradictions = store.contradiction_query(
            "person:marcus_chen", "runway", "12 months"
        )
    assert len(contradictions) == 1
    assert contradictions[0].value == "18 months"


def test_get_profile_round_trip():
    fake = MagicMock()
    profile_doc = MagicMock()
    import json
    profile_doc.content = json.dumps({
        "traits": ["decisive"],
        "open_items": ["cap table"],
        "notes": [],
    })
    resp = MagicMock()
    resp.results = [profile_doc]
    fake.search.documents.return_value = resp
    with patch.object(store, "_get_client", return_value=fake):
        prof = store.get_profile("person:marcus_chen")
    assert prof == {"traits": ["decisive"], "open_items": ["cap table"], "notes": []}


def test_put_profile_includes_as_of_date():
    fake = MagicMock()
    fake.add.return_value = MagicMock()
    with patch.object(store, "_get_client", return_value=fake):
        ok = store.put_profile("person:marcus_chen", {"traits": ["x"]})
    assert ok is True
    kwargs = fake.add.call_args.kwargs
    assert kwargs["metadata"]["as_of_date"] == datetime.date.today().isoformat()
    assert kwargs["metadata"]["kind"] == "profile"
    assert kwargs["custom_id"] == "chief:profile:person:marcus_chen"


def test_consolidate_batches_per_claim():
    fake = MagicMock()
    fake.add.return_value = MagicMock()
    with patch.object(store, "_get_client", return_value=fake):
        pushed = store.consolidate([_mk_claim(), _mk_claim(timestamp="2026-04-16T00:00:00")])
    assert pushed == 2
    assert fake.add.call_count == 2


def test_consolidate_partial_failure_continues():
    """One bad push must not abort the batch."""
    fake = MagicMock()
    fake.add.side_effect = [RuntimeError("bad"), MagicMock()]
    with patch.object(store, "_get_client", return_value=fake):
        pushed = store.consolidate([_mk_claim(), _mk_claim(timestamp="2026-04-16T00:00:00")])
    assert pushed == 1


# ── timeout + hard-down degradation ─────────────────────────────────────────


def test_add_claim_timeout_returns_false():
    fake = MagicMock()

    def slow_add(*_a, **_kw):
        time.sleep(1.5)  # > the patched timeout below

    fake.add.side_effect = slow_add
    with patch.object(store, "_get_client", return_value=fake), \
         patch.object(store, "SUPERMEMORY_TIMEOUT_SECONDS", 0.1):
        ok = store.add_claim(_mk_claim())
    assert ok is False


def test_get_claims_timeout_returns_empty():
    fake = MagicMock()

    def slow_search(*_a, **_kw):
        time.sleep(1.5)

    fake.search.documents.side_effect = slow_search
    with patch.object(store, "_get_client", return_value=fake), \
         patch.object(store, "SUPERMEMORY_TIMEOUT_SECONDS", 0.1):
        out = store.get_claims("person:marcus_chen")
    assert out == []


def test_get_claims_sdk_exception_returns_empty():
    fake = MagicMock()
    fake.search.documents.side_effect = RuntimeError("network down")
    with patch.object(store, "_get_client", return_value=fake):
        out = store.get_claims("person:marcus_chen")
    assert out == []


def test_put_profile_sdk_exception_returns_false():
    fake = MagicMock()
    fake.add.side_effect = RuntimeError("network down")
    with patch.object(store, "_get_client", return_value=fake):
        ok = store.put_profile("person:marcus_chen", {"traits": []})
    assert ok is False


# ── malformed payload defensive parsing ─────────────────────────────────────


def test_get_claims_skips_unparseable_docs():
    fake = MagicMock()
    bad = MagicMock(); bad.content = "{not json"
    good = MagicMock()
    import json
    good.content = json.dumps({
        "entity_id": "person:marcus_chen",
        "attribute": "runway",
        "value": "18 months",
        "source": "prior_call:apr15",
        "timestamp": "2026-04-15T10:00:00",
        "conversation_id": None,
        "confidence": 1.0,
    })
    resp = MagicMock()
    resp.results = [bad, good]
    fake.search.documents.return_value = resp
    with patch.object(store, "_get_client", return_value=fake):
        out = store.get_claims("person:marcus_chen")
    assert len(out) == 1
    assert out[0].value == "18 months"


# ── decision-pipeline parity: down-path is invisible to decision today ──────
# (Task 21 will wire supermemory_store into L3; this test pins the
# baseline that the store unavailable === existing behavior.)


def test_store_unavailable_does_not_break_decision_imports(monkeypatch):
    """Importing the decision pipeline while the store is unavailable must
    not raise — confirms the soft-dependency contract."""
    monkeypatch.delenv("SUPERMEMORY_API_KEY", raising=False)
    store.reset_client()
    # Importing decision_agent + cache builds the message with a date but
    # no Supermemory facts; this should be entirely unaffected.
    import importlib
    import memory.cache as cache_mod
    importlib.reload(cache_mod)
    msgs = cache_mod.build_messages("b", "u", [])
    # Today's date is injected as a separate text block; assert it survived.
    text_blocks = "".join(b["text"] for b in msgs[0]["content"])
    assert "Today:" in text_blocks
