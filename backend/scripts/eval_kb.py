"""Evaluate chat routing plus evidence-filtered retrieval against Pinecone.

No LLM calls. This checks retrieval/routing, not final-answer factuality.
Negative cases must retrieve no evidence; provider outages always fail.
Use --direct to diagnose retrieval independently of routing.

Run from the backend folder:
    python -m scripts.eval_kb
    python -m scripts.eval_kb --cases tests/eval_cases.json --top-k 4
    python -m scripts.eval_kb --cases tests/eval_rag_cases.json
"""

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List

from services.kb_rag import KnowledgeBaseUnavailable, search_knowledge_base
from services.context_router import decide_context_strategy

DEFAULT_CASES_PATH = os.path.join("tests", "eval_cases.json")
DEFAULT_TOP_K = 4

# Fraction of a case's expected_keywords that must appear in the retrieved
# text for that case to pass on the keyword check. Not 100%: a good chunk
# can legitimately contain most but not literally every expected term.
KEYWORD_MATCH_THRESHOLD = 0.5


def load_cases(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _combined_text(hits: List[Dict[str, Any]]) -> str:
    """All retrieved chunks' text, concatenated and lowercased — this is
    the same material format_kb_context() would hand to the Groq prompt.
    """
    return " ".join((h.get("fields", {}).get("text") or "") for h in hits).lower()


def evaluate_case(case: Dict[str, Any], top_k: int, through_router: bool = True) -> Dict[str, Any]:
    """Run one test case's query through real retrieval and score it."""
    query = case["query"]
    expected_keywords = [k.lower() for k in case.get("expected_keywords", [])]
    expected_source = case.get("expected_source")

    start = time.perf_counter()
    routed = decide_context_strategy(query, 0).use_knowledge_base
    unavailable = False
    try:
        hits = search_knowledge_base(query, top_k=top_k) if routed or not through_router else []
    except KnowledgeBaseUnavailable:
        hits = []
        unavailable = True
    latency_ms = (time.perf_counter() - start) * 1000

    combined = _combined_text(hits)
    matched_keywords = [k for k in expected_keywords if k in combined]
    missing_keywords = [k for k in expected_keywords if k not in matched_keywords]
    keyword_ratio = (len(matched_keywords) / len(expected_keywords)) if expected_keywords else 1.0

    retrieved_sources = [h.get("fields", {}).get("document_name") for h in hits]
    source_matched = expected_source is None or expected_source in retrieved_sources

    expects_no_evidence = case.get("expect_no_evidence", False)
    passed = (not hits if expects_no_evidence else bool(hits) and keyword_ratio >= KEYWORD_MATCH_THRESHOLD and source_matched)
    passed = passed and not unavailable
    if through_router:
        passed = passed and routed == case.get("expect_kb", True)

    return {
        "query": query,
        "passed": passed,
        "routed_to_kb": routed,
        "unavailable": unavailable,
        "matched_keywords": matched_keywords,
        "missing_keywords": missing_keywords,
        "expected_source": expected_source,
        "retrieved_sources": retrieved_sources,
        "source_matched": source_matched,
        "top_score": hits[0]["score"] if hits else None,
        "hit_count": len(hits),
        "latency_ms": round(latency_ms, 1),
    }


def run(cases_path: str, top_k: int, through_router: bool = True) -> List[Dict[str, Any]]:
    cases = load_cases(cases_path)
    return [evaluate_case(case, top_k, through_router=through_router) for case in cases]


def print_report(results: List[Dict[str, Any]]) -> None:
    total = len(results)
    passed = sum(1 for r in results if r["passed"])

    print(f"\n{'=' * 72}")
    print(f"KB RAG Evaluation - {passed}/{total} passed")
    print(f"{'=' * 72}\n")

    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        print(f"[{status}] {r['query']}")
        print(f"       matched keywords : {r['matched_keywords'] or '(none)'}")
        print(f"       routed to KB: {r['routed_to_kb']} | retrieval unavailable: {r['unavailable']}")
        if r["missing_keywords"]:
            print(f"       missing keywords : {r['missing_keywords']}")
        print(f"       expected source  : {r['expected_source']}")
        print(f"       retrieved source(s): {r['retrieved_sources']}")
        print(f"       top score: {r['top_score']}  |  hits: {r['hit_count']}  |  latency: {r['latency_ms']} ms")
        print()

    if total:
        avg_latency = sum(r["latency_ms"] for r in results) / total
        print(f"{'-' * 72}")
        print(f"Pass rate: {passed}/{total} ({100 * passed / total:.0f}%)")
        print(f"Average retrieval latency: {avg_latency:.1f} ms")
    else:
        print("No cases loaded.")
    print(f"{'=' * 72}\n")


def _parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate the Pinecone knowledge-base RAG retrieval layer against a fixed set of factual queries.",
    )
    parser.add_argument(
        "--cases",
        default=DEFAULT_CASES_PATH,
        help=f"Path to the eval cases JSON file (default: {DEFAULT_CASES_PATH})",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=DEFAULT_TOP_K,
        help=f"top_k passed to search_knowledge_base for each query (default: {DEFAULT_TOP_K})",
    )
    parser.add_argument("--direct", action="store_true", help="Bypass routing to diagnose retrieval alone.")
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])

    if not os.path.exists(args.cases):
        print(f"Eval cases file not found: {args.cases}")
        sys.exit(1)

    results = run(args.cases, args.top_k, through_router=not args.direct)
    print_report(results)

    failed = sum(1 for r in results if not r["passed"])
    sys.exit(1 if failed else 0)
