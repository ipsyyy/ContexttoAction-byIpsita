"""Optional hosted scoring for saved transcript evidence."""

import math
import os
from urllib.parse import quote

import httpx


def score_passages(question: str, passages: list[dict]) -> list[dict]:
    key = os.getenv("SIE_API_KEY") or os.getenv("SUPERLINKED_API_KEY")
    if not key:
        raise ValueError("missing_key")
    model = os.getenv("SIE_RERANK_MODEL", "Qwen/Qwen3-Reranker-0.6B")
    endpoint = os.getenv("SIE_BASE_URL", "https://api.superlinked.com").rstrip("/")
    if endpoint not in {"https://api.superlinked.com", "https://eu.api.superlinked.com"}:
        raise ValueError("invalid_endpoint")

    items = [{"id": str(index), "text": passage["excerpt"]} for index, passage in enumerate(passages)]
    response = httpx.post(
        f"{endpoint}/v1/score/{quote(model, safe='')}",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        json={"query": {"text": question[:2000]}, "items": items},
        timeout=httpx.Timeout(6.0, connect=2.0),
    )
    response.raise_for_status()
    scores = response.json()["scores"]
    if not isinstance(scores, list) or len(scores) != len(items):
        raise ValueError("incomplete_scores")
    if any(
        not isinstance(entry, dict)
        or not isinstance(entry.get("item_id"), str)
        or type(entry.get("rank")) is not int
        or type(entry.get("score")) not in (int, float)
        or not math.isfinite(entry["score"])
        for entry in scores
    ):
        raise ValueError("invalid_scores")
    if {entry["item_id"] for entry in scores} != {item["id"] for item in items}:
        raise ValueError("invalid_item_ids")
    if {entry["rank"] for entry in scores} != set(range(len(items))):
        raise ValueError("invalid_ranks")
    return sorted(scores, key=lambda entry: entry["rank"])
