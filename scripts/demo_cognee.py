"""Build and read a source-linked Cognee graph for the engineering demo."""

import argparse
from hashlib import sha256
from html import escape
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

from dotenv import load_dotenv
import httpx

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".local-data" / "sponsor-evidence"
DEFAULT_PROFILE = {
    "question": "Why did the team choose one retrieval pass, and how does the failed retrieval observation relate to the later proposal?",
    "heading": "Synthetic document assistant retrieval review",
    "evidence": [
        ("Decision", "retrieval-decision.txt", "The team chose one retrieval pass"),
        ("Rationale", "retrieval-decision.txt", "The reason for one retrieval pass"),
        ("Observation", "retrieval-decision.txt", "In six failed retrieval examples"),
        ("Later recommendation", "retrieval-proposal.txt", "I propose three sequential retrieval passes"),
    ],
}


def demo_profile():
    saved = ROOT / ".local-data" / "private-sponsor-demo.json"
    if saved.is_file():
        return json.loads(saved.read_text(encoding="utf-8"))["cognee"], ROOT / "demo-data"
    return DEFAULT_PROFILE, ROOT / "tests" / "fixtures"


def demo_evidence(profile, directory):
    sources = {name: (directory / name).read_text(encoding="utf-8")
               for _, name, _ in profile["evidence"]}
    records = []
    for label, name, anchor in profile["evidence"]:
        text = sources[name]
        at = text.index(anchor)
        start = text.rfind("\n", 0, at) + 1
        end = text.find("\n", at)
        source_id = sha256(text.encode()).hexdigest()
        records.append({
            "label": label, "filename": name, "source_id": source_id, "offset": start,
            "reference": f"{source_id}:{start}", "excerpt": text[start:end if end >= 0 else len(text)],
        })
    return records


def matching_evidence(response, evidence):
    payload = json.dumps(response, ensure_ascii=False).casefold()
    return [
        item for item in evidence
        if item["reference"].casefold() in payload
        or item["excerpt"].partition(": ")[2].casefold() in payload
    ]


def render_report(result, evidence, question):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "cognee.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    cards = "".join(
        f"<article><h2>{escape(item['label'])}</h2><blockquote>{escape(item['excerpt'])}</blockquote>"
        f"<p>{escape(item['filename'])} · character {item['offset']}</p>"
        f"<code>{escape(item['source_id'])}</code></article>"
        for item in evidence
    )
    (OUTPUT / "cognee.html").write_text(
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
        "<title>Engineering decision evidence</title>"
        "<style>body{font:17px system-ui;max-width:820px;margin:40px auto;padding:0 24px;"
        "color:#172a28;background:#f8faf8}article,details{background:white;padding:12px 20px;"
        "margin:12px 0;border:1px solid #c9d5cf}code{overflow-wrap:anywhere}pre{white-space:pre-wrap}</style>"
        f"<h1>Why we chose one retrieval pass</h1><p>{escape(question)}</p>"
        f"<p>Cognee Cloud · {escape(result['search_type'])} · {len(evidence)} matched original excerpts</p>"
        f"{cards}<details><summary>Inspect the Cognee response</summary>"
        f"<pre>{escape(json.dumps(result['response'], indent=2, ensure_ascii=False))}</pre></details>",
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("probe", "seed", "read"))
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    key = os.getenv("COGNEE_API_KEY")
    base = os.getenv("COGNEE_BASE_URL") or os.getenv("COGNEE_API_URL") or os.getenv("COGNEE_URL")
    if not key or not base:
        print("Cognee blocked: set COGNEE_API_KEY and COGNEE_BASE_URL in the ignored .env.", file=sys.stderr)
        return 2
    url = urlsplit(base)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
        print("Cognee requires a clean HTTPS tenant URL.", file=sys.stderr)
        return 2
    api = base.rstrip("/").removesuffix("/api/v1") + "/api/v1"
    try:
        with httpx.Client(base_url=api, headers={"X-Api-Key": key}, timeout=httpx.Timeout(20.0, connect=4.0)) as client:
            if args.operation == "probe":
                client.get("datasets/").raise_for_status()
                print("Cognee authenticated dataset read: HTTP 200.")
                return 0

            profile, directory = demo_profile()
            evidence = demo_evidence(profile, directory)
            dataset = "cta-ipsita-engineering-" + sha256(json.dumps(evidence).encode()).hexdigest()[:12]
            if args.operation == "seed":
                files = [
                    ("data", (f"evidence-{i}.txt", f"{profile['heading']}. {item['label']}.\n"
                              f"{item['excerpt']}\nSource reference: {item['reference']}", "text/plain"))
                    for i, item in enumerate(evidence, 1)
                ]
                client.post("add", data={"datasetName": dataset}, files=files).raise_for_status()
                response = client.post("cognify", json={"datasets": [dataset], "run_in_background": True})
                response.raise_for_status()
                print(f"Cognee accepted graph processing for {dataset}. Check with the read command.")
                return 0

            for search_type in ("GRAPH_COMPLETION", "CHUNKS"):
                response = client.post("search", json={
                    "query": profile["question"], "datasets": [dataset], "search_type": search_type,
                    "only_context": True, "verbose": True, "top_k": 8,
                })
                response.raise_for_status()
                payload = response.json()
                matched = matching_evidence(payload, evidence)
                if {"Decision", "Rationale"} <= {item["label"] for item in matched}:
                    result = {"dataset": dataset, "search_type": search_type, "response": payload,
                              "cited": [{k: v for k, v in item.items() if k != "excerpt"} for item in matched]}
                    render_report(result, matched, profile["question"])
                    print(f"Cognee {search_type} matched {len(matched)} original excerpts. Open {OUTPUT / 'cognee.html'}.")
                    return 0
            print("Cognee returned results without both required original references.", file=sys.stderr)
    except httpx.HTTPStatusError as error:
        print(f"Cognee {args.operation} failed: HTTP {error.response.status_code}.", file=sys.stderr)
    except (httpx.HTTPError, OSError, ValueError) as error:
        print(f"Cognee {args.operation} failed: {type(error).__name__}.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
