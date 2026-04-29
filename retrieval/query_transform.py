"""
Query transformation utilities:
  - expand_query  : generate 3 alternative phrasings via Gemini
  - crag_retry    : if top score < threshold, rewrite query and retry once
"""
import logging
from typing import Any, Callable, Dict, List

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage

from backend.config import settings

logger = logging.getLogger(__name__)


def _llm():
    return ChatGoogleGenerativeAI(
        model=settings.GEMINI_MODEL,
        google_api_key=settings.GEMINI_API_KEY,
        temperature=0.3,
    )


def expand_query(query: str) -> List[str]:
    """Generate 3 alternative query phrasings. Returns [original, ...variants]."""
    try:
        prompt = (
            "Generate 3 alternative ways to phrase this search query to improve "
            "document retrieval. Return only the 3 alternatives, one per line, no numbering:\n\n"
            f"Query: {query}"
        )
        resp = _llm().invoke([HumanMessage(content=prompt)])
        variants = [ln.strip() for ln in resp.content.strip().splitlines() if ln.strip()]
        return [query] + variants[:3]
    except Exception as e:
        logger.warning("Query expansion failed: %s", e)
        return [query]


def crag_retry(
    query: str,
    results: List[Dict[str, Any]],
    search_fn: Callable[[str], List[Dict[str, Any]]],
    threshold: float = 0.3,
) -> List[Dict[str, Any]]:
    """
    CRAG self-correction: if best score < threshold, rewrite query and retry.
    Returns the better of the two result sets.
    """
    best_score = results[0]["score"] if results else 0.0
    if best_score >= threshold:
        return results  # confident enough

    logger.info("CRAG: low confidence (best=%.4f), rewriting query…", best_score)
    try:
        prompt = (
            f"The query '{query}' returned poor document search results. "
            "Rewrite it to be more specific for retrieval. Return only the rewritten query."
        )
        resp = _llm().invoke([HumanMessage(content=prompt)])
        rewritten = resp.content.strip()
        logger.info("CRAG rewritten: %s", rewritten)
        new_results = search_fn(rewritten)
        return new_results if new_results else results
    except Exception as e:
        logger.warning("CRAG retry failed: %s", e)
        return results
