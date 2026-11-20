"""Compare historical retrieval with and without SIE using the engineering demo."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from dotenv import load_dotenv
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from memory import retrieval
from memory.superlinked import score_passages
from memory.transcripts import TranscriptStore


CASES = [
    ("decision", "Which retrieval approach did the document assistant team choose?",
     "retrieval-decision.txt", "The team chose one retrieval pass"),
    ("observation", "How many retrieval failures involved missing section headings?",
     "retrieval-decision.txt", "In six failed retrieval examples"),
    ("proposal", "What retrieval change was proposed without benchmark results?",
     "retrieval-proposal.txt", "The proposed retrieval change has no benchmark results"),
]


def demo_cases():
    saved = ROOT / ".local-data" / "private-sponsor-demo.json"
    if saved.is_file():
        return json.loads(saved.read_text(encoding="utf-8"))["superlinked"]["cases"], ROOT / "demo-data"
    return CASES, ROOT / "tests" / "fixtures"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true", help="Score two short synthetic passages only.")
    parser.add_argument("--with-moss", action="store_true", help="Read from the current Moss index.")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")

    if not (os.getenv("SIE_API_KEY") or os.getenv("SUPERLINKED_API_KEY")):
        print("Superlinked blocked: set SUPERLINKED_API_KEY or SIE_API_KEY in the ignored .env.", file=sys.stderr)
        return 2
    if args.probe:
        try:
            scores = score_passages("How many retrieval passes were approved?", [
                {"excerpt": "The team approved one retrieval pass."},
                {"excerpt": "The schedule lists several breaks."},
            ])
        except httpx.HTTPStatusError as error:
            print(f"Superlinked probe failed: HTTP {error.response.status_code}.", file=sys.stderr)
            return 2
        except (httpx.HTTPError, KeyError, TypeError, ValueError, OverflowError) as error:
            print(f"Superlinked probe failed: {type(error).__name__}.", file=sys.stderr)
            return 2
        print(json.dumps({"provider": "Superlinked", "status": "scored", "scores": scores}))
        return 0

    if args.with_moss:
        asyncio.run(retrieval.init_moss())
    examples, directory = demo_cases()
    with TemporaryDirectory(prefix="cta-ranking-") as tmp:
        store = TranscriptStore(Path(tmp) / "evidence.sqlite3")
        for name in dict.fromkeys(case[2] for case in examples):
            path = directory / name
            store.import_text(name, path.read_text(encoding="utf-8"))
        cases = []
        for label, question, name, phrase in examples:
            original = retrieval.historical_sources(store, question, limit=4)
            incoming = {}
            os.environ["CONTEXT_AGENT_SUPERLINKED_ENABLED"] = "1"
            selected = retrieval.historical_sources(store, question, limit=4, ranking=incoming)
            case = {"case": label, "baseline_top1": bool(original and phrase in original[0]["excerpt"]
                                                         and original[0]["filename"] == name),
                    "status": incoming["status"]}
            if incoming["status"] == "scored":
                annotated = incoming["passages"]
                relevant = [
                    item for item in annotated if item["filename"] == name
                    and phrase in store.get(item["source_id"])["text"][item["offset"]:item["offset"] + item["length"]]
                ]
                case.update(
                    before=min((item["before"] for item in relevant), default=None),
                    after=min((item["after"] for item in relevant), default=None),
                    selected=[{"source_id": item["source_id"], "offset": item["offset"]} for item in selected],
                    passages=annotated,
                )
            elif "reason" in incoming:
                case["reason"] = incoming["reason"]
            cases.append(case)

    report = {"provider": "Superlinked", "moss_available": retrieval.moss is not None, "cases": cases}
    report["scored_cases"] = sum(case["status"] == "scored" for case in cases)
    report["rank_changed_cases"] = sum(
        any(item["before"] != item["after"] for item in case.get("passages", [])) for case in cases
    )
    print(json.dumps(report, indent=2))
    return 0 if report["scored_cases"] == len(cases) else 2


if __name__ == "__main__":
    raise SystemExit(main())
