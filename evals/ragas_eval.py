"""
RAGAS-based evaluation for InsightForge RAG pipeline.

Wraps the RAGAS library to evaluate:
- faithfulness
- answer_relevancy
- context_precision
- context_recall

Usage:
    from evals.ragas_eval import run_ragas
    results = await run_ragas(samples)
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def run_ragas(
    questions: list[str],
    answers: list[str],
    contexts: list[list[str]],
    ground_truths: Optional[list[str]] = None,
    llm=None,
    embeddings=None,
) -> dict:
    """
    Run RAGAS evaluation on a batch of QA samples.

    Args:
        questions:    List of user questions.
        answers:      List of generated answers.
        contexts:     List of retrieved context chunks per question.
        ground_truths: Optional reference answers.
        llm:          LangChain LLM for RAGAS judges (defaults to Gemini 2.5-Flash).
        embeddings:   LangChain embeddings (defaults to Google Generative AI).

    Returns:
        Dict with per-metric scores and sample-level breakdowns.
    """
    try:
        from datasets import Dataset
        from ragas import evaluate
        from ragas.metrics import faithfulness, answer_relevancy, context_precision
        from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
        from backend.config import settings
    except ImportError as e:
        logger.error("RAGAS dependencies missing: %s", e)
        return {"error": str(e)}

    if llm is None:
        llm = ChatGoogleGenerativeAI(
            model=settings.GEMINI_MODEL,
            google_api_key=settings.GEMINI_API_KEY,
            temperature=0,
        )
    if embeddings is None:
        embeddings = GoogleGenerativeAIEmbeddings(
            model="models/embedding-001",
            google_api_key=settings.GEMINI_API_KEY,
        )

    data = {
        "question": questions,
        "answer":   answers,
        "contexts": contexts,
    }
    if ground_truths:
        data["ground_truth"] = ground_truths

    dataset = Dataset.from_dict(data)

    metrics = [faithfulness, answer_relevancy]
    if ground_truths:
        metrics.append(context_precision)

    try:
        result = evaluate(
            dataset=dataset,
            metrics=metrics,
            llm=llm,
            embeddings=embeddings,
            raise_exceptions=False,
        )
        scores = result.to_pandas().to_dict(orient="list")
        aggregate = {
            metric: round(float(sum(v for v in vals if v is not None) / max(len([v for v in vals if v is not None]), 1)), 4)
            for metric, vals in scores.items()
            if metric not in ("question", "answer", "contexts", "ground_truth")
        }
        return {"aggregate": aggregate, "per_sample": scores, "n_samples": len(questions)}

    except Exception as exc:
        logger.error("RAGAS evaluation failed: %s", exc)
        return {"error": str(exc), "n_samples": len(questions)}


async def ragas_from_research_results(research_responses: list[dict]) -> dict:
    """
    Convenience wrapper: take a list of /research API responses and run RAGAS.

    Each response dict should have keys: query, report.answer, retrieved_docs.
    """
    questions, answers, contexts = [], [], []
    for r in research_responses:
        questions.append(r.get("query", ""))
        report = r.get("report") or {}
        answers.append(report.get("answer") or report.get("executive_summary") or "")
        docs = r.get("retrieved_docs") or []
        contexts.append([d.get("content", "") for d in docs[:5]])

    return await run_ragas(questions, answers, contexts)
