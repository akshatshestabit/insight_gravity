"""
Streaming endpoints — real-time agent output via Server-Sent Events.

POST /stream/research    — Run crew and stream per-agent results as they complete
GET  /stream/chat/{id}   — WebSocket-compatible SSE chat with token streaming
GET  /cache/stats        — Semantic cache statistics
POST /cache/invalidate   — Flush semantic cache
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional, AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(tags=["streaming"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class StreamResearchRequest(BaseModel):
    query: str
    job_ids: list[str] = []
    max_cost_usd: float = 0.50


# ── SSE helpers ───────────────────────────────────────────────────────────────

def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _sse_error(message: str) -> str:
    return _sse("error", {"message": message})


# ── Streaming research endpoint ───────────────────────────────────────────────

@router.post("/stream/research", summary="Stream research crew — per-agent SSE")
async def stream_research(req: StreamResearchRequest):
    """
    Run the full 6-agent crew and stream results incrementally.

    SSE events emitted:
      agent_start   {agent, description}
      agent_done    {agent, result_preview, cost_usd, duration_ms}
      plan          {steps, requires_table, requires_chart}
      retrieval     {n_docs, modalities}
      table_analysis {preview}
      chart_analysis {preview}
      answer        {executive_summary, answer_preview, n_citations}
      critique      {passed, score, issues}
      done          {session_id, total_cost_usd, total_duration_ms}
      error         {message}
    """
    session_id = str(uuid.uuid4())

    async def _generate() -> AsyncGenerator[str, None]:
        # Check semantic cache first
        try:
            from retrieval.semantic_cache import get_semantic_cache
            cache = get_semantic_cache()
            cached = await cache.get(req.query)
            if cached:
                logger.info("Semantic cache hit for streaming request")
                yield _sse("cache_hit", {"similarity": cached.get("_similarity", 0)})
                report = cached.get("final_report") or {}
                crit   = cached.get("critique")   or {}
                yield _sse("plan", cached.get("plan") or {})
                yield _sse("answer", {
                    "executive_summary": (report.get("executive_summary") or "")[:200],
                    "answer_preview":    (report.get("answer") or "")[:300],
                    "n_citations":       len(report.get("citations") or []),
                })
                yield _sse("critique", crit)
                yield _sse("done", {
                    "session_id": session_id,
                    "total_cost_usd": cached.get("cost_usd", 0),
                    "cache_hit": True,
                })
                return
        except Exception as exc:
            logger.debug("Cache lookup skipped: %s", exc)

        # Run the crew with per-step streaming
        from agents.crew.state import DEFAULT_BUDGETS
        from agents.crew.graph import make_postgres_checkpointer, build_crew_graph
        from langgraph.checkpoint.memory import MemorySaver

        t_start = time.perf_counter()

        AGENT_DESCRIPTIONS = {
            "planner":       "Decomposing your query into a research plan…",
            "retriever":     "Searching across text, tables, and images…",
            "table_analyst": "Analysing extracted tables with Pandas & DuckDB…",
            "chart_analyst": "Interpreting figures and charts with Gemini Vision…",
            "synthesizer":   "Composing a citation-rich answer…",
            "critic":        "Verifying faithfulness and citation accuracy…",
        }

        try:
            checkpointer = await make_postgres_checkpointer()
        except Exception:
            checkpointer = MemorySaver()

        # Build graph that emits events per node via a queue
        event_queue: asyncio.Queue = asyncio.Queue()
        final_state: dict = {}

        async def _run_crew():
            """Run crew in background and push events to queue."""
            try:
                from agents.crew.nodes.planner import planner_node
                from agents.crew.nodes.retriever import retriever_node
                from agents.crew.nodes.table_analyst import table_analyst_node
                from agents.crew.nodes.chart_analyst import chart_analyst_node
                from agents.crew.nodes.synthesizer import synthesizer_node
                from agents.crew.nodes.critic import critic_node

                state = {
                    "query": req.query, "session_id": session_id,
                    "job_ids": req.job_ids, "messages": [],
                    "plan": None, "retrieved_docs": [], "table_analysis": None,
                    "chart_analysis": None, "synthesized_answer": None, "citations": [],
                    "critique": None, "critique_cycles": 0,
                    "budgets": dict(DEFAULT_BUDGETS), "total_cost_usd": 0.0,
                    "budget_exceeded": False, "awaiting_approval": False,
                    "approval_reason": None, "final_report": None, "error": None,
                }

                for agent_name, node_fn in [
                    ("planner",       planner_node),
                    ("retriever",     retriever_node),
                    ("table_analyst", table_analyst_node),
                    ("chart_analyst", chart_analyst_node),
                    ("synthesizer",   synthesizer_node),
                    ("critic",        critic_node),
                ]:
                    plan = state.get("plan") or {}
                    # Skip analysts if not needed
                    if agent_name == "table_analyst" and not plan.get("requires_table_analysis"):
                        continue
                    if agent_name == "chart_analyst" and not plan.get("requires_chart_analysis"):
                        continue
                    if state.get("budget_exceeded"):
                        break

                    t_agent = time.perf_counter()
                    await event_queue.put(("agent_start", {
                        "agent": agent_name,
                        "description": AGENT_DESCRIPTIONS.get(agent_name, ""),
                    }))

                    try:
                        patch = await node_fn(state)
                        state.update(patch)
                    except Exception as exc:
                        logger.error("Agent %s failed: %s", agent_name, exc)
                        state["error"] = str(exc)

                    dur_ms = round((time.perf_counter() - t_agent) * 1000)

                    # Build agent-specific result preview
                    preview = {}
                    if agent_name == "planner" and state.get("plan"):
                        plan = state["plan"]
                        preview = {"steps": plan.get("steps", [])[:3],
                                   "requires_table": plan.get("requires_table_analysis"),
                                   "requires_chart": plan.get("requires_chart_analysis")}
                        await event_queue.put(("plan", plan))
                    elif agent_name == "retriever":
                        docs = state.get("retrieved_docs") or []
                        preview = {"n_docs": len(docs),
                                   "modalities": list({d.get("modality") for d in docs})}
                        await event_queue.put(("retrieval", preview))
                    elif agent_name == "table_analyst":
                        ta = state.get("table_analysis") or ""
                        preview = {"preview": ta[:150]}
                        await event_queue.put(("table_analysis", preview))
                    elif agent_name == "chart_analyst":
                        ca = state.get("chart_analysis") or ""
                        preview = {"preview": ca[:150]}
                        await event_queue.put(("chart_analysis", preview))
                    elif agent_name == "synthesizer":
                        report = state.get("final_report") or {}
                        preview = {
                            "executive_summary": (report.get("executive_summary") or "")[:200],
                            "answer_preview":    (state.get("synthesized_answer") or "")[:300],
                            "n_citations":       len(state.get("citations") or []),
                        }
                        await event_queue.put(("answer", preview))
                    elif agent_name == "critic":
                        crit = state.get("critique") or {}
                        await event_queue.put(("critique", crit))

                    await event_queue.put(("agent_done", {
                        "agent": agent_name, "duration_ms": dur_ms,
                        "cost_usd": round(state.get("total_cost_usd", 0), 6),
                        **preview,
                    }))

                final_state.update(state)
            except Exception as exc:
                logger.exception("Crew streaming error: %s", exc)
                await event_queue.put(("error", {"message": str(exc)}))
            finally:
                await event_queue.put(None)  # sentinel

        crew_task = asyncio.create_task(_run_crew())

        # Drain the queue and yield SSE events
        while True:
            try:
                item = await asyncio.wait_for(event_queue.get(), timeout=120.0)
            except asyncio.TimeoutError:
                yield _sse_error("Crew timed out after 120 seconds")
                break

            if item is None:
                break

            event_type, payload = item
            yield _sse(event_type, payload)

        await crew_task

        total_duration_ms = round((time.perf_counter() - t_start) * 1000)
        total_cost        = round(final_state.get("total_cost_usd", 0), 6)

        yield _sse("done", {
            "session_id":        session_id,
            "total_cost_usd":    total_cost,
            "total_duration_ms": total_duration_ms,
            "error":             final_state.get("error"),
        })

        # Store in semantic cache for future hits
        if not final_state.get("error"):
            try:
                from retrieval.semantic_cache import get_semantic_cache
                cache = get_semantic_cache()
                await cache.set(req.query, {
                    "plan":         final_state.get("plan"),
                    "final_report": final_state.get("final_report"),
                    "critique":     final_state.get("critique"),
                    "cost_usd":     total_cost,
                    "duration_ms":  total_duration_ms,
                })
            except Exception as exc:
                logger.debug("Cache store skipped: %s", exc)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Access-Control-Allow-Origin": "*"},
    )


# ── Semantic cache management ─────────────────────────────────────────────────

@router.get("/cache/stats", summary="Semantic cache statistics")
async def cache_stats():
    from retrieval.semantic_cache import get_semantic_cache
    cache = get_semantic_cache()
    return await cache.stats()


@router.post("/cache/invalidate", summary="Flush semantic cache")
async def cache_invalidate(query: Optional[str] = None):
    from retrieval.semantic_cache import get_semantic_cache
    cache = get_semantic_cache()
    n = await cache.invalidate(query)
    return {"invalidated": n, "query": query or "all"}
