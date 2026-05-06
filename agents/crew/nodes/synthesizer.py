"""Synthesizer node — composes a grounded, citation-rich final answer."""
from __future__ import annotations

import json
import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from agents.crew.state import Citation, CrewState, DEFAULT_BUDGETS, update_budget
from backend.config import settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a research synthesis specialist. Given:
1. A research question
2. Retrieved document excerpts
3. Optional table analysis
4. Optional chart/figure analysis

Produce a comprehensive, citation-rich answer. Format your response as JSON:
{
  "answer": "Full, well-structured answer in markdown...",
  "citations": [
    {
      "source": "document_name.pdf",
      "filename": "document_name.pdf",
      "page": 3,
      "content_preview": "First 100 chars of cited chunk...",
      "relevance_score": 0.92,
      "modality": "text"
    }
  ],
  "executive_summary": "One-paragraph summary"
}

Rules:
- Every factual claim must map to a citation.
- Use [1], [2], ... inline citation markers.
- Output ONLY valid JSON.
"""


def _build_doc_context(docs: list[dict]) -> str:
    lines = []
    for i, doc in enumerate(docs[:8], 1):
        prov = doc.get("provenance", {})
        source = f"{prov.get('filename', 'Unknown')} p.{prov.get('page', '?')}"
        modality = doc.get("modality", "text").upper()
        score = doc.get("score", 0.0)
        content = doc.get("content", "")[:600]
        lines.append(f"[{i}] ({modality}, score={score:.2f}) {source}\n{content}")
    return "\n\n".join(lines)


async def synthesizer_node(state: CrewState) -> dict:
    """Compose a citation-rich answer from all gathered evidence."""
    agent = "synthesizer"

    budgets = state.get("budgets") or dict(DEFAULT_BUDGETS)
    if budgets.get(agent, DEFAULT_BUDGETS[agent])["tokens_used"] >= \
            budgets.get(agent, DEFAULT_BUDGETS[agent])["tokens_limit"]:
        return {"budget_exceeded": True, "error": "Synthesizer budget exceeded."}

    docs = state.get("retrieved_docs") or []
    table_analysis = state.get("table_analysis") or ""
    chart_analysis = state.get("chart_analysis") or ""

    doc_context = _build_doc_context(docs)

    sections = [f"## Retrieved Evidence\n{doc_context}"]
    if table_analysis:
        sections.append(f"## Table Analysis\n{table_analysis}")
    if chart_analysis:
        sections.append(f"## Chart/Figure Analysis\n{chart_analysis}")

    full_context = "\n\n".join(sections)

    prompt = (
        f"Research question: {state['query']}\n\n"
        f"{full_context[:12_000]}\n\n"
        "Now synthesize a comprehensive, citation-rich answer."
    )

    llm = ChatGoogleGenerativeAI(
        model=settings.GEMINI_MODEL,
        google_api_key=settings.GEMINI_API_KEY,
        temperature=0.1,
    )

    response = None
    answer = ""
    exec_summary = ""
    citations_raw = []
    try:
        response = await llm.ainvoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ])
        raw = response.content.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        parsed = json.loads(raw)
        answer = parsed.get("answer", "")
        exec_summary = parsed.get("executive_summary", "")
        citations_raw = parsed.get("citations", [])
    except json.JSONDecodeError as exc:
        logger.error("Synthesizer JSON parse error: %s", exc)
        answer = response.content if response is not None else "Synthesis failed."
    except Exception as exc:
        logger.error("Synthesizer LLM error: %s", exc)
        answer = "Could not synthesize answer due to an error."

    # Build Citation objects from docs for fallback if LLM didn't emit them
    if not citations_raw:
        citations_raw = [
            {
                "source": d.get("provenance", {}).get("filename", "Unknown"),
                "filename": d.get("provenance", {}).get("filename", "Unknown"),
                "page": d.get("provenance", {}).get("page", 0),
                "content_preview": d.get("content", "")[:100],
                "relevance_score": d.get("score", 0.0),
                "modality": d.get("modality", "text"),
            }
            for d in docs[:5]
        ]

    citations: list[Citation] = [
        Citation(
            source=c.get("source", ""),
            filename=c.get("filename", ""),
            page=c.get("page", 0),
            content_preview=c.get("content_preview", ""),
            relevance_score=c.get("relevance_score", 0.0),
            modality=c.get("modality", "text"),
        )
        for c in citations_raw
    ]

    usage = getattr(response, "usage_metadata", None) or {}
    in_tok  = usage.get("input_tokens", len(prompt) // 4)
    out_tok = usage.get("output_tokens", 500)
    budget_update = update_budget(state, agent, in_tok, out_tok)

    synthesized = answer or "No answer synthesized."

    return {
        "synthesized_answer": synthesized,
        "citations": citations,
        "messages": [AIMessage(content=f"[Synthesizer] Answer composed ({len(citations)} citations).")],
        "final_report": {
            "executive_summary": exec_summary,
            "answer": synthesized,
            "citations": citations_raw,
            "table_analysis": table_analysis,
            "chart_analysis": chart_analysis,
        },
        **budget_update,
    }
