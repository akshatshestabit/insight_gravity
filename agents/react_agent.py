"""
LangGraph ReAct Agent using Gemini.

Architecture:
  HumanMessage → [LLM reasons] → ToolCall? → [Tool executes] → loop → AIMessage (final answer)

LangGraph's create_react_agent handles the loop automatically.
We extract the full message trace for logging/display.
"""
import uuid
from typing import Any, Dict, List

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.prebuilt import create_react_agent

from agents.tools.calculator import calculator
from agents.tools.file_reader import file_reader
from agents.tools.web_search import web_search
from backend.config import settings

TOOLS = [calculator, web_search, file_reader]


def _build_agent():
    llm = ChatGoogleGenerativeAI(
        model=settings.GEMINI_MODEL,
        google_api_key=settings.GEMINI_API_KEY,
        temperature=0,
    )
    return create_react_agent(llm, TOOLS)


def _extract_trace(messages: list) -> List[Dict[str, Any]]:
    """Convert LangGraph message list into a human-readable trace."""
    trace = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            trace.append({"type": "input", "content": msg.content, "tool": None})

        elif isinstance(msg, AIMessage):
            # If the model made tool calls, record them as actions
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    trace.append({
                        "type": "action",
                        "content": tc.get("args", {}),
                        "tool": tc.get("name"),
                    })
            # Intermediate thoughts (model text before tool call)
            elif msg.content:
                trace.append({"type": "thought", "content": msg.content, "tool": None})

        elif isinstance(msg, ToolMessage):
            trace.append({
                "type": "observation",
                "content": msg.content,
                "tool": msg.name,
            })

    # Last AIMessage with real content = final answer
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content and not msg.tool_calls:
            trace.append({"type": "answer", "content": msg.content, "tool": None})
            break

    return trace


async def run_agent(message: str, session_id: str | None = None) -> Dict[str, Any]:
    """
    Run the ReAct agent and return the final answer + full trace.

    Returns:
        {
            "answer": str,
            "trace": [ {type, content, tool?}, ... ],
            "session_id": str
        }
    """
    if not session_id:
        session_id = str(uuid.uuid4())

    agent = _build_agent()

    result = await agent.ainvoke({"messages": [HumanMessage(content=message)]})
    messages = result["messages"]

    trace = _extract_trace(messages)

    # Final answer is the last non-tool-call AIMessage
    final_answer = ""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content and not msg.tool_calls:
            final_answer = msg.content
            break

    return {
        "answer": final_answer or "No answer produced.",
        "trace": trace,
        "session_id": session_id,
    }
