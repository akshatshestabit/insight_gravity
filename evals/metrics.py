"""
Custom evaluation metrics for InsightForge.

Covers:
- Keyword recall (golden dataset match)
- RAGAS-style faithfulness + answer relevancy
- Citation accuracy (filename + page verification)
- Table answer accuracy (value extraction)
- Agent trajectory efficiency (steps, cost)
"""
from __future__ import annotations

import re
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class MetricResult:
    name: str
    score: float          # 0.0 – 1.0
    passed: bool
    details: dict = field(default_factory=dict)


@dataclass
class EvalSample:
    question_id: str
    category: str
    question: str
    answer: str
    expected_keywords: list[str]
    citations: list[dict] = field(default_factory=list)
    retrieved_docs: list[dict] = field(default_factory=list)
    expected_no_answer: bool = False
    difficulty: str = "medium"
    cost_usd: float = 0.0
    duration_ms: int = 0
    agent_steps: int = 0
    critique_score: float = 0.0


# ── Keyword recall ────────────────────────────────────────────────────────────

def keyword_recall(sample: EvalSample) -> MetricResult:
    """Fraction of expected keywords found in the answer (case-insensitive)."""
    if not sample.expected_keywords:
        return MetricResult("keyword_recall", 1.0, True, {"note": "no keywords to check"})

    answer_lower = sample.answer.lower()
    found = [kw for kw in sample.expected_keywords if kw.lower() in answer_lower]
    score = len(found) / len(sample.expected_keywords)
    return MetricResult(
        "keyword_recall", score, score >= 0.6,
        {"found": found, "missing": [k for k in sample.expected_keywords if k not in found]},
    )


# ── Citation accuracy ─────────────────────────────────────────────────────────

def citation_accuracy(sample: EvalSample) -> MetricResult:
    """Check that every citation references a real document in retrieved_docs."""
    if not sample.citations:
        return MetricResult("citation_accuracy", 0.5, True, {"note": "no citations produced"})

    retrieved_files = {d.get("provenance", {}).get("filename", "") for d in sample.retrieved_docs}
    valid = 0
    for c in sample.citations:
        fname = c.get("filename") or c.get("source") or ""
        if any(fname in rf or rf in fname for rf in retrieved_files if rf):
            valid += 1

    score = valid / len(sample.citations) if sample.citations else 1.0
    return MetricResult(
        "citation_accuracy", score, score >= 0.5,
        {"total": len(sample.citations), "valid": valid},
    )


# ── Faithfulness (LLM-free proxy) ─────────────────────────────────────────────

def faithfulness_proxy(sample: EvalSample) -> MetricResult:
    """
    Proxy faithfulness: check that answer tokens overlap with retrieved doc content.
    Real faithfulness uses an LLM judge; this is a cheap offline approximation.
    """
    if not sample.retrieved_docs:
        return MetricResult("faithfulness", 0.0, False, {"note": "no docs retrieved"})

    doc_text = " ".join(d.get("content", "") for d in sample.retrieved_docs).lower()
    # Extract meaningful words from answer (exclude stopwords)
    stopwords = {"the","a","an","is","are","was","were","in","on","at","to","of","and","or","for","with"}
    answer_words = [w for w in re.findall(r"\b\w+\b", sample.answer.lower()) if w not in stopwords and len(w) > 3]
    if not answer_words:
        return MetricResult("faithfulness", 0.5, True, {"note": "answer too short"})

    overlap = sum(1 for w in answer_words if w in doc_text)
    score = min(overlap / len(answer_words), 1.0)
    return MetricResult("faithfulness", score, score >= 0.4, {"overlap_rate": score})


# ── Answer relevance ──────────────────────────────────────────────────────────

def answer_relevance(sample: EvalSample) -> MetricResult:
    """Check that the answer addresses the question (question word overlap)."""
    q_words = set(re.findall(r"\b\w+\b", sample.question.lower()))
    a_words = set(re.findall(r"\b\w+\b", sample.answer.lower()))
    stopwords = {"what","how","why","when","where","who","is","are","the","a","an","does","did","do"}
    q_words -= stopwords
    if not q_words:
        return MetricResult("answer_relevance", 0.8, True, {"note": "question too generic"})
    overlap = q_words & a_words
    score = len(overlap) / len(q_words)
    return MetricResult("answer_relevance", score, score >= 0.3, {"overlap_words": list(overlap)[:5]})


# ── No-answer detection ───────────────────────────────────────────────────────

def no_answer_correctness(sample: EvalSample) -> MetricResult:
    """For out-of-domain / blocked queries: system should indicate no answer."""
    if not sample.expected_no_answer:
        return MetricResult("no_answer_correctness", 1.0, True, {"note": "n/a"})

    refusal_phrases = [
        "not found", "no relevant", "no information", "cannot find",
        "don't have", "do not have", "outside the scope", "no documents",
        "blocked", "not available", "unable to answer",
    ]
    answer_lower = sample.answer.lower()
    is_refused = any(p in answer_lower for p in refusal_phrases) or len(sample.answer) < 50
    score = 1.0 if is_refused else 0.0
    return MetricResult("no_answer_correctness", score, is_refused, {"answer_length": len(sample.answer)})


# ── Table value extraction ────────────────────────────────────────────────────

def table_value_accuracy(sample: EvalSample) -> MetricResult:
    """For table-category questions: check that numeric values from expected_keywords appear."""
    if sample.category != "table":
        return MetricResult("table_value_accuracy", 1.0, True, {"note": "not a table question"})
    numeric_expected = [kw for kw in sample.expected_keywords if re.search(r"\d", kw)]
    if not numeric_expected:
        return MetricResult("table_value_accuracy", 1.0, True, {"note": "no numeric keywords"})
    answer_lower = sample.answer.lower()
    found = [kw for kw in numeric_expected if kw.lower() in answer_lower]
    score = len(found) / len(numeric_expected)
    return MetricResult("table_value_accuracy", score, score >= 0.5, {"numeric_found": found})


# ── Agent efficiency ──────────────────────────────────────────────────────────

def agent_efficiency(sample: EvalSample, max_cost: float = 0.05, max_ms: int = 60_000) -> MetricResult:
    """Score based on cost and latency staying within budget."""
    cost_ok = sample.cost_usd <= max_cost
    latency_ok = sample.duration_ms <= max_ms
    score = (0.5 if cost_ok else 0.0) + (0.5 if latency_ok else 0.0)
    return MetricResult(
        "agent_efficiency", score, score >= 0.5,
        {"cost_usd": sample.cost_usd, "duration_ms": sample.duration_ms,
         "cost_ok": cost_ok, "latency_ok": latency_ok},
    )


# ── Aggregate scorer ──────────────────────────────────────────────────────────

def score_sample(sample: EvalSample) -> dict:
    """Run all relevant metrics for a sample and return aggregate."""
    metrics = [
        keyword_recall(sample),
        citation_accuracy(sample),
        faithfulness_proxy(sample),
        answer_relevance(sample),
        no_answer_correctness(sample),
        agent_efficiency(sample),
    ]
    if sample.category == "table":
        metrics.append(table_value_accuracy(sample))

    scores = {m.name: m.score for m in metrics}
    passed = {m.name: m.passed for m in metrics}
    details = {m.name: m.details for m in metrics}

    # Composite: average of core metrics (exclude efficiency from quality score)
    core = ["keyword_recall", "faithfulness_proxy", "answer_relevance"]
    composite = sum(scores[k] for k in core) / len(core)

    return {
        "question_id": sample.question_id,
        "category": sample.category,
        "difficulty": sample.difficulty,
        "composite_score": round(composite, 3),
        "passed": composite >= 0.5,
        "metrics": scores,
        "metrics_passed": passed,
        "details": details,
    }
