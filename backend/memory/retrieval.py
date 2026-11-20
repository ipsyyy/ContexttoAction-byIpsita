"""Moss-backed semantic retrieval over the scripted past-call store.

Switched to the official ``moss`` package on PyPI (`pip install moss`). The
client methods (`create_index`, `load_index`, `query`) are async at runtime
— their `inspect.signature` shows sync types but they return coroutines and
emit RuntimeWarnings if you don't await. The function shape here is
unchanged from the `inferedge_moss` era.

Degradation is unchanged: if the SDK or project keys are missing,
`retrieve()` returns the full static doc set so the decision agent still
sees the contradiction.

Module-level `moss` is intentionally patch-able: tests stub it with a
MagicMock whose `query` returns a SearchResult-shaped object directly.
The production code wraps the SDK call in `asyncio.run` via
`_query_async`, which transparently passes through a MagicMock return
value because awaiting a non-coroutine raises and we catch it.
"""

import asyncio
import os
import threading
import time

try:
    from moss import MossClient, DocumentInfo, MutationOptions, QueryOptions  # type: ignore
    _moss_available = True
except Exception as e:  # pragma: no cover
    print(f"[moss] SDK unavailable ({type(e).__name__}) — falling back to static retrieval")
    _moss_available = False
    MossClient = None  # type: ignore
    DocumentInfo = None  # type: ignore
    QueryOptions = None  # type: ignore


INDEX_NAME = "context-to-action-memory"

# The single source of truth for past-call facts injected into the decision agent.
# Kept as plain (id, text) so the static fallback works without the SDK's types.
_RAW_DOCS = [
    ("c1",  "April 15 call with Marcus Chen: stated runway 18 months."),
    ("c2",  "April 15 call with Marcus Chen: committed to sending cap table by end of week."),
    ("c3",  "April 15 call with Marcus Chen: wants to wait until ARR hits $2M before Series A."),
    ("c4",  "March 2 call with Marcus Chen: ARR at $1.2M, growing 15% MoM."),
    ("c5",  "March 2 call with Marcus Chen: team size 12, hiring 3 engineers."),
    ("c6",  "March 2 call with Marcus Chen: will not close a round before Q3."),
    ("c7",  "April 3 call with Sarah Kim (Drifter Labs): DAUs at 11,000, growth accelerating."),
    ("c8",  "April 3 call with Sarah Kim (Drifter Labs): runway 24 months, burn $165k/month."),
    ("c9",  "April 3 call with Sarah Kim (Drifter Labs): iOS app launching end of May."),
    ("c10", "April 3 call with Sarah Kim (Drifter Labs): Shopify is largest enterprise customer, $180k ACV."),
    ("c11", "March 18 call with Sarah Kim (Drifter Labs): DAUs at 11,200."),
    ("c12", "April 22 call with James Okafor (Meridian Health): 3 hospital pilots live."),
    ("c13", "April 22 call with James Okafor (Meridian Health): sales cycle averaging 9 months."),
    ("c14", "April 22 call with James Okafor (Meridian Health): ARR $800k, burn $90k/month, 20 months runway."),
    ("c15", "February 10 call with James Okafor (Meridian Health): 5 hospital pilots live, sales cycle 6 months."),
]

# Module-level client handle. Initialized lazily by init_moss(); tests patch it directly.
moss: object | None = None
_init_lock = threading.Lock()

# Most-recently-retrieved facts, written by prefetch() in the background and
# read by get_latest_facts() on the hot path. Lives here (not in decision/)
# because retrieval is the only writer.
_latest_facts: list[str] = []
_latest_lock = threading.Lock()

# Live retrieval telemetry — what the Dashboard's "Live retrieval" panel
# reads. Updated on every prefetch query. Read-only from outside.
_last_query_meta: dict = {
    "query": "",
    "results": [],
    "ms": 0,
    "source": "",           # 'moss' | 'static'
    "at": "",
}
_last_query_lock = threading.Lock()


def get_last_query_meta() -> dict:
    with _last_query_lock:
        return dict(_last_query_meta)


def _have_keys() -> bool:
    return bool(os.getenv("MOSS_PROJECT_ID") and os.getenv("MOSS_PROJECT_KEY"))


async def init_moss() -> None:
    """Create + load the past-calls index. Safe to call multiple times.

    No-ops cleanly if the SDK or project keys are missing.
    """
    global moss
    if not _moss_available:
        return
    if not _have_keys():
        print("[moss] MOSS_PROJECT_ID / MOSS_PROJECT_KEY not set — skipping init")
        return

    try:
        candidate = MossClient(os.environ["MOSS_PROJECT_ID"], os.environ["MOSS_PROJECT_KEY"])
        await candidate.load_index(INDEX_NAME)
    except Exception as error:
        print(f"[moss] index unavailable: {type(error).__name__}")
    else:
        moss = candidate


async def _query_async(utterance: str, top_k: int = 2) -> list[str]:
    """Wraps the awaitable ``moss.query`` call. When ``moss`` is a test
    MagicMock, the call returns the mock's `return_value` (not a
    coroutine); we handle both shapes so prod awaits and tests don't."""
    if moss is None:
        return []
    try:
        maybe_coro = moss.query(
            INDEX_NAME, utterance, QueryOptions(top_k=top_k, alpha=0.6),
        )
        if asyncio.iscoroutine(maybe_coro):
            results = await maybe_coro
        else:
            results = maybe_coro
        return [d.text for d in results.docs]
    except Exception as e:
        print(f"[moss] query error: {type(e).__name__}")
        return []


def retrieve(utterance: str, *, top_k: int = 2) -> list[str]:
    """Past-call facts for the utterance. Live retrieval defaults to two."""
    if moss is None:
        return _static_fallback()
    try:
        return asyncio.run(_query_async(utterance, top_k))
    except RuntimeError:
        # Already inside an event loop — punt to a fresh thread with its own loop.
        return _retrieve_in_thread(utterance, top_k)
    except Exception as e:
        print(f"[moss] retrieve error: {type(e).__name__}")
        return _static_fallback()


def _retrieve_in_thread(utterance: str, top_k: int = 2) -> list[str]:
    result: list[list[str]] = [[]]

    def runner() -> None:
        try:
            result[0] = asyncio.run(_query_async(utterance, top_k))
        except Exception as e:
            print(f"[moss] thread retrieve error: {type(e).__name__}")
            result[0] = _static_fallback()

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    t.join(timeout=2.0)
    return result[0] or _static_fallback()


def _static_fallback() -> list[str]:
    """Inject the full doc set when Moss is unavailable. Small enough to fit."""
    return [] if _have_keys() or os.getenv("CONTEXT_AGENT_DESKTOP") == "1" else [t for (_id, t) in _RAW_DOCS]


def get_latest_facts() -> list[str]:
    """Hot-path read of the most-recently-prefetched facts.

    When a conversation snapshot is active (L3: memory.conversation), its
    frozen claims override this path entirely — the decision reads ONLY
    the snapshot, not Moss / static fallback. This is the read-live/
    write-after model: a mid-call retrieval change can't poison the
    contradiction-catch.

    Otherwise, on cold start (nothing prefetched yet), returns the static
    doc set — same as retrieve()'s failure path. Never blocks, never
    calls Moss.
    """
    # L3 snapshot override. Late-import to avoid a cycle at module load
    # time and to keep this module functional when conversation.py is
    # absent (tests, alternate entry points).
    try:
        from memory.conversation import snapshot_facts  # noqa: WPS433
        snap = snapshot_facts()
        if snap is not None:
            if moss is None:
                return snap
            with _latest_lock:
                return list(dict.fromkeys([*snap, *_latest_facts]))
    except Exception:
        pass

    with _latest_lock:
        if _latest_facts:
            return list(_latest_facts)
    return _static_fallback()


def prefetch(utterance: str) -> None:
    """Kick a background retrieval and update _latest_facts when it completes.

    Intentionally fire-and-forget. The decision agent reads whatever has
    been written to _latest_facts at call time; it never blocks waiting for
    this prefetch to finish. If the prefetch loses to the decision call
    (i.e., the decision uses last utterance's facts), that's the documented
    tradeoff — preferable to taking Moss latency on the hot path.
    """
    if not (utterance or "").strip():
        return
    threading.Thread(target=_prefetch_run, args=(utterance,), daemon=True).start()


def _prefetch_run(utterance: str) -> None:
    global _latest_facts
    t0 = time.perf_counter()
    source = "moss" if moss is not None else "moss-unavailable" if _have_keys() else "sample"
    try:
        facts = retrieve(utterance)
    except Exception as e:
        print(f"[moss] prefetch error: {type(e).__name__}")
        return
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    with _latest_lock:
        _latest_facts = list(facts)
    # Surface telemetry whether or not the call returned facts — the Dashboard
    # cares about the latency and the resolved source either way.
    import datetime as _dt
    with _last_query_lock:
        _last_query_meta.update({
            "query": utterance[:120],
            "results": [f[:160] for f in (facts or [])][:5],
            "ms": elapsed_ms,
            "source": source if facts or moss is None else "moss-no-results",
            "at": _dt.datetime.now().isoformat(timespec="seconds"),
        })


def add_session_docs(docs: list[tuple[str, str]]) -> None:
    """Push (id, text) docs into the live Moss index so the next query covers
    them. No-op when Moss is unavailable. Called at start_conversation after
    hydrating prior claims from Supermemory."""
    if not docs or not _moss_available or (moss is None and not _have_keys()):
        return

    async def add_documents():
        global moss
        client = moss or MossClient(os.environ["MOSS_PROJECT_ID"], os.environ["MOSS_PROJECT_KEY"])
        documents = [DocumentInfo(id=identifier, text=text) for identifier, text in docs]
        if moss is None:
            try:
                await client.load_index(INDEX_NAME)
            except Exception:
                await client.create_index(INDEX_NAME, documents, "moss-minilm")
            else:
                await client.add_docs(INDEX_NAME, documents, MutationOptions(upsert=True))
        else:
            await client.add_docs(INDEX_NAME, documents, MutationOptions(upsert=True))
        await client.load_index(INDEX_NAME)
        moss = client

    def runner():
        with _init_lock:
            try:
                asyncio.run(add_documents())
            except Exception as error:
                print(f"[moss] indexing failed: {type(error).__name__}")

    threading.Thread(target=runner, daemon=True).start()


def reset_latest_facts() -> None:
    """For tests / session boundaries."""
    global _latest_facts
    with _latest_lock:
        _latest_facts = []


def historical_sources(
    store, query: str, limit: int, exclude_source: str = "", *, ranking: dict | None = None,
) -> list[dict]:
    """Resolve semantic hits back to original local evidence, then fill from SQLite."""
    enabled = ranking is not None and os.getenv("CONTEXT_AGENT_SUPERLINKED_ENABLED") == "1"
    if enabled and not (os.getenv("SIE_API_KEY") or os.getenv("SUPERLINKED_API_KEY")):
        ranking.update(status="fallback", reason="missing_key")
        enabled = False
    if not enabled:
        return _baseline_historical_sources(store, query, limit, exclude_source)

    pool_size = max(8, limit)
    facts = retrieve(query[:1800], top_k=pool_size) if moss is not None else []
    lexical = store.search(query, limit=pool_size, exclude_source=exclude_source)
    semantic = [_resolve_moss_source(store, fact, exclude_source) for fact in facts]
    candidates = _unique_sources(
        [*semantic[:2], *lexical[:4], *semantic[2:], *lexical[4:]], pool_size,
    )
    if len(candidates) < 2:
        ranking.update(status="insufficient_candidates")
        return _baseline_historical_sources(store, query, limit, exclude_source)

    import httpx
    from memory.superlinked import score_passages

    try:
        scores = score_passages(query, candidates)
    except (httpx.HTTPError, KeyError, TypeError, ValueError, OverflowError) as error:
        reason = f"HTTP {error.response.status_code}" if isinstance(error, httpx.HTTPStatusError) else type(error).__name__
        ranking.update(status="fallback", reason=reason)
        return _baseline_historical_sources(store, query, limit, exclude_source)

    selected = []
    movements = []
    for entry in scores:
        index = int(entry["item_id"])
        passage = candidates[index]
        selected.append(passage)
        movements.append({
            "source_id": passage["source_id"], "filename": passage["filename"],
            "offset": passage["offset"], "length": len(passage["excerpt"]),
            "before": index + 1, "after": entry["rank"] + 1, "score": entry["score"],
        })
    ranking.update(status="scored", passages=movements)
    return selected[:limit]


def _baseline_historical_sources(store, query, limit, exclude_source):
    results = []
    seen = {exclude_source}
    if moss is not None:
        for fact in retrieve(query[:1800]):
            hit = _resolve_moss_source(store, fact, exclude_source)
            if hit and hit["source_id"] not in seen:
                results.append(hit)
                seen.add(hit["source_id"])
    for hit in store.search(query, limit=limit, exclude_source=exclude_source):
        if hit["source_id"] not in seen:
            results.append(hit)
            seen.add(hit["source_id"])
    return results[:limit]


def _resolve_moss_source(store, fact, exclude_source):
    import re

    match = re.match(r"^\[local:([a-f0-9]{64}):(\d+)\]", fact)
    if not match or match.group(1) == exclude_source:
        return None
    source_id, offset = match.group(1), int(match.group(2))
    source = store.get(source_id)
    if not source or offset >= len(source["text"]):
        return None
    return {"source_id": source_id, "filename": source["filename"],
            "offset": offset, "excerpt": source["text"][offset:offset + 900]}


def _unique_sources(hits, limit):
    seen = set()
    results = []
    for hit in hits:
        if hit is None:
            continue
        key = (hit["source_id"], hit["offset"])
        if key not in seen:
            results.append(hit)
            seen.add(key)
    return results[:limit]
