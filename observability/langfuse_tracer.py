"""
LangFuse observability — full-trace integration for InsightForge.

Wraps every agent node, retrieval call, and tool invocation in a LangFuse span
so you can debug latency, token usage, and failures in the LangFuse dashboard.

Configuration (add to .env):
    LANGFUSE_PUBLIC_KEY=pk-lf-...
    LANGFUSE_SECRET_KEY=sk-lf-...
    LANGFUSE_HOST=https://cloud.langfuse.com   # or your self-hosted URL

If keys are not set the tracer runs in no-op mode (no network calls).

Usage:
    from observability.langfuse_tracer import tracer, trace_agent_node

    @trace_agent_node("planner")
    async def planner_node(state): ...
"""
from __future__ import annotations

import functools
import logging
import os
import time
from contextlib import contextmanager
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# ── LangFuse client (lazy singleton) ─────────────────────────────────────────

_lf_client = None


def _get_client():
    global _lf_client
    if _lf_client is not None:
        return _lf_client
    try:
        from langfuse import Langfuse
        pk = os.getenv("LANGFUSE_PUBLIC_KEY", "")
        sk = os.getenv("LANGFUSE_SECRET_KEY", "")
        host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")
        if not pk or not sk:
            logger.info("LangFuse keys not set — tracing disabled.")
            _lf_client = _NoopClient()
        else:
            _lf_client = Langfuse(public_key=pk, secret_key=sk, host=host)
            logger.info("LangFuse tracing enabled at %s", host)
    except ImportError:
        logger.warning("langfuse package not installed — tracing disabled.")
        _lf_client = _NoopClient()
    return _lf_client


class _NoopClient:
    """Drop-in replacement when LangFuse is unavailable."""
    def trace(self, **kwargs): return _NoopTrace()
    def flush(self): pass


class _NoopTrace:
    def span(self, **kwargs): return _NoopSpan()
    def generation(self, **kwargs): return _NoopSpan()
    def update(self, **kwargs): return self
    def end(self, **kwargs): return self


class _NoopSpan:
    def span(self, **kwargs): return _NoopSpan()
    def generation(self, **kwargs): return _NoopSpan()
    def update(self, **kwargs): return self
    def end(self, **kwargs): return self


# ── Convenience wrapper ───────────────────────────────────────────────────────

class InsightForgeTracer:
    """High-level tracer for InsightForge agent runs."""

    def start_research_trace(self, session_id: str, query: str):
        """Open a top-level trace for a /research invocation."""
        client = _get_client()
        return client.trace(
            id=session_id,
            name="research_crew",
            input={"query": query},
            session_id=session_id,
            tags=["research", "multi-agent"],
        )

    def span_agent(self, trace, agent_name: str, input_data: dict):
        """Open a child span for a specific agent node."""
        return trace.span(
            name=f"agent:{agent_name}",
            input=input_data,
            metadata={"agent": agent_name},
        )

    def span_retrieval(self, trace, query: str, modality: str, n_results: int):
        """Open a span for a retrieval call."""
        return trace.span(
            name="retrieval",
            input={"query": query, "modality": modality},
            metadata={"n_results": n_results},
        )

    def generation(self, trace, name: str, prompt: str, completion: str,
                   model: str, input_tokens: int, output_tokens: int, cost_usd: float):
        """Record an LLM generation event."""
        return trace.generation(
            name=name,
            model=model,
            input=prompt[:1000],
            output=completion[:1000],
            usage={"input": input_tokens, "output": output_tokens, "total": input_tokens + output_tokens},
            metadata={"cost_usd": cost_usd},
        )

    def end_trace(self, trace, output: dict, level: str = "DEFAULT"):
        """Close a research trace with final output."""
        try:
            trace.update(
                output={"cost_usd": output.get("cost_usd"), "critique_score": output.get("critique", {}).get("score")},
                level=level,
            )
        except Exception as exc:
            logger.debug("LangFuse trace end failed: %s", exc)
        finally:
            try:
                _get_client().flush()
            except Exception:
                pass


tracer = InsightForgeTracer()


# ── Decorator for agent nodes ─────────────────────────────────────────────────

def trace_agent_node(agent_name: str):
    """
    Decorator that wraps an async agent node function with LangFuse tracing.

    Usage:
        @trace_agent_node("synthesizer")
        async def synthesizer_node(state: CrewState) -> dict: ...
    """
    def decorator(fn: Callable):
        @functools.wraps(fn)
        async def wrapper(state: dict, *args, **kwargs):
            session_id = state.get("session_id", "unknown")
            t0 = time.perf_counter()

            client = _get_client()
            # Try to get the parent trace; fall back to creating a new one
            _trace = client.trace(
                name=f"agent:{agent_name}",
                session_id=session_id,
                input={"query": state.get("query", "")[:200]},
                tags=[agent_name],
            )
            span = _trace.span(name=agent_name, input={"query": state.get("query", "")[:200]})

            try:
                result = await fn(state, *args, **kwargs)
                elapsed = round((time.perf_counter() - t0) * 1000)
                span.end(output={"duration_ms": elapsed, "keys": list(result.keys()) if result else []})
                return result
            except Exception as exc:
                span.end(output={"error": str(exc)}, level="ERROR")
                raise

        return wrapper
    return decorator


# ── LangChain callback handler ────────────────────────────────────────────────

def get_langchain_handler(session_id: str):
    """
    Return a LangChain CallbackHandler for LangFuse.
    Pass this to LangChain/LangGraph invocations via config.

    Usage:
        handler = get_langchain_handler(session_id)
        await llm.ainvoke(messages, config={"callbacks": [handler]})
    """
    try:
        from langfuse.callback import CallbackHandler
        pk = os.getenv("LANGFUSE_PUBLIC_KEY", "")
        sk = os.getenv("LANGFUSE_SECRET_KEY", "")
        host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")
        if not pk or not sk:
            return None
        return CallbackHandler(public_key=pk, secret_key=sk, host=host, session_id=session_id)
    except ImportError:
        return None
