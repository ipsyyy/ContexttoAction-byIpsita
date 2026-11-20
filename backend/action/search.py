"""Web search — two backends.

search()      → Tavily Search API, good for factual lookups
search_deep() → Browser Use cloud,  ~5-15s,  good for structured data,
                                              company pages, Crunchbase, LinkedIn
"""

import os
import time
import requests

_TAVILY_ENDPOINT = "https://api.tavily.com/search"


class TavilySearchError(RuntimeError):
    """A sanitized provider failure suitable for display."""


def search_sources(query: str, count: int = 5, *, strict: bool = False) -> list[dict]:
    """Return usable public Tavily results with URLs."""
    key = os.getenv("TAVILY_API_KEY")
    if not key or not query.strip():
        if strict:
            raise TavilySearchError("Tavily is not configured or the public search query is empty.")
        return []
    try:
        response = requests.post(
            _TAVILY_ENDPOINT, json={"query": query, "max_results": max(1, min(count, 10))},
            headers={"Authorization": f"Bearer {key}"}, timeout=8,
        )
        response.raise_for_status()
        return [
            {"title": item.get("title", ""), "excerpt": item.get("content", ""), "url": item["url"]}
            for item in response.json().get("results", [])[:count]
            if isinstance(item, dict) and isinstance(item.get("url"), str)
            and item["url"].startswith(("https://", "http://"))
        ]
    except requests.HTTPError as error:
        status = error.response.status_code
        print(f"[search] Tavily HTTP {status}")
        if strict:
            raise TavilySearchError(f"Tavily could not complete the search (HTTP {status}).") from None
    except Exception as error:
        print(f"[search] Tavily failed: {type(error).__name__}")
        if strict:
            raise TavilySearchError("Tavily did not return a usable response. Try again.") from None
    return []


def search(query: str, count: int = 5) -> str | None:
    sources = search_sources(query, count)
    return "\n".join(f"- {item['title']} ({item['url']}): {item['excerpt']}" for item in sources) or None


# ── Browser Use cloud ─────────────────────────────────────────────────────────

_BU_BASE     = "https://api.browser-use.com/api/v3"
_BU_TIMEOUT  = 60    # seconds to wait for session
_BU_POLL     = 3


def search_deep(task: str) -> str | None:
    """Browser Use cloud — navigates the web and returns structured output.

    `task` should be a plain-English instruction, e.g.:
    "Find Drifter Labs on Crunchbase and return their latest funding round and total raised."
    """
    key = os.getenv("BROWSER_USE_API_KEY")
    if not key:
        print("[search_deep] BROWSER_USE_API_KEY not set — skipping")
        return None

    headers = {
        "X-Browser-Use-API-Key": key,
        "Content-Type": "application/json",
    }
    try:
        r = requests.post(
            f"{_BU_BASE}/sessions",
            json={"task": task, "maxSteps": 15},
            headers=headers,
            timeout=15,
        )
        r.raise_for_status()
        session_id = r.json()["id"]
        print(f"[search_deep] session {session_id} — {task[:60]!r}")
    except Exception as e:
        print(f"[search_deep] create error: {e}")
        return None

    deadline = time.time() + _BU_TIMEOUT
    while time.time() < deadline:
        time.sleep(_BU_POLL)
        try:
            r = requests.get(f"{_BU_BASE}/sessions/{session_id}", headers=headers, timeout=10)
            r.raise_for_status()
            data = r.json()
            status = data.get("status")
            if status == "finished":
                output = data.get("output") or ""
                print(f"[search_deep] done: {output[:120]!r}")
                return output
            elif status in ("stopped", "failed"):
                print(f"[search_deep] session {status}")
                return None
        except Exception as e:
            print(f"[search_deep] poll error: {e}")

    print(f"[search_deep] timed out after {_BU_TIMEOUT}s")
    return None
