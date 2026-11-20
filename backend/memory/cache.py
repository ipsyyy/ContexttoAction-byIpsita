import datetime
import os

from memory.past_calls import PAST_CALLS
from user_profile import user_preamble


def _today_iso() -> str:
    return datetime.date.today().isoformat()


def build_messages(current_belief: str, utterance: str, retrieved_facts: list[str]) -> list:
    # Always include PAST_CALLS as the cached memory block; append any
    # live-retrieved facts if Moss returned any.
    extra = "\n" + "\n".join(retrieved_facts) if retrieved_facts else ""
    personal = os.getenv("CONTEXT_AGENT_DESKTOP") == "1"
    facts_text = ("\n".join(retrieved_facts) or "No historical evidence retrieved.") if personal else PAST_CALLS.strip() + extra
    # Today's date is included in the uncached prompt tail. It gives the
    # decision agent a fixed "now" against which to interpret relative
    # references ("by end of week", "last quarter"), and helps it judge
    # whether a remembered fact is recent or stale.
    today = _today_iso()
    return [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": facts_text,
                    "cache_control": {"type": "ephemeral"},
                },
                {
                    "type": "text",
                    "text": (
                        f"{user_preamble()}"
                        f"Today: {today}\n\n"
                        f"Current belief:\n{current_belief}\n\n"
                        f"New utterance: {utterance}\n\n"
                        f"Act or stay silent?"
                    ),
                },
            ],
        }
    ]
