"""
Modality routing agent.
Given a user query, decides the best retrieval modality:
  text  → facts, concepts, narrative
  table → numbers, statistics, comparisons
  image → charts, diagrams, visual info
  graph → entity relationships
  all   → broad / unclear query
"""
import logging

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage

from backend.config import settings

logger = logging.getLogger(__name__)

VALID = {"text", "table", "image", "graph", "all"}


def route_query(query: str) -> str:
    """Return the best modality: text | table | image | graph | all."""
    prompt = (
        "You are a retrieval router. Given a user question, pick the best search modality.\n"
        "Options:\n"
        "  text  — facts, concepts, narrative paragraphs\n"
        "  table — numbers, stats, comparisons, structured data\n"
        "  image — charts, diagrams, figures, visual content\n"
        "  graph — relationships between people, orgs, or entities\n"
        "  all   — broad question or unclear — search everything\n\n"
        f"Question: {query}\n\n"
        "Reply with ONLY one word from: text, table, image, graph, all"
    )
    try:
        llm = ChatGoogleGenerativeAI(
            model=settings.GEMINI_MODEL,
            google_api_key=settings.GEMINI_API_KEY,
            temperature=0,
        )
        resp = llm.invoke([HumanMessage(content=prompt)])
        decision = resp.content.strip().lower().split()[0]
        if decision not in VALID:
            decision = "all"
        logger.info("Router: '%s' → %s", query[:60], decision)
        return decision
    except Exception as e:
        logger.warning("Router failed, defaulting to 'all': %s", e)
        return "all"
