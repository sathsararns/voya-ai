"""Evaluation harness for the Pinecone knowledge-base RAG retrieval layer.

Runs a fixed set of factual travel queries straight through
services.kb_rag.search_knowledge_base — the exact same retrieval call
groq_service.py makes before every Groq request — and checks whether the
retrieved chunks actually contain the expected keywords and come from the
expected source document.

This evaluates RETRIEVAL quality specifically: deterministic, no LLM call,
no Groq API cost, no dependence on the model's phrasing that day. It is
deliberately NOT an end-to-end "does the chatbot's final answer look right"
eval — that would mean scoring non-deterministic LLM output, which cuts
against the "deterministic and simple" goal here. If the retrieval layer
reliably surfaces the right grounding chunks, the Groq prompt (see
groq_service.py) already instructs the model to ground its answer in them.

Run from the backend folder:
    python -m scripts.eval_kb
    python -m scripts.eval_kb --cases tests/eval_cases.json --top-k 4
"""

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List

from services.kb_rag import search_knowledge_base

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


def evaluate_case(case: Dict[str, Any], top_k: int) -> Dict[str, Any]:
    """Run one test case's query through real retrieval and score it."""
    query = case["query"]
    expected_keywords = [k.lower() for k in case.get("expected_keywords", [])]
    expected_source = case.get("expected_source")

    start = time.perf_counter()
    hits = search_knowledge_base(query, top_k=top_k)
    latency_ms = (time.perf_counter() - start) * 1000

    combined = _combined_text(hits)
    matched_keywords = [k for k in expected_keywords if k in combined]
    missing_keywords = [k for k in expected_keywords if k not in matched_keywords]
    keyword_ratio = (len(matched_keywords) / len(expected_keywords)) if expected_keywords else 1.0

    retrieved_sources = [h.get("fields", {}).get("document_name") for h in hits]
    source_matched = expected_source is None or expected_source in retrieved_sources

    passed = bool(hits) and keyword_ratio >= KEYWORD_MATCH_THRESHOLD and source_matched

    return {
        "query": query,
        "passed": passed,
        "matched_keywords": matched_keywords,
        "missing_keywords": missing_keywords,
        "expected_source": expected_source,
        "retrieved_sources": retrieved_sources,
        "source_matched": source_matched,
        "top_score": hits[0]["score"] if hits else None,
        "hit_count": len(hits),
        "latency_ms": round(latency_ms, 1),
    }


def run(cases_path: str, top_k: int) -> List[Dict[str, Any]]:
    cases = load_cases(cases_path)
    return [evaluate_case(case, top_k) for case in cases]


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
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])

    if not os.path.exists(args.cases):
        print(f"Eval cases file not found: {args.cases}")
        sys.exit(1)

    results = run(args.cases, args.top_k)
    print_report(results)

    failed = sum(1 for r in results if not r["passed"])
    sys.exit(1 if failed else 0)
