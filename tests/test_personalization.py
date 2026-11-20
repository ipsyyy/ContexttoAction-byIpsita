"""L2 personalization parity tests.

The personalization hook ENRICHES a whisper's rendering when the briefing
carries a counterparty profile. It never changes the decision and never
manufactures a new whisper.

Parity rule under test:
  Same input + no profile           → identical content.
  Same input + profile without name → identical content.
  Same input + profile with name    → only the leading pronoun substituted.
"""

from unittest.mock import patch

import memory.claims as claims_module
import memory.conversation as conv
import memory.supermemory_store as store
from memory.claims import Claim, ClaimStore, _SupermemoryStoreAdapter


def _claim() -> Claim:
    return Claim(
        entity_id="person:marcus_chen",
        attribute="runway_months",
        value="18",
        source="prior_call:apr15",
        timestamp="2026-04-15T10:00:00",
        conversation_id="apr15",
        confidence=1.0,
    )


def setup_function(_fn):
    conv.reset_conversation_state()
    claims_module.reset_default_store()
    store.reset_client()


def test_briefing_carries_profile_from_supermemory_store():
    fixture_profile = {
        "name": "Marcus Chen",
        "role": "Founder & CEO",
        "traits": ["meticulous_on_metrics"],
        "open_items": ["cap table"],
        "notes": [],
    }
    s = ClaimStore(backend=_SupermemoryStoreAdapter())
    with patch.object(store, "get_claims", return_value=[_claim()]), \
         patch.object(store, "get_profile", return_value=fixture_profile):
        briefing = conv.start_conversation("marcus", store=s)

    assert briefing.profile == fixture_profile


def test_unresolved_counterparty_does_not_fetch_profile():
    """Provenance integrity: an unresolved counterparty must not trigger any
    Supermemory lookup, including the profile fetch."""
    s = ClaimStore(backend=_SupermemoryStoreAdapter())
    with patch.object(store, "get_profile") as gp:
        briefing = conv.start_conversation("nobody-i-know", store=s)
        gp.assert_not_called()
    assert briefing.profile is None


def test_personalize_whisper_no_profile_returns_unchanged():
    """Parity: with no profile, content is verbatim."""
    s = ClaimStore(backend=_SupermemoryStoreAdapter())
    with patch.object(store, "get_claims", return_value=[]), \
         patch.object(store, "get_profile", return_value=None):
        conv.start_conversation("marcus", store=s)

    content = "He said 18 months runway on April 15. He just said 12."
    assert conv.personalize_whisper(content) == content


def test_personalize_whisper_with_profile_substitutes_leading_pronoun():
    """Personalization trigger: profile present + name set + leading "He"
    → replaced with first name."""
    profile = {"name": "Marcus Chen", "traits": []}
    s = ClaimStore(backend=_SupermemoryStoreAdapter())
    with patch.object(store, "get_claims", return_value=[]), \
         patch.object(store, "get_profile", return_value=profile):
        conv.start_conversation("marcus", store=s)

    content = "He said 18 months runway on April 15. He just said 12."
    out = conv.personalize_whisper(content)
    assert out.startswith("Marcus ")
    # Only the LEADING pronoun is touched — minimal-surgery hook.
    assert "He just said 12." in out


def test_personalize_whisper_no_leading_pronoun_unchanged():
    """If the whisper doesn't start with He/She, nothing changes."""
    profile = {"name": "Marcus Chen"}
    s = ClaimStore(backend=_SupermemoryStoreAdapter())
    with patch.object(store, "get_claims", return_value=[]), \
         patch.object(store, "get_profile", return_value=profile):
        conv.start_conversation("marcus", store=s)

    content = "Past said 18 months runway. They just said 12."
    assert conv.personalize_whisper(content) == content


def test_personalize_whisper_profile_without_name_unchanged():
    """Parity: a profile without a usable name field must not change content."""
    profile = {"traits": ["meticulous_on_metrics"]}
    s = ClaimStore(backend=_SupermemoryStoreAdapter())
    with patch.object(store, "get_claims", return_value=[]), \
         patch.object(store, "get_profile", return_value=profile):
        conv.start_conversation("marcus", store=s)

    content = "He said 18 months runway on April 15. He just said 12."
    assert conv.personalize_whisper(content) == content


def test_personalize_whisper_empty_content_returns_empty():
    """Defensive: empty string in, empty string out."""
    assert conv.personalize_whisper("") == ""


def test_profile_fetch_failure_does_not_break_priming():
    """Hard-down on get_profile must not block conversation start."""
    s = ClaimStore(backend=_SupermemoryStoreAdapter())
    with patch.object(store, "get_claims", return_value=[_claim()]), \
         patch.object(store, "get_profile", side_effect=RuntimeError("net")):
        briefing = conv.start_conversation("marcus", store=s)

    # Claims still hydrated.
    assert len(briefing.claims) == 1
    # Profile is None — degraded silently.
    assert briefing.profile is None
