"""L3 — conversation priming and frozen snapshot.

A chief of staff walks into the meeting pre-briefed. That briefing is the
counterparty's profile (L2), prior claims (L1), and any unresolved
commitments — frozen at conversation start. For the duration of the call the
decision agent reads ONLY from this snapshot; nothing mutates it
mid-conversation. New knowledge buffered during the call consolidates into
the persistent store at conversation end (L5).

Why frozen: the decision hot path must read a stable world model. A mid-call
write race could poison the contradiction-catch the demo depends on.

Degradation:
- No counterparty hint, or hint that can't be resolved deterministically:
  briefing is empty (entity = UNRESOLVED_ENTITY, no claims, no profile),
  and the system behaves exactly as it does today (Moss / static fallback
  in retrieval.get_latest_facts).
- No L1 claims for the resolved entity: briefing has the entity id but no
  claims, and retrieval falls through to today's behavior.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Optional, Tuple

from memory.claims import (
    Claim,
    ClaimStore,
    UNRESOLVED_ENTITY,
    get_default_store,
    resolve_entity,
)


# ── Briefing ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Briefing:
    """Immutable snapshot the decision agent reads during a conversation.

    The fields are tuples (not lists) so the briefing is structurally
    hashable / safe to share across threads without locks once published.
    """

    entity_id: str
    counterparty_hint: Optional[str]
    conversation_id: Optional[str]
    claims: Tuple[Claim, ...] = field(default_factory=tuple)
    open_commitments: Tuple[Claim, ...] = field(default_factory=tuple)
    profile: Optional[object] = None       # populated in L2
    external_sources: Tuple[str, ...] = field(default_factory=tuple)  # L4 metadata

    def is_empty(self) -> bool:
        """True when the briefing has no actionable content — either
        unresolved counterparty or no claims/profile. The decision layer
        treats an empty briefing as "no priming" and falls back to today's
        behavior."""
        return (
            self.entity_id == UNRESOLVED_ENTITY
            and not self.claims
            and not self.open_commitments
            and self.profile is None
        )

    def has_claims(self) -> bool:
        return bool(self.claims)

    def rendered_facts(self) -> list[str]:
        """Render claims as the short fact strings the decision prompt
        expects. Each line carries enough provenance for the decision agent
        to cite source + date in its whisper content."""
        return [_render_claim(c) for c in self.claims]


def _emit_briefing_whisper(counterparty_hint, claims, open_commits) -> None:
    """Surface a one-sentence "here's what Chief remembers about this person"
    whisper at conversation start. Fires Haiku to compress N claims into a
    single readable line, then pushes via the bridge so the orb pops it like
    any other whisper. Best-effort; never raises."""
    try:
        if not claims:
            return
        name = counterparty_hint or claims[0].entity_id
        # Compose a compact, raw rendering for the model to summarize.
        lines = [f"- {c.attribute} = {c.value!r} ({c.source})" for c in claims[:12]]
        if open_commits:
            lines.append(f"- open commitments: {len(open_commits)}")
        body = "\n".join(lines)

        import anthropic
        client = anthropic.Anthropic()
        prompt = (
            f"Chief is about to start a live call with {name}. Below are the "
            f"claims Chief remembers about them from prior calls. Write ONE "
            f"sentence (under 28 words) that briefs the user before the call "
            f"begins — lead with the name, then the 2–4 most material facts. "
            f"Be concrete and specific (numbers, dates, names). No preamble, "
            f"no markdown, no quotes.\n\n"
            f"Claims:\n{body}"
        )
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=80,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(getattr(b, "text", "") for b in resp.content).strip()
        if not text or len(text) < 10:
            return

        from bridge import emit_whisper
        emit_whisper(text)
        try:
            import main_call
            main_call.push_whisper(text)
        except Exception:
            pass
        try:
            from voice.tts import speak
            speak(text)
        except Exception:
            pass
        print("[conversation] emitted a briefing alert")
    except Exception as e:
        print(f"[conversation] briefing whisper error: {type(e).__name__}")


def _render_claim(c: Claim) -> str:
    """One-line claim renderer. Matches the format of the existing static
    PAST_CALLS strings so the decision prompt doesn't need re-tuning."""
    # Pull a friendly date from the timestamp if possible.
    date = (c.timestamp or "").split("T", 1)[0] or c.timestamp or "?"
    src = c.source or "?"
    return f"[{src} @ {date}] {c.attribute}={c.value!r}"


# ── Active conversation singleton ───────────────────────────────────────────
# Module-level — one active conversation at a time. start_conversation
# publishes; end_conversation clears. Tests call reset_conversation_state().


_active: Optional[Briefing] = None
_active_lock = threading.Lock()
_active_store: Optional[ClaimStore] = None

# Tracks which entities Chief has briefed in this process so the briefing
# whisper doesn't re-fire every time the conversation session boundary is
# crossed (the 12s idle timer ends and starts sessions frequently).
_briefed_entities: set[str] = set()


def reset_briefed_entities() -> None:
    """For tests / explicit re-brief. Not used in production."""
    global _briefed_entities
    _briefed_entities = set()


def is_active() -> bool:
    with _active_lock:
        return _active is not None


def get_active_briefing() -> Optional[Briefing]:
    with _active_lock:
        return _active


def snapshot_facts() -> Optional[list[str]]:
    """Hot-path entry point for memory.retrieval.get_latest_facts.

    Returns:
        None when no briefing is active OR the briefing has no claims to
        contribute (in which case the caller falls through to today's
        retrieval behavior — preserving 0/20).
        list[str] of rendered claim facts otherwise.
    """
    with _active_lock:
        b = _active
    if b is None or not b.claims:
        return None
    return b.rendered_facts()


# ── Lifecycle ────────────────────────────────────────────────────────────────


def start_conversation(
    counterparty_hint: Optional[str] = None,
    *,
    conversation_id: Optional[str] = None,
    store: Optional[ClaimStore] = None,
) -> Briefing:
    """Freeze a snapshot for the upcoming conversation.

    Pulls claims for the resolved counterparty from the L1 store, separates
    open commitments, and publishes the briefing as the active snapshot.
    OFF the hot path — call this at conversation start, before utterances
    begin flowing.

    If the counterparty hint doesn't resolve, the briefing is empty and the
    system behaves exactly as today (the decision sees Moss / static
    retrieval, not the briefing).
    """
    global _active, _active_store
    s = store or get_default_store()
    _active_store = s

    entity_id = resolve_entity(counterparty_hint)

    claims: tuple[Claim, ...]
    open_commits: tuple[Claim, ...]

    profile: Optional[dict] = None

    if entity_id == UNRESOLVED_ENTITY:
        # No identity, no priming. Stay with today's behavior.
        claims = ()
        open_commits = ()
    else:
        # Hydrate from the persistent backend (no-op when no backend or no
        # SUPERMEMORY_API_KEY).
        s.hydrate_from_backend(entity_id)
        all_claims = tuple(s.get_claims(entity_id))
        claims = all_claims
        open_commits = tuple(
            c for c in all_claims
            if c.attribute == "commitment" and not _is_resolved_commitment(c)
        )

        # L2: pull the counterparty profile off-path. Failures degrade to
        # profile=None — decision pipeline behavior is unchanged.
        try:
            from memory.supermemory_store import get_profile
            profile = get_profile(entity_id)
        except Exception as e:
            print(f"[conversation] profile fetch error: {type(e).__name__}")
            profile = None

    briefing = Briefing(
        entity_id=entity_id,
        counterparty_hint=counterparty_hint,
        conversation_id=conversation_id,
        claims=claims,
        open_commitments=open_commits,
        profile=profile,
    )

    with _active_lock:
        _active = briefing

    # Push the hydrated claims into Moss's live index so the existing
    # prefetch() pipeline can retrieve them semantically alongside the static
    # fixtures. Off-path, non-blocking — no-op when Moss is unavailable.
    if claims:
        try:
            from memory.retrieval import add_session_docs
            docs = [
                (f"claim:{c.entity_id}:{c.attribute}:{c.timestamp}", _render_claim(c))
                for c in claims
            ]
            add_session_docs(docs)
        except Exception as e:
            print(f"[conversation] moss seed error: {type(e).__name__}")

        # Briefing-on-start whisper: intentionally disabled. The briefing
        # was repeating too often and adding noise; judges learn what
        # Chief remembers when the first contradiction fires anyway.
        # (Re-enable by uncommenting; one-shot dedupe set still applies.)
        # global _briefed_entities
        # if entity_id not in _briefed_entities:
        #     _briefed_entities.add(entity_id)
        #     import threading as _th
        #     _th.Thread(target=_emit_briefing_whisper, args=(counterparty_hint, claims, open_commits), daemon=True).start()

    print(
        f"[conversation] start: claims={len(claims)} "
        f"open_commitments={len(open_commits)}"
    )
    return briefing


def end_conversation() -> Tuple[Claim, ...]:
    """Close the conversation. Consolidate the L1 buffer (write-after) and
    clear the active briefing. Returns the consolidated claims."""
    global _active, _active_store
    with _active_lock:
        briefing = _active
        _active = None
    store = _active_store
    _active_store = None

    consolidated: tuple[Claim, ...] = ()
    if store is not None:
        try:
            consolidated = store.consolidate()
        except Exception as e:
            print(f"[conversation] consolidate error: {type(e).__name__}")

    if briefing is not None:
        print(f"[conversation] end: consolidated={len(consolidated)} claims")
    return consolidated


def reset_conversation_state() -> None:
    """For tests. Drops the active briefing without consolidating."""
    global _active, _active_store
    with _active_lock:
        _active = None
    _active_store = None


# ── Helpers ─────────────────────────────────────────────────────────────────


def _is_resolved_commitment(c: Claim) -> bool:
    """A commitment is treated as resolved when its value is the literal
    string 'resolved' (case-insensitive) or when its claim-extra (value
    being a dict) carries a 'resolved': True flag. Hand-authored fixtures
    will commonly use the latter for explicit modeling."""
    v = c.value
    if isinstance(v, str):
        return v.strip().lower() == "resolved"
    if isinstance(v, dict):
        return bool(v.get("resolved"))
    return False


# ── Priming-whisper hook ─────────────────────────────────────────────────────


def eval_priming_whisper(utterance: Optional[str] = None) -> Optional[dict]:
    """Decide whether the briefing warrants an early cross-conversation
    whisper, under the SAME narrow restraint as make_decision.

    Allowed firings:
    - An open unresolved commitment exists for the resolved counterparty
      AND (a) utterance is None (priming-at-start mode) OR
          (b) the utterance keywords-overlaps the commitment topic.
    - That's it. Speculative "context" is not allowed.

    Returns:
        {"action": "whisper", "content": str, "provenance": str} or None.
        None means: stay with whatever the regular decision pipeline does.
        Caller treats None as silent — the regular decision agent still
        runs on the utterance.

    Hard rules:
    - No active briefing -> None.
    - Briefing entity is UNRESOLVED -> None.
    - No open commitments -> None.
    - Commitment without source or with unresolved entity -> None.
    """
    with _active_lock:
        b = _active
    if b is None:
        return None
    if b.entity_id == UNRESOLVED_ENTITY:
        return None
    if not b.open_commitments:
        return None

    candidate = b.open_commitments[0]
    if not candidate.is_eligible():
        return None

    if utterance is not None:
        if not _commitment_topic_in_utterance(candidate, utterance):
            return None

    content = _render_commitment_whisper(candidate)
    provenance = candidate.provenance()
    return {"action": "whisper", "content": content, "provenance": provenance}


def _commitment_topic_in_utterance(c: Claim, utterance: str) -> bool:
    """Restraint-first overlap check. Looks for any token from the
    commitment value (when string) in the utterance, case-insensitive."""
    if not utterance:
        return False
    u = utterance.lower()
    tokens: list[str] = []
    v = c.value
    if isinstance(v, str):
        tokens = [t for t in v.lower().replace("'", " ").split() if len(t) > 3]
    elif isinstance(v, dict):
        topic = v.get("topic") or v.get("about") or ""
        tokens = [t for t in str(topic).lower().split() if len(t) > 3]
    return any(t in u for t in tokens)


def _render_commitment_whisper(c: Claim) -> str:
    """One-sentence rendering of an open commitment for the orb."""
    v = c.value
    if isinstance(v, dict):
        topic = v.get("topic") or v.get("about") or "commitment"
        when = v.get("by") or v.get("date") or c.timestamp.split("T", 1)[0]
        return f"Open from {c.source}: owes you {topic} (by {when})."
    return f"Open from {c.source}: {v}"


# ── L2 personalization hook ──────────────────────────────────────────────────
# Restraint rule: personalization only ENRICHES an existing whisper. It never
# manufactures new ones. The hook receives the model's whisper content and
# returns either the original (no profile / no safe substitution) or a
# minimally rewritten version where third-person pronouns get the
# counterparty's real name. Net effect on decision behavior is zero unless
# the briefing carries a profile.

_PRONOUN_RE = None


def _pronoun_re():
    global _PRONOUN_RE
    if _PRONOUN_RE is None:
        import re
        # Match start-of-content "He"/"She" only. Conservative: we don't try
        # to fix every pronoun in the sentence; just the leading subject of
        # the kind of whisper the decision agent currently emits
        # ("He said X. He just said Y.").
        _PRONOUN_RE = re.compile(r"^(He|She)\b")
    return _PRONOUN_RE


def personalize_whisper(content: str, briefing: Optional[Briefing] = None) -> str:
    """Return `content` with leading "He"/"She" replaced by the briefing's
    counterparty name when (and ONLY when) the briefing carries a profile
    with a usable `name` field.

    Parity guarantee:
      personalize_whisper(c, None)            == c
      personalize_whisper(c, briefing_no_profile) == c
      personalize_whisper("Nothing leading.", briefing) == "Nothing leading."

    The hook never CHANGES the decision (silent stays silent; whisper stays
    whisper). It only changes the rendered content when the personalization
    trigger fires.
    """
    if not content:
        return content
    if briefing is None:
        briefing = get_active_briefing()
    if briefing is None or briefing.profile is None:
        return content
    name = None
    if isinstance(briefing.profile, dict):
        name = briefing.profile.get("name")
    if not name or not isinstance(name, str):
        return content
    first_name = name.split()[0]
    if not first_name:
        return content
    return _pronoun_re().sub(first_name, content, count=1)
