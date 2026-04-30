"""Planner node — decomposes the user query into a structured research plan."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from langchain_core.messages import AIMessage, HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from agents.crew.state import (
    CrewState,
    ResearchPlan,
    DEFAULT_BUDGETS,
    update_budget,
)
from backend.config import settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a research planning specialist. Given a user query, produce a
structured JSON research plan with these exact keys:
{
  "steps": ["step1", "step2", ...],
  "requires_retrieval": true,
  "requires_table_analysis": true|false,
  "requires_chart_analysis": true|false,
  "modalities": ["text", "table", "image"],
  "rationale": "why this plan"
}

Rules:
- requires_table_analysis = true if the query asks about numbers, statistics, comparisons, or trends.
- requires_chart_analysis = true if the query asks about figures, diagrams, charts, or visual content.
- modalities must be a subset of: text, table, image, graph, all.
- Output ONLY valid JSON — no markdown fences, no extra text.
"""


async def planner_node(state: CrewState) -> dict:
    """Decomposes the query and returns an updated state with a ResearchPlan."""
    agent = "planner"

    # ── Budget guard ───────────────────────────────────────────────────────
    budgets = state.get("budgets") or dict(DEFAULT_BUDGETS)
    budget = budgets.get(agent, DEFAULT_BUDGETS[agent])
    if budget["tokens_used"] >= budget["tokens_limit"]:
        logger.warning("Planner budget exceeded — skipping.")
        return {"budget_exceeded": True, "error": "Planner token budget exceeded."}

    llm = ChatGoogleGenerativeAI(
        model=settings.GEMINI_MODEL,
        google_api_key=settings.GEMINI_API_KEY,
        temperature=0,
    )

    query = state["query"]

    response = None
    user_msg = HumanMessage(content=f"Research query: {query}")

    try:
        response = await llm.ainvoke([
            HumanMessage(content=_SYSTEM_PROMPT),
            user_msg,
        ])
        raw = response.content.strip()

        # Strip optional markdown fences
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        plan_dict = json.loads(raw)
        plan: ResearchPlan = {
            "steps": plan_dict.get("steps", []),
            "requires_retrieval": bool(plan_dict.get("requires_retrieval", True)),
            "requires_table_analysis": bool(plan_dict.get("requires_table_analysis", False)),
            "requires_chart_analysis": bool(plan_dict.get("requires_chart_analysis", False)),
            "modalities": plan_dict.get("modalities", ["text"]),
            "rationale": plan_dict.get("rationale", ""),
        }

    except (json.JSONDecodeError, Exception) as exc:
        logger.error("Planner failed: %s", exc)
        plan = ResearchPlan(
            steps=["Retrieve relevant documents", "Synthesize findings"],
            requires_retrieval=True,
            requires_table_analysis=False,
            requires_chart_analysis=False,
            modalities=["text"],
            rationale="Fallback plan due to parsing error.",
        )

    # ── Usage metadata ─────────────────────────────────────────────────────
    usage = getattr(response, "usage_metadata", None) or {}
    in_tok  = usage.get("input_tokens", len(_SYSTEM_PROMPT) // 4)
    out_tok = usage.get("output_tokens", 100)

    budget_update = update_budget(state, agent, in_tok, out_tok)

    return {
        "plan": plan,
        "messages": [AIMessage(content=f"[Planner] Plan created: {plan['steps']}")],
        **budget_update,
    }
