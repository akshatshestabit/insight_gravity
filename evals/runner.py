"""
Evaluation runner — orchestrates the full eval pipeline.

Usage:
    python -m evals.runner --job-id <id> --limit 20 --output results.json
    make eval JOB_ID=<id>
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from tabulate import tabulate

from evals.metrics import EvalSample, score_sample

logger = logging.getLogger(__name__)

API_BASE = "http://localhost:8000"
GOLDEN_PATH = Path(__file__).parent / "golden_data" / "questions.json"


@dataclass
class EvalResult:
    timestamp: str
    job_id: Optional[str]
    total: int
    passed: int
    failed: int
    skipped: int
    composite_score: float
    by_category: dict = field(default_factory=dict)
    by_difficulty: dict = field(default_factory=dict)
    by_metric: dict = field(default_factory=dict)
    samples: list[dict] = field(default_factory=list)
    duration_s: float = 0.0


async def _call_research(client: httpx.AsyncClient, question: str, job_id: Optional[str]) -> dict:
    """Call POST /research and return the response dict."""
    payload = {
        "query": question,
        "job_ids": [job_id] if job_id else [],
        "config": {"max_cost_usd": 0.10},
    }
    try:
        resp = await client.post("/research", json=payload, timeout=90.0)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        return {"error": str(exc), "query": question}


async def run_eval(
    job_id: Optional[str] = None,
    limit: Optional[int] = None,
    categories: Optional[list[str]] = None,
    output_path: Optional[str] = None,
    concurrency: int = 2,
) -> EvalResult:
    """
    Run the full golden-dataset evaluation.

    Args:
        job_id:      Qdrant job_id to scope retrieval (None = search all).
        limit:       Max questions to evaluate.
        categories:  Filter to specific categories (text, table, image, multihop, edge).
        output_path: Write JSON results to this file.
        concurrency: Parallel research requests (keep low to avoid rate limits).

    Returns:
        EvalResult with aggregate and per-sample scores.
    """
    t0 = time.perf_counter()
    golden = json.loads(GOLDEN_PATH.read_text())

    if categories:
        golden = [q for q in golden if q["category"] in categories]
    if limit:
        golden = golden[:limit]

    logger.info("Running eval on %d questions (job_id=%s)", len(golden), job_id)

    sem = asyncio.Semaphore(concurrency)
    sample_results: list[dict] = []

    async def _evaluate_one(q: dict) -> dict:
        async with sem:
            response = await _call_research(
                client, q["question"], job_id
            )

        if response.get("error"):
            return {
                "question_id": q["id"],
                "category": q["category"],
                "difficulty": q["difficulty"],
                "skipped": True,
                "error": response["error"],
                "composite_score": 0.0,
                "passed": False,
            }

        report = response.get("report") or {}
        sample = EvalSample(
            question_id=q["id"],
            category=q["category"],
            question=q["question"],
            answer=report.get("answer") or report.get("executive_summary") or "",
            expected_keywords=q.get("expected_keywords") or [],
            citations=report.get("citations") or [],
            retrieved_docs=[],          # not returned in API response directly
            expected_no_answer=q.get("expected_no_answer", False),
            difficulty=q["difficulty"],
            cost_usd=response.get("cost_usd", 0.0),
            duration_ms=response.get("duration_ms", 0),
            critique_score=(response.get("critique") or {}).get("score", 0.0),
        )
        return score_sample(sample)

    async with httpx.AsyncClient(base_url=API_BASE) as client:
        tasks = [_evaluate_one(q) for q in golden]
        sample_results = await asyncio.gather(*tasks)

    # ── Aggregate ─────────────────────────────────────────────────────────────
    valid = [r for r in sample_results if not r.get("skipped")]
    skipped = len(sample_results) - len(valid)
    passed = sum(1 for r in valid if r.get("passed"))
    failed = len(valid) - passed
    composite = sum(r["composite_score"] for r in valid) / max(len(valid), 1)

    by_category: dict = {}
    for r in valid:
        cat = r["category"]
        by_category.setdefault(cat, {"total": 0, "passed": 0, "score_sum": 0.0})
        by_category[cat]["total"] += 1
        by_category[cat]["passed"] += int(r.get("passed", False))
        by_category[cat]["score_sum"] += r.get("composite_score", 0.0)
    for cat in by_category:
        n = by_category[cat]["total"]
        by_category[cat]["avg_score"] = round(by_category[cat]["score_sum"] / n, 3) if n else 0.0

    by_difficulty: dict = {}
    for r in valid:
        diff = r["difficulty"]
        by_difficulty.setdefault(diff, {"total": 0, "passed": 0})
        by_difficulty[diff]["total"] += 1
        by_difficulty[diff]["passed"] += int(r.get("passed", False))

    # Per-metric averages
    metric_keys = set()
    for r in valid:
        metric_keys.update((r.get("metrics") or {}).keys())
    by_metric = {}
    for mk in metric_keys:
        vals = [r["metrics"][mk] for r in valid if mk in (r.get("metrics") or {})]
        by_metric[mk] = round(sum(vals) / len(vals), 3) if vals else 0.0

    result = EvalResult(
        timestamp=datetime.now(timezone.utc).isoformat(),
        job_id=job_id,
        total=len(sample_results),
        passed=passed,
        failed=failed,
        skipped=skipped,
        composite_score=round(composite, 3),
        by_category=by_category,
        by_difficulty=by_difficulty,
        by_metric=by_metric,
        samples=sample_results,
        duration_s=round(time.perf_counter() - t0, 1),
    )

    if output_path:
        out = Path(output_path)
        out.write_text(json.dumps(result.__dict__, indent=2, default=str))
        logger.info("Results written to %s", out)

    return result


def print_report(result: EvalResult) -> None:
    """Pretty-print evaluation results to stdout."""
    print("\n" + "═" * 60)
    print("  InsightForge Evaluation Report")
    print("═" * 60)
    print(f"  Timestamp : {result.timestamp}")
    print(f"  Job ID    : {result.job_id or 'all'}")
    print(f"  Duration  : {result.duration_s}s")
    print()
    print(f"  Total     : {result.total}")
    print(f"  Passed    : {result.passed}  ({100*result.passed//max(result.total,1)}%)")
    print(f"  Failed    : {result.failed}")
    print(f"  Skipped   : {result.skipped}")
    print(f"  Composite : {result.composite_score:.3f}")

    print("\n── By Category ──────────────────────────────────────────")
    rows = [(cat, v["total"], v["passed"], f"{v['avg_score']:.3f}")
            for cat, v in result.by_category.items()]
    print(tabulate(rows, headers=["Category", "Total", "Passed", "Avg Score"], tablefmt="rounded_grid"))

    print("\n── By Difficulty ────────────────────────────────────────")
    rows = [(diff, v["total"], v["passed"]) for diff, v in result.by_difficulty.items()]
    print(tabulate(rows, headers=["Difficulty", "Total", "Passed"], tablefmt="rounded_grid"))

    print("\n── Per-Metric Averages ───────────────────────────────────")
    rows = [(k, f"{v:.3f}") for k, v in result.by_metric.items()]
    print(tabulate(rows, headers=["Metric", "Avg Score"], tablefmt="rounded_grid"))
    print("═" * 60 + "\n")


# ── CLI entry point ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description="InsightForge Eval Runner")
    parser.add_argument("--job-id",    default=None, help="Qdrant job_id to scope retrieval")
    parser.add_argument("--limit",     type=int, default=None, help="Max questions to run")
    parser.add_argument("--categories",nargs="+", default=None, help="Filter categories")
    parser.add_argument("--output",    default="eval_results.json", help="Output JSON path")
    parser.add_argument("--concurrency", type=int, default=2, help="Parallel requests")
    args = parser.parse_args()

    result = asyncio.run(run_eval(
        job_id=args.job_id,
        limit=args.limit,
        categories=args.categories,
        output_path=args.output,
        concurrency=args.concurrency,
    ))
    print_report(result)
