"""
InsightForge Multi-Agent Crew — LangGraph stateful graph.

Topology (supervisor-hierarchical):
  START → planner → retriever → [table_analyst?, chart_analyst?] → synthesizer → critic → END
                                        ↑__________________________|  (revision loop, max 3×)

Checkpointing: Postgres (AsyncPostgresSaver) with MemorySaver fallback.
HITL: graph can be compiled with interrupt_before=["synthesizer"] for approval gates.
Budget: hard-stop edges check budget_exceeded before each expensive node.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Literal

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.memory import MemorySaver

from agents.crew.state import (
    CrewState,
    DEFAULT_BUDGETS,
    MAX_CRITIQUE_CYCLES,
    CRITIQUE_PASS_THRESHOLD,
)
from agents.crew.nodes.planner import planner_node
from agents.crew.nodes.retriever import retriever_node
from agents.crew.nodes.table_analyst import table_analyst_node
from agents.crew.nodes.chart_analyst import chart_analyst_node
from agents.crew.nodes.synthesizer import synthesizer_node
from agents.crew.nodes.critic import critic_node

logger = logging.getLogger(__name__)


# ── Conditional-edge routing functions ───────────────────────────────────────

def _route_after_planner(state: CrewState) -> Literal["retriever", "__end__"]:
    """Abort if budget is already blown or planner failed."""
    if state.get("budget_exceeded") or state.get("error"):
        return END
    return "retriever"


def _route_after_retriever(state: CrewState) -> list[str] | str:
    """Fan out to specialist analysts based on the plan, or go straight to synthesizer."""
    if state.get("budget_exceeded") or state.get("error"):
        return END

    plan = state.get("plan") or {}
    targets = []
    if plan.get("requires_table_analysis"):
        targets.append("table_analyst")
    if plan.get("requires_chart_analysis"):
        targets.append("chart_analyst")

    return targets if targets else "synthesizer"


def _route_after_analysts(state: CrewState) -> Literal["synthesizer", "__end__"]:
    if state.get("budget_exceeded") or state.get("error"):
        return END
    return "synthesizer"


def _route_after_critic(state: CrewState) -> Literal["synthesizer", "__end__"]:
    """
    Pass  → END.
    Fail  → back to synthesizer for revision (up to MAX_CRITIQUE_CYCLES).
    Budget/error → END.
    """
    if state.get("budget_exceeded") or state.get("error"):
        return END

    critique = state.get("critique") or {}
    cycles = state.get("critique_cycles", 0)

    if critique.get("passed") or cycles >= MAX_CRITIQUE_CYCLES:
        return END

    logger.info("Critique cycle %d failed — sending back for revision.", cycles)
    return "synthesizer"


# ── Graph builder ─────────────────────────────────────────────────────────────

def _make_graph() -> StateGraph:
    g = StateGraph(CrewState)

    g.add_node("planner",       planner_node)
    g.add_node("retriever",     retriever_node)
    g.add_node("table_analyst", table_analyst_node)
    g.add_node("chart_analyst", chart_analyst_node)
    g.add_node("synthesizer",   synthesizer_node)
    g.add_node("critic",        critic_node)

    # ── Edges ──────────────────────────────────────────────────────────────
    g.add_edge(START, "planner")
    g.add_conditional_edges("planner", _route_after_planner)

    # Fan-out: retriever → [table_analyst, chart_analyst] OR synthesizer
    g.add_conditional_edges(
        "retriever",
        _route_after_retriever,
        {
            "table_analyst": "table_analyst",
            "chart_analyst": "chart_analyst",
            "synthesizer":   "synthesizer",
            END:             END,
        },
    )

    # Fan-in: both analysts converge at synthesizer
    g.add_conditional_edges("table_analyst", _route_after_analysts)
    g.add_conditional_edges("chart_analyst", _route_after_analysts)

    g.add_edge("synthesizer", "critic")
    g.add_conditional_edges("critic", _route_after_critic)

    return g


def build_crew_graph(
    *,
    checkpointer=None,
    interrupt_before: list[str] | None = None,
):
    """
    Compile the crew graph with the given checkpointer.

    Args:
        checkpointer: LangGraph checkpointer (Postgres or Memory).
                      If None, an in-memory saver is used.
        interrupt_before: Node names to pause at for HITL approval.
                          e.g. ["synthesizer"] to require approval before final answer.
    """
    if checkpointer is None:
        checkpointer = MemorySaver()

    g = _make_graph()
    kwargs: dict = {"checkpointer": checkpointer}
    if interrupt_before:
        kwargs["interrupt_before"] = interrupt_before

    return g.compile(**kwargs)


# ── Postgres checkpointer factory ─────────────────────────────────────────────

async def make_postgres_checkpointer():
    """
    Create an AsyncPostgresSaver backed by Postgres.
    Falls back to MemorySaver if the package or DB is unavailable.
    """
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from psycopg_pool import AsyncConnectionPool

        db_url = os.getenv(
            "DATABASE_URL",
            "postgresql://insightforge:insightforge@localhost:5432/insightforge",
        )
        # Strip SQLAlchemy driver prefix if present
        db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")

        pool = AsyncConnectionPool(conninfo=db_url, max_size=5, open=False)
        await pool.open()
        saver = AsyncPostgresSaver(pool)
        await saver.setup()   # creates the checkpoints table if absent
        logger.info("Using Postgres checkpointer.")
        return saver

    except Exception as exc:
        logger.warning("Postgres checkpointer unavailable (%s) — using MemorySaver.", exc)
        return MemorySaver()


# ── High-level runner ─────────────────────────────────────────────────────────

async def run_crew(
    query: str,
    job_ids: list[str] | None = None,
    session_id: str | None = None,
    max_cost_usd: float = 0.50,
    require_hitl: bool = False,
    checkpointer=None,
) -> dict:
    """
    Run the full multi-agent crew for a research query.

    Returns a structured dict with:
        session_id, plan, final_report, critique, cost_usd, duration_ms, error
    """
    session_id = session_id or str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)

    if checkpointer is None:
        checkpointer = await make_postgres_checkpointer()

    interrupt_nodes = ["synthesizer"] if require_hitl else None
    app = build_crew_graph(checkpointer=checkpointer, interrupt_before=interrupt_nodes)

    # Scale per-agent budgets proportionally to max_cost_usd
    budgets = {
        k: {**v, "cost_limit_usd": v["cost_limit_usd"] * (max_cost_usd / 0.50)}
        for k, v in DEFAULT_BUDGETS.items()
    }

    initial_state: CrewState = {
        "query": query,
        "session_id": session_id,
        "job_ids": job_ids or [],
        "messages": [],
        "plan": None,
        "retrieved_docs": [],
        "table_analysis": None,
        "chart_analysis": None,
        "synthesized_answer": None,
        "citations": [],
        "critique": None,
        "critique_cycles": 0,
        "budgets": budgets,
        "total_cost_usd": 0.0,
        "budget_exceeded": False,
        "awaiting_approval": False,
        "approval_reason": None,
        "final_report": None,
        "error": None,
    }

    config = {"configurable": {"thread_id": session_id}}

    try:
        final_state = await app.ainvoke(initial_state, config=config)
    except Exception as exc:
        logger.error("Crew run failed: %s", exc)
        final_state = {**initial_state, "error": str(exc)}

    elapsed_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)

    return {
        "session_id": session_id,
        "plan": final_state.get("plan"),
        "final_report": final_state.get("final_report"),
        "critique": final_state.get("critique"),
        "cost_usd": round(final_state.get("total_cost_usd", 0.0), 6),
        "duration_ms": elapsed_ms,
        "error": final_state.get("error"),
        "awaiting_approval": final_state.get("awaiting_approval", False),
    }


async def resume_crew(session_id: str, checkpointer=None) -> dict:
    """
    Resume a paused (HITL) crew session by its session_id.
    The graph was interrupted before 'synthesizer'; this continues from that point.
    """
    if checkpointer is None:
        checkpointer = await make_postgres_checkpointer()

    app = build_crew_graph(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": session_id}}

    try:
        # Invoking with None resumes from the last checkpoint
        final_state = await app.ainvoke(None, config=config)
    except Exception as exc:
        logger.error("Crew resume failed: %s", exc)
        return {"session_id": session_id, "error": str(exc)}

    return {
        "session_id": session_id,
        "final_report": final_state.get("final_report"),
        "critique": final_state.get("critique"),
        "cost_usd": round(final_state.get("total_cost_usd", 0.0), 6),
        "error": final_state.get("error"),
    }
