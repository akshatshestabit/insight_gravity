"""Shared state TypedDict for the InsightForge multi-agent crew."""
from __future__ import annotations

from typing import Annotated, Any, Optional
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages


class AgentBudget(TypedDict):
    tokens_used: int
    tokens_limit: int
    cost_usd: float
    cost_limit_usd: float


class ResearchPlan(TypedDict):
    steps: list[str]
    requires_retrieval: bool
    requires_table_analysis: bool
    requires_chart_analysis: bool
    modalities: list[str]   # text | table | image | graph | all
    rationale: str


class Citation(TypedDict):
    source: str
    filename: str
    page: int
    content_preview: str
    relevance_score: float
    modality: str


class CritiqueResult(TypedDict):
    passed: bool
    score: float            # 0.0 – 1.0; threshold for pass is 0.75
    issues: list[str]
    suggestions: list[str]
    faithful: bool          # answer grounded in retrieved evidence
    citations_valid: bool   # every citation points to a real chunk


class CrewState(TypedDict):
    # ── Input ──────────────────────────────────────────────────────────────
    query: str
    session_id: str
    job_ids: list[str]              # Qdrant job_ids to scope retrieval

    # ── LangGraph message bus ──────────────────────────────────────────────
    messages: Annotated[list, add_messages]

    # ── Planning ───────────────────────────────────────────────────────────
    plan: Optional[ResearchPlan]

    # ── Retrieval ──────────────────────────────────────────────────────────
    retrieved_docs: list[dict]      # [{content, score, modality, provenance}]

    # ── Specialist analysis ────────────────────────────────────────────────
    table_analysis: Optional[str]
    chart_analysis: Optional[str]

    # ── Synthesis ──────────────────────────────────────────────────────────
    synthesized_answer: Optional[str]
    citations: list[Citation]

    # ── Critique ───────────────────────────────────────────────────────────
    critique: Optional[CritiqueResult]
    critique_cycles: int            # hard-stop at MAX_CRITIQUE_CYCLES

    # ── Budget tracking (keyed by agent name) ──────────────────────────────
    budgets: dict[str, AgentBudget]
    total_cost_usd: float
    budget_exceeded: bool

    # ── Human-in-the-Loop ──────────────────────────────────────────────────
    awaiting_approval: bool
    approval_reason: Optional[str]

    # ── Final output ───────────────────────────────────────────────────────
    final_report: Optional[dict]    # structured citation-rich report
    error: Optional[str]


# ── Constants ─────────────────────────────────────────────────────────────────

MAX_CRITIQUE_CYCLES = 3
CRITIQUE_PASS_THRESHOLD = 0.75

# Per-agent default limits
DEFAULT_BUDGETS: dict[str, AgentBudget] = {
    "planner":       {"tokens_used": 0, "tokens_limit": 4_000,  "cost_usd": 0.0, "cost_limit_usd": 0.05},
    "retriever":     {"tokens_used": 0, "tokens_limit": 2_000,  "cost_usd": 0.0, "cost_limit_usd": 0.02},
    "table_analyst": {"tokens_used": 0, "tokens_limit": 8_000,  "cost_usd": 0.0, "cost_limit_usd": 0.10},
    "chart_analyst": {"tokens_used": 0, "tokens_limit": 8_000,  "cost_usd": 0.0, "cost_limit_usd": 0.10},
    "synthesizer":   {"tokens_used": 0, "tokens_limit": 16_000, "cost_usd": 0.0, "cost_limit_usd": 0.20},
    "critic":        {"tokens_used": 0, "tokens_limit": 8_000,  "cost_usd": 0.0, "cost_limit_usd": 0.10},
}

# Gemini 2.5-Flash approximate pricing (USD per token)
INPUT_COST_PER_TOKEN  = 0.15 / 1_000_000
OUTPUT_COST_PER_TOKEN = 0.60 / 1_000_000


def estimate_cost(input_tokens: int, output_tokens: int) -> float:
    return input_tokens * INPUT_COST_PER_TOKEN + output_tokens * OUTPUT_COST_PER_TOKEN


def update_budget(state: CrewState, agent: str, input_tok: int, output_tok: int) -> dict:
    """Return partial state dict updating budget for a given agent."""
    budgets = dict(state.get("budgets") or DEFAULT_BUDGETS)
    agent_budget = dict(budgets.get(agent, DEFAULT_BUDGETS.get(agent, {})))
    cost = estimate_cost(input_tok, output_tok)
    agent_budget["tokens_used"] += input_tok + output_tok
    agent_budget["cost_usd"] += cost
    budgets[agent] = agent_budget

    total = state.get("total_cost_usd", 0.0) + cost
    exceeded = (
        agent_budget["tokens_used"] > agent_budget["tokens_limit"]
        or agent_budget["cost_usd"] > agent_budget["cost_limit_usd"]
    )
    return {"budgets": budgets, "total_cost_usd": total, "budget_exceeded": exceeded}
