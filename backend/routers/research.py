"""
POST /research — Run the full multi-agent crew and return a citation-rich report.

The endpoint is synchronous from the client's perspective but internally
runs the LangGraph crew graph with Postgres checkpointing.

Flow:
  1. Validate request
  2. Build / reuse a Postgres checkpointer (cached per process)
  3. Run agents/crew/graph.run_crew()
  4. Return structured ResearchResponse

The endpoint also supports Server-Sent Events streaming of agent status via
GET /research/stream/{session_id}.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agents.crew.graph import run_crew, resume_crew, make_postgres_checkpointer
from backend.database import get_db
from sqlalchemy import text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/research", tags=["research"])

# ── Process-level checkpointer cache ─────────────────────────────────────────
_checkpointer = None
_checkpointer_lock = asyncio.Lock()


async def _get_checkpointer():
    global _checkpointer
    async with _checkpointer_lock:
        if _checkpointer is None:
            _checkpointer = await make_postgres_checkpointer()
    return _checkpointer


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class ResearchConfig(BaseModel):
    max_cost_usd: float = Field(default=0.50, ge=0.01, le=5.0)
    require_hitl: bool  = False
    modalities: list[str] = Field(default_factory=lambda: ["all"])


class ResearchRequest(BaseModel):
    query: str            = Field(..., min_length=3, max_length=2000)
    job_ids: list[str]   = Field(default_factory=list)
    session_id: Optional[str] = None
    config: ResearchConfig = Field(default_factory=ResearchConfig)


class CitationOut(BaseModel):
    source: str
    filename: str
    page: int
    content_preview: str
    relevance_score: float
    modality: str


class ReportSection(BaseModel):
    title: str
    content: str


class ResearchReport(BaseModel):
    executive_summary: str
    answer: str
    citations: list[CitationOut]
    table_analysis: Optional[str] = None
    chart_analysis: Optional[str] = None


class CritiqueOut(BaseModel):
    passed: bool
    score: float
    faithful: bool
    citations_valid: bool
    issues: list[str]
    suggestions: list[str]


class ResearchResponse(BaseModel):
    session_id: str
    query: str
    plan: Optional[dict] = None
    report: Optional[ResearchReport] = None
    critique: Optional[CritiqueOut]  = None
    cost_usd: float
    duration_ms: int
    awaiting_approval: bool = False
    error: Optional[str]   = None
    created_at: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("", response_model=ResearchResponse, summary="Run multi-agent research crew")
async def research(request: ResearchRequest, background_tasks: BackgroundTasks):
    """
    Decompose the query, retrieve evidence across all modalities,
    analyse tables and charts, synthesize a grounded answer, and verify it.

    Returns a structured, citation-rich research report.
    """
    session_id = request.session_id or str(uuid.uuid4())

    try:
        checkpointer = await _get_checkpointer()
        result = await run_crew(
            query=request.query,
            job_ids=request.job_ids,
            session_id=session_id,
            max_cost_usd=request.config.max_cost_usd,
            require_hitl=request.config.require_hitl,
            checkpointer=checkpointer,
        )
    except Exception as exc:
        logger.exception("Research crew failed for session %s", session_id)
        raise HTTPException(status_code=500, detail=str(exc))

    # ── Persist session to DB (best-effort) ───────────────────────────────
    background_tasks.add_task(_persist_session, session_id, request.query, result)

    # ── Build response ────────────────────────────────────────────────────
    final_report = result.get("final_report") or {}
    critique_raw = result.get("critique") or {}

    report = ResearchReport(
        executive_summary=final_report.get("executive_summary", ""),
        answer=final_report.get("answer", ""),
        citations=[
            CitationOut(
                source=c.get("source", ""),
                filename=c.get("filename", ""),
                page=c.get("page", 0),
                content_preview=c.get("content_preview", ""),
                relevance_score=c.get("relevance_score", 0.0),
                modality=c.get("modality", "text"),
            )
            for c in final_report.get("citations", [])
        ],
        table_analysis=final_report.get("table_analysis"),
        chart_analysis=final_report.get("chart_analysis"),
    ) if final_report else None

    critique = CritiqueOut(
        passed=critique_raw.get("passed", False),
        score=critique_raw.get("score", 0.0),
        faithful=critique_raw.get("faithful", False),
        citations_valid=critique_raw.get("citations_valid", False),
        issues=critique_raw.get("issues", []),
        suggestions=critique_raw.get("suggestions", []),
    ) if critique_raw else None

    return ResearchResponse(
        session_id=session_id,
        query=request.query,
        plan=result.get("plan"),
        report=report,
        critique=critique,
        cost_usd=result.get("cost_usd", 0.0),
        duration_ms=result.get("duration_ms", 0),
        awaiting_approval=result.get("awaiting_approval", False),
        error=result.get("error"),
        created_at=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/stream/{session_id}", summary="Stream agent-step events via SSE")
async def research_stream(session_id: str):
    """
    Server-Sent Events stream for a running research session.

    Each event contains a JSON payload describing which agent just completed
    and a summary of what it produced.  The client polls until the
    final_report event is received.
    """
    async def _event_generator():
        # In a production system you'd subscribe to a Redis pub/sub channel
        # that each agent node publishes to.  For now we return a synthetic
        # sequence of events as a demonstration.
        stages = [
            ("planner",       "Decomposing research query into a structured plan…"),
            ("retriever",     "Executing multimodal hybrid retrieval (dense + BM25)…"),
            ("table_analyst", "Analysing extracted tables with Pandas/DuckDB…"),
            ("chart_analyst", "Interpreting figures and charts with Gemini Vision…"),
            ("synthesizer",   "Composing citation-rich answer from all evidence…"),
            ("critic",        "Verifying citation faithfulness and answer quality…"),
        ]
        for agent, msg in stages:
            payload = json.dumps({"agent": agent, "message": msg, "session_id": session_id})
            yield f"event: agent_update\ndata: {payload}\n\n"
            await asyncio.sleep(0.1)   # non-blocking yield

        done = json.dumps({"agent": "done", "session_id": session_id, "message": "Research complete."})
        yield f"event: done\ndata: {done}\n\n"

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Persistence helper ────────────────────────────────────────────────────────

async def _persist_session(session_id: str, query: str, result: dict) -> None:
    """Write research session metadata to PostgreSQL (best-effort)."""
    try:
        async for db in get_db():
            await db.execute(
                text(
                    "INSERT INTO research_sessions "
                    "(id, query, plan, final_report, critique, cost_usd, duration_ms, error, created_at) "
                    "VALUES (:id, :query, :plan, :final_report, :critique, :cost_usd, :duration_ms, :error, NOW()) "
                    "ON CONFLICT (id) DO UPDATE SET "
                    "  final_report = EXCLUDED.final_report, "
                    "  critique     = EXCLUDED.critique, "
                    "  cost_usd     = EXCLUDED.cost_usd"
                ),
                {
                    "id":           session_id,
                    "query":        query,
                    "plan":         json.dumps(result.get("plan"), default=str),
                    "final_report": json.dumps(result.get("final_report"), default=str),
                    "critique":     json.dumps(result.get("critique"), default=str),
                    "cost_usd":     result.get("cost_usd", 0.0),
                    "duration_ms":  result.get("duration_ms", 0),
                    "error":        result.get("error"),
                },
            )
            await db.commit()
    except Exception as exc:
        logger.debug("Session persist skipped: %s", exc)
