"""Per-utterance claim extractor.

Given a final utterance + the active conversation's counterparty, ask Haiku
to emit zero or more structured claims (entity_id, attribute, value). Writes
land in the L1 buffer; consolidate() at conversation end flushes them to
Supermemory.

Off the decision hot path. Fires in a daemon thread after `make_decision`
returns. Failures degrade silently — a dropped utterance never blocks the
loop.

The output schema is intentionally narrow:
    {"claims": [{"attribute": str, "value": str|number, "confidence": 0..1}, ...]}

We do NOT ask the model to invent entity ids. The active briefing's
counterparty entity is attached automatically — the model only emits
attribute/value pairs about that counterparty. Anything else is rejected.
"""

from __future__ import annotations

import datetime
import json
import re
import threading
from typing import Any, Iterable, Optional

import anthropic

from memory.claims import Claim, UNRESOLVED_ENTITY, get_default_store
from memory.conversation import get_active_briefing


_client = anthropic.Anthropic()


# Tight system prompt. Output strict JSON. Conservatism wins; spurious claims
# poison Supermemory and downstream contradiction-catching.
EXTRACTOR_PROMPT = """
You extract structured business facts about a single counterparty from one utterance.

The counterparty in this conversation is: {counterparty}

Output strict JSON of the form:
{{"claims": [{{"attribute": "<snake_case_attribute>", "value": <string or number>, "confidence": <0..1>}}, ...]}}

Extract a claim ONLY when ALL of these hold:
1. The utterance asserts a present-tense, externally checkable fact ABOUT the counterparty or their company (runway, ARR, headcount, churn, burn, customer counts, named customers, product launches, pricing, deal sizes, pipeline counts).
2. The fact has a specific value (a number, a date, a named entity, a percentage).
3. The fact is NOT hedged into uncertainty ("maybe", "could", "hoping to") AND NOT future-tense.

Allowed attributes (use these canonical names; pick the closest match):
  runway_months, runway_statement, arr, arr_growth_rate_mom, monthly_burn,
  team_size, hiring_count, churn_monthly_pct, nps, dau, gross_margin_pct,
  hospital_pilots, sales_cycle_months, largest_customer, largest_customer_acv,
  product_launch, commitment, fundraise_status

Reject the utterance (output {{"claims": []}}) when:
- The speaker is restating an opinion, plan, projection, or aspiration.
- The utterance is small talk, a question, or a hedge.
- The value is qualitative without a checkable number ("good", "growing fast").
- The fact is about someone OTHER than the counterparty.

Output JSON only. No markdown. No prose. No code fences.
""".strip()


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _parse(text: str) -> dict:
    s = (text or "").strip()
    s = _FENCE_RE.sub("", s).strip()
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
    return {"claims": []}


def _extract(utterance: str, entity_id: str, counterparty_label: str, conversation_id: Optional[str]) -> list[Claim]:
    """Call Haiku, parse, return Claim objects. Empty list on any failure."""
    if not utterance.strip():
        return []
    try:
        resp = _client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=240,
            system=EXTRACTOR_PROMPT.format(counterparty=counterparty_label),
            messages=[{"role": "user", "content": utterance}],
        )
        text = "".join(getattr(b, "text", "") for b in resp.content)
    except Exception as e:
        print(f"[extractor] model error: {type(e).__name__}")
        return []

    payload = _parse(text)
    raw_claims = payload.get("claims") or []
    if not isinstance(raw_claims, list):
        return []

    now = datetime.datetime.now().isoformat(timespec="seconds")
    out: list[Claim] = []
    for rc in raw_claims:
        if not isinstance(rc, dict):
            continue
        attr = (rc.get("attribute") or "").strip()
        value = rc.get("value")
        if not attr or value is None or value == "":
            continue
        conf = rc.get("confidence")
        try:
            conf_f = float(conf) if conf is not None else 0.8
        except Exception:
            conf_f = 0.8
        out.append(Claim(
            entity_id=entity_id,
            attribute=attr,
            value=value,
            source=f"live:{conversation_id}" if conversation_id else "live",
            timestamp=now,
            conversation_id=conversation_id,
            confidence=max(0.0, min(1.0, conf_f)),
        ))
    return out


def _run(utterance: str) -> None:
    """Worker: read the active briefing, call extractor, write to L1 buffer."""
    briefing = get_active_briefing()
    if briefing is None:
        return
    if briefing.entity_id == UNRESOLVED_ENTITY:
        # No identity → nothing to attach claims to. The L3 contract is that
        # unresolved entities never trigger any claim work.
        return
    label = briefing.counterparty_hint or briefing.entity_id

    claims = _extract(utterance, briefing.entity_id, label, briefing.conversation_id)
    if not claims:
        return

    store = get_default_store()
    for c in claims:
        try:
            # persist=False routes to the L1 buffer so reads on the hot path
            # don't see mid-conversation claims. consolidate() at end_conversation
            # flushes the buffer into the live store and the Supermemory backend.
            store.add_claim(c, persist=False)
        except Exception as e:
            print(f"[extractor] store write error: {type(e).__name__}")
            continue
    print(f"[extractor] extracted {len(claims)} claim(s)")


def fire(utterance: str) -> None:
    """Public entry — kick the extractor in a daemon thread. Never blocks."""
    if not (utterance or "").strip():
        return
    threading.Thread(target=_run, args=(utterance,), daemon=True).start()
