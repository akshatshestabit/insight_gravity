"""Retriever node — executes multimodal RAG against Qdrant collections."""
from __future__ import annotations

import asyncio
import logging

from langchain_core.messages import AIMessage

from agents.crew.state import CrewState, DEFAULT_BUDGETS, update_budget
from retrieval.retriever import retrieve as _retrieve_sync

logger = logging.getLogger(__name__)


async def retriever_node(state: CrewState) -> dict:
    """Runs multimodal hybrid retrieval and stores results in state."""
    agent = "retriever"

    budgets = state.get("budgets") or dict(DEFAULT_BUDGETS)
    if budgets.get(agent, DEFAULT_BUDGETS[agent])["tokens_used"] >= \
            budgets.get(agent, DEFAULT_BUDGETS[agent])["tokens_limit"]:
        return {"budget_exceeded": True, "error": "Retriever token budget exceeded."}

    plan = state.get("plan") or {}
    query = state["query"]
    job_ids = state.get("job_ids") or []
    modalities = plan.get("modalities", ["text"])

    # Choose the retrieval modality — prefer "all" when multiple are needed
    modality = "all" if len(modalities) > 1 else (modalities[0] if modalities else "auto")

    try:
        # Use first job_id for scoped search, or search all if none specified
        job_id = job_ids[0] if job_ids else None
        # retrieve() is synchronous — run in thread to avoid blocking the event loop
        results = await asyncio.to_thread(
            _retrieve_sync,
            query,
            10,
            job_id,
            modality,
        )
        docs = [
            {
                "content": r.get("content", ""),
                "score": r.get("score", 0.0),
                "modality": r.get("modality", "text"),
                "provenance": r.get("provenance", {}),
            }
            for r in results
        ]
    except Exception as exc:
        logger.error("Retriever error: %s", exc)
        docs = []

    # Retriever itself doesn't call LLM, but count estimated overhead tokens
    budget_update = update_budget(state, agent, 500, 100)

    return {
        "retrieved_docs": docs,
        "messages": [AIMessage(content=f"[Retriever] Found {len(docs)} chunks for '{query}'")],
        **budget_update,
    }
