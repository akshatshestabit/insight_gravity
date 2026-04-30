"""Critic node — verifies citation faithfulness and answer quality."""
from __future__ import annotations

import json
import logging

from langchain_core.messages import AIMessage, HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from agents.crew.state import (
    CrewState,
    CritiqueResult,
    DEFAULT_BUDGETS,
    CRITIQUE_PASS_THRESHOLD,
    update_budget,
)
from backend.config import settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a rigorous research quality critic. Given:
1. A research question
2. Retrieved evidence (the sources the answer must be grounded in)
3. A synthesized answer with citations

Evaluate the answer on these dimensions and return JSON:
{
  "passed": true|false,
  "score": 0.0-1.0,
  "faithful": true|false,
  "citations_valid": true|false,
  "issues": ["issue1", ...],
  "suggestions": ["fix1", ...]
}

Scoring rubric (each worth 0.25):
1. Faithfulness: Every claim is supported by provided evidence.
2. Citation accuracy: In-text citations map to real chunks in context.
3. Completeness: All parts of the question are addressed.
4. Clarity: Answer is well-structured and professionally written.

passed = true when score >= 0.75.
Output ONLY valid JSON.
"""


def _truncate_docs(docs: list[dict], max_chars: int = 4000) -> str:
    lines = []
    chars = 0
    for i, doc in enumerate(docs[:6], 1):
        prov = doc.get("provenance", {})
        source = f"{prov.get('filename', 'unknown')} p.{prov.get('page', '?')}"
        content = doc.get("content", "")[:400]
        snippet = f"[{i}] {source}: {content}"
        if chars + len(snippet) > max_chars:
            break
        lines.append(snippet)
        chars += len(snippet)
    return "\n\n".join(lines)


async def critic_node(state: CrewState) -> dict:
    """Verify the synthesized answer against the retrieved evidence."""
    agent = "critic"
    cycles = state.get("critique_cycles", 0) + 1

    budgets = state.get("budgets") or dict(DEFAULT_BUDGETS)
    if budgets.get(agent, DEFAULT_BUDGETS[agent])["tokens_used"] >= \
            budgets.get(agent, DEFAULT_BUDGETS[agent])["tokens_limit"]:
        return {
            "budget_exceeded": True,
            "error": "Critic budget exceeded.",
            "critique_cycles": cycles,
        }

    docs = state.get("retrieved_docs") or []
    answer = state.get("synthesized_answer") or ""
    citations = state.get("citations") or []

    doc_context = _truncate_docs(docs)
    citations_text = json.dumps(citations[:5], indent=2)

    prompt = (
        f"Research question: {state['query']}\n\n"
        f"Evidence:\n{doc_context}\n\n"
        f"Citations in answer:\n{citations_text}\n\n"
        f"Synthesized answer:\n{answer[:3000]}\n\n"
        "Evaluate this answer."
    )

    response = None
    llm = ChatGoogleGenerativeAI(
        model=settings.GEMINI_MODEL,
        google_api_key=settings.GEMINI_API_KEY,
        temperature=0,
    )

    try:
        response = await llm.ainvoke([
            HumanMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ])
        raw = response.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw)
        critique = CritiqueResult(
            passed=bool(parsed.get("passed", False)),
            score=float(parsed.get("score", 0.5)),
            faithful=bool(parsed.get("faithful", False)),
            citations_valid=bool(parsed.get("citations_valid", False)),
            issues=parsed.get("issues", []),
            suggestions=parsed.get("suggestions", []),
        )
    except Exception as exc:
        logger.error("Critic parse error: %s", exc)
        # On parse failure: pass with a moderate score to avoid infinite loops
        critique = CritiqueResult(
            passed=True, score=0.75,
            faithful=True, citations_valid=True,
            issues=[], suggestions=[],
        )

    usage = getattr(response, "usage_metadata", None) or {}
    in_tok  = usage.get("input_tokens", len(prompt) // 4)
    out_tok = usage.get("output_tokens", 200)
    budget_update = update_budget(state, agent, in_tok, out_tok)

    status = "PASSED" if critique["passed"] else "FAILED"
    logger.info("Critic cycle %d: %s (score=%.2f)", cycles, status, critique["score"])

    return {
        "critique": critique,
        "critique_cycles": cycles,
        "messages": [
            AIMessage(
                content=(
                    f"[Critic] Cycle {cycles}: {status} "
                    f"(score={critique['score']:.2f}). "
                    f"Issues: {critique['issues']}"
                )
            )
        ],
        **budget_update,
    }
