from fastapi import APIRouter, HTTPException

from agents.react_agent import run_agent
from backend.models import ChatRequest, ChatResponse, TraceStep

router = APIRouter(prefix="/chat", tags=["Chat"])


@router.post("", response_model=ChatResponse, summary="Chat with the ReAct agent")
async def chat(request: ChatRequest):
    """
    Send a message to the LangGraph ReAct agent (powered by Gemini).
    Returns the final answer and the full reasoning trace
    (input → thought → action → observation → answer).
    """
    try:
        result = await run_agent(request.message, request.session_id)
        return ChatResponse(
            answer=result["answer"],
            trace=[TraceStep(**step) for step in result["trace"]],
            session_id=result["session_id"],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
