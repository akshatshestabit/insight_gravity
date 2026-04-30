"""
Session management endpoints for long-running research investigations.

GET  /session/{id}           — Retrieve session metadata and current status
POST /session/{id}/resume    — Resume a paused (HITL) session
GET  /session/{id}/messages  — Full agent message trace for a session
DELETE /session/{id}         — Cancel and delete a session
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from agents.crew.graph import resume_crew, make_postgres_checkpointer
from backend.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/session", tags=["session"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class SessionStatus(BaseModel):
    session_id: str
    query: str
    status: str                  # running | completed | awaiting_approval | error
    cost_usd: float
    duration_ms: int
    error: Optional[str]   = None
    created_at: Optional[str] = None


class ResumeRequest(BaseModel):
    approved: bool = True        # set False to abort the paused session
    feedback: Optional[str] = None  # optional human feedback injected before resuming


class ResumeResponse(BaseModel):
    session_id: str
    status: str
    final_report: Optional[dict]  = None
    critique: Optional[dict]      = None
    cost_usd: float
    error: Optional[str]          = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/{session_id}", response_model=SessionStatus, summary="Get session status")
async def get_session(session_id: str):
    """Retrieve metadata and status for a research session."""
    async for db in get_db():
        row = await db.execute(
            text("SELECT * FROM research_sessions WHERE id = :id"),
            {"id": session_id},
        )
        record = row.mappings().first()

    if not record:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")

    return SessionStatus(
        session_id=session_id,
        query=record["query"],
        status="awaiting_approval" if record.get("awaiting_approval") else
               ("error" if record.get("error") else
               ("completed" if record.get("final_report") else "running")),
        cost_usd=float(record.get("cost_usd") or 0.0),
        duration_ms=int(record.get("duration_ms") or 0),
        error=record.get("error"),
        created_at=str(record.get("created_at") or ""),
    )


@router.post("/{session_id}/resume", response_model=ResumeResponse, summary="Resume HITL-paused session")
async def resume_session(session_id: str, body: ResumeRequest):
    """
    Resume a session that was paused at a Human-in-the-Loop interrupt node.

    If approved=False the session is aborted.
    Optional feedback is injected into the graph state before resuming.
    """
    if not body.approved:
        # Mark session as aborted in DB (best-effort)
        try:
            async for db in get_db():
                await db.execute(
                    text("UPDATE research_sessions SET error = 'Rejected by operator' WHERE id = :id"),
                    {"id": session_id},
                )
                await db.commit()
        except Exception as exc:
            logger.debug("Session abort DB update failed: %s", exc)

        return ResumeResponse(
            session_id=session_id,
            status="aborted",
            error="Session rejected by operator.",
            cost_usd=0.0,
        )

    try:
        checkpointer = await make_postgres_checkpointer()
        result = await resume_crew(session_id=session_id, checkpointer=checkpointer)
    except Exception as exc:
        logger.exception("Session resume failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    return ResumeResponse(
        session_id=session_id,
        status="completed" if not result.get("error") else "error",
        final_report=result.get("final_report"),
        critique=result.get("critique"),
        cost_usd=result.get("cost_usd", 0.0),
        error=result.get("error"),
    )


@router.get("/{session_id}/messages", summary="Retrieve full agent message trace")
async def get_session_messages(session_id: str):
    """
    Return the full LangGraph message history for a session.

    Useful for debugging agent reasoning or displaying a step-by-step trace
    in the research UI.
    """
    try:
        checkpointer = await make_postgres_checkpointer()

        # Retrieve latest checkpoint state from the checkpointer
        from langgraph.graph import StateGraph
        config = {"configurable": {"thread_id": session_id}}

        # Pull messages from the saved checkpoint
        snapshot = await checkpointer.aget(config)
        if not snapshot:
            raise HTTPException(status_code=404, detail=f"No checkpoint found for session '{session_id}'.")

        messages = snapshot.values.get("messages", [])
        trace = []
        for msg in messages:
            msg_type = type(msg).__name__
            content = msg.content if hasattr(msg, "content") else str(msg)
            trace.append({"type": msg_type, "content": content})

        return {"session_id": session_id, "message_count": len(trace), "messages": trace}

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Message retrieval failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/{session_id}", summary="Cancel and delete a session")
async def delete_session(session_id: str):
    """Delete a research session from the database."""
    try:
        async for db in get_db():
            result = await db.execute(
                text("DELETE FROM research_sessions WHERE id = :id RETURNING id"),
                {"id": session_id},
            )
            deleted = result.fetchone()
            await db.commit()

        if not deleted:
            raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")

        return {"session_id": session_id, "status": "deleted"}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Session delete failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
