"""Chart Analyst node — uses Gemini Vision to interpret figures and images."""
from __future__ import annotations

import base64
import logging

from langchain_core.messages import AIMessage, HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from agents.crew.state import CrewState, DEFAULT_BUDGETS, update_budget
from backend.config import settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a visual chart and figure analyst. You will be given:
1. A research question
2. Captions and descriptions of figures/images extracted from documents

Your job:
- Interpret the visual content described in the captions
- Extract quantitative or qualitative insights relevant to the research question
- Note chart types, axes, trends, and key data points when mentioned in captions

Format your response as clear paragraphs. Reference specific figures by their source.
"""


async def chart_analyst_node(state: CrewState) -> dict:
    """Interpret image/chart chunks from the retrieval results."""
    agent = "chart_analyst"

    budgets = state.get("budgets") or dict(DEFAULT_BUDGETS)
    if budgets.get(agent, DEFAULT_BUDGETS[agent])["tokens_used"] >= \
            budgets.get(agent, DEFAULT_BUDGETS[agent])["tokens_limit"]:
        return {"budget_exceeded": True, "error": "Chart Analyst budget exceeded."}

    docs = state.get("retrieved_docs") or []
    image_docs = [d for d in docs if d.get("modality") == "image"]

    if not image_docs:
        return {
            "chart_analysis": "No chart or image content found in the retrieved documents.",
            "messages": [AIMessage(content="[ChartAnalyst] No image chunks to analyse.")],
        }

    # Build a context from captions (images are stored as captions in Qdrant)
    figure_texts = []
    for i, doc in enumerate(image_docs[:5], 1):   # cap at 5 images per run
        prov = doc.get("provenance", {})
        source = f"{prov.get('filename', 'Unknown')} p.{prov.get('page', '?')}"
        caption = doc.get("content", "(no caption)")
        figure_texts.append(f"Figure {i} [{source}]: {caption}")

    context = "\n\n".join(figure_texts)

    response = None
    llm = ChatGoogleGenerativeAI(
        model=settings.GEMINI_MODEL,
        google_api_key=settings.GEMINI_API_KEY,
        temperature=0,
    )

    prompt = (
        f"Research question: {state['query']}\n\n"
        f"Figures and charts found:\n{context}\n\n"
        "Provide a detailed visual analysis answering the research question based on these figures."
    )

    try:
        response = await llm.ainvoke([
            HumanMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ])
        analysis = response.content
    except Exception as exc:
        logger.error("Chart analyst LLM error: %s", exc)
        analysis = f"Could not analyse charts: {exc}"

    usage = getattr(response, "usage_metadata", None) or {}
    in_tok  = usage.get("input_tokens", len(prompt) // 4)
    out_tok = usage.get("output_tokens", 300)
    budget_update = update_budget(state, agent, in_tok, out_tok)

    return {
        "chart_analysis": analysis,
        "messages": [AIMessage(content=f"[ChartAnalyst] Analysed {len(image_docs)} figure(s).")],
        **budget_update,
    }
