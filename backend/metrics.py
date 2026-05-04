"""
Prometheus metrics for InsightForge.

Tracks:
  - HTTP request latency and counts (by endpoint, status)
  - LLM token usage and cost per agent
  - Retrieval latency and result counts
  - Tool call success / failure rates
  - Guardrail trips (injection blocks, PII redactions)
  - Semantic cache hit rate
  - Ingest throughput (chunks/s, documents/s)
  - Agent crew cost and duration

Exposes: GET /metrics  (Prometheus text format)

Grafana dashboard JSON: infra/grafana/dashboards/insightforge.json
"""
from __future__ import annotations

import time
from functools import wraps
from typing import Callable

from prometheus_client import (
    Counter, Gauge, Histogram, Info,
    CollectorRegistry, generate_latest, CONTENT_TYPE_LATEST,
    REGISTRY,
)
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

# ── Metric definitions ────────────────────────────────────────────────────────

# HTTP
http_requests_total = Counter(
    "insightforge_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
)
http_request_duration_seconds = Histogram(
    "insightforge_http_request_duration_seconds",
    "HTTP request latency",
    ["endpoint"],
    buckets=[0.1, 0.5, 1, 2, 5, 10, 30, 60, 120],
)

# LLM
llm_tokens_total = Counter(
    "insightforge_llm_tokens_total",
    "Total LLM tokens consumed",
    ["agent", "direction"],   # direction: input | output
)
llm_cost_usd_total = Counter(
    "insightforge_llm_cost_usd_total",
    "Total LLM cost in USD",
    ["agent"],
)
llm_call_duration_seconds = Histogram(
    "insightforge_llm_call_duration_seconds",
    "LLM call latency per agent",
    ["agent"],
    buckets=[0.5, 1, 2, 5, 10, 20, 30, 60],
)
llm_calls_total = Counter(
    "insightforge_llm_calls_total",
    "Total LLM calls",
    ["agent", "status"],   # status: success | error
)

# Retrieval
retrieval_duration_seconds = Histogram(
    "insightforge_retrieval_duration_seconds",
    "Retrieval pipeline latency",
    ["modality"],
    buckets=[0.05, 0.1, 0.5, 1, 2, 5],
)
retrieval_results_count = Histogram(
    "insightforge_retrieval_results_count",
    "Number of chunks returned per retrieval",
    buckets=[0, 1, 2, 5, 10, 20],
)
retrieval_cache_hits_total = Counter(
    "insightforge_retrieval_cache_hits_total",
    "Semantic cache hits",
)
retrieval_cache_misses_total = Counter(
    "insightforge_retrieval_cache_misses_total",
    "Semantic cache misses",
)

# Tool calls (MCP)
tool_calls_total = Counter(
    "insightforge_tool_calls_total",
    "MCP tool invocations",
    ["tool_name", "status"],
)
tool_call_duration_seconds = Histogram(
    "insightforge_tool_call_duration_seconds",
    "MCP tool call latency",
    ["tool_name"],
    buckets=[0.01, 0.05, 0.1, 0.5, 1, 5],
)

# Guardrails
guardrail_blocks_total = Counter(
    "insightforge_guardrail_blocks_total",
    "Guardrail blocks by type",
    ["block_type"],   # injection | pii | toxicity
)
guardrail_pii_redactions_total = Counter(
    "insightforge_guardrail_pii_redactions_total",
    "PII redactions applied",
    ["location"],   # input | output
)

# Ingest pipeline
ingest_documents_total = Counter(
    "insightforge_ingest_documents_total",
    "Documents ingested",
    ["status"],   # success | failed
)
ingest_chunks_total = Counter(
    "insightforge_ingest_chunks_total",
    "Chunks extracted and embedded",
    ["chunk_type"],   # text | table | image
)
ingest_duration_seconds = Histogram(
    "insightforge_ingest_duration_seconds",
    "Per-document ingestion duration",
    buckets=[1, 5, 10, 30, 60, 120, 300],
)

# Research crew
research_sessions_total = Counter(
    "insightforge_research_sessions_total",
    "Research crew runs",
    ["status"],   # completed | error | hitl_paused
)
research_duration_seconds = Histogram(
    "insightforge_research_duration_seconds",
    "Full crew run duration",
    buckets=[5, 10, 20, 30, 60, 120],
)
research_cost_usd = Histogram(
    "insightforge_research_cost_usd",
    "Cost per research session",
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5],
)
research_critique_score = Histogram(
    "insightforge_research_critique_score",
    "Critic score per session",
    buckets=[0.25, 0.5, 0.6, 0.7, 0.75, 0.8, 0.9, 1.0],
)

# Active sessions
active_research_sessions = Gauge(
    "insightforge_active_research_sessions",
    "Currently running research crew sessions",
)
active_ingest_jobs = Gauge(
    "insightforge_active_ingest_jobs",
    "Currently processing ingest jobs",
)

# System info
app_info = Info("insightforge_app", "InsightForge application information")
app_info.info({"version": "0.5.0", "day": "5", "environment": "production"})


# ── Convenience record functions ──────────────────────────────────────────────

def record_llm_call(agent: str, input_tokens: int, output_tokens: int,
                    cost_usd: float, duration_s: float, success: bool = True):
    llm_tokens_total.labels(agent=agent, direction="input").inc(input_tokens)
    llm_tokens_total.labels(agent=agent, direction="output").inc(output_tokens)
    llm_cost_usd_total.labels(agent=agent).inc(cost_usd)
    llm_call_duration_seconds.labels(agent=agent).observe(duration_s)
    llm_calls_total.labels(agent=agent, status="success" if success else "error").inc()


def record_retrieval(modality: str, n_results: int, duration_s: float, cache_hit: bool = False):
    retrieval_duration_seconds.labels(modality=modality).observe(duration_s)
    retrieval_results_count.observe(n_results)
    if cache_hit:
        retrieval_cache_hits_total.inc()
    else:
        retrieval_cache_misses_total.inc()


def record_tool_call(tool_name: str, duration_s: float, success: bool = True):
    tool_calls_total.labels(tool_name=tool_name, status="success" if success else "error").inc()
    tool_call_duration_seconds.labels(tool_name=tool_name).observe(duration_s)


def record_guardrail_block(block_type: str):
    guardrail_blocks_total.labels(block_type=block_type).inc()


def record_pii_redaction(location: str):
    guardrail_pii_redactions_total.labels(location=location).inc()


def record_research_session(status: str, duration_s: float, cost_usd: float, critique_score: float):
    research_sessions_total.labels(status=status).inc()
    research_duration_seconds.observe(duration_s)
    research_cost_usd.observe(cost_usd)
    if critique_score > 0:
        research_critique_score.observe(critique_score)


def record_ingest(status: str, duration_s: float, text_chunks: int = 0,
                  table_chunks: int = 0, image_chunks: int = 0):
    ingest_documents_total.labels(status=status).inc()
    ingest_duration_seconds.observe(duration_s)
    ingest_chunks_total.labels(chunk_type="text").inc(text_chunks)
    ingest_chunks_total.labels(chunk_type="table").inc(table_chunks)
    ingest_chunks_total.labels(chunk_type="image").inc(image_chunks)


# ── FastAPI middleware ─────────────────────────────────────────────────────────

class PrometheusMiddleware(BaseHTTPMiddleware):
    """Record HTTP request latency and counts for every endpoint."""

    SKIP_PATHS = {"/metrics", "/health", "/"}

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        if path in self.SKIP_PATHS:
            return await call_next(request)

        # Normalise path for cardinality control
        label = self._normalise(path)
        t0 = time.perf_counter()

        response = await call_next(request)

        duration = time.perf_counter() - t0
        http_requests_total.labels(
            method=request.method, endpoint=label, status=response.status_code
        ).inc()
        http_request_duration_seconds.labels(endpoint=label).observe(duration)
        return response

    @staticmethod
    def _normalise(path: str) -> str:
        parts = path.strip("/").split("/")
        # Replace UUIDs and long IDs with {id}
        cleaned = []
        for p in parts:
            if len(p) > 20 or (len(p) == 36 and p.count("-") == 4):
                cleaned.append("{id}")
            else:
                cleaned.append(p)
        return "/" + "/".join(cleaned)


# ── /metrics endpoint ─────────────────────────────────────────────────────────

async def metrics_endpoint(request: Request) -> Response:
    """Expose Prometheus metrics in text format."""
    return Response(
        content=generate_latest(REGISTRY),
        media_type=CONTENT_TYPE_LATEST,
    )
