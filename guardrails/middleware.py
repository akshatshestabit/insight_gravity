"""
FastAPI guardrails middleware.

Intercepts every request/response to:
1. Validate and clean query inputs (injection, PII, toxicity)
2. Redact PII from LLM outputs
3. Log guardrail events to the audit chain

Applied selectively to /chat, /research, /retrieve endpoints.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from guardrails.validators import validate_query, validate_output

logger = logging.getLogger(__name__)

# Endpoints where guardrails are active
_GUARDED_PATHS = {"/chat", "/research", "/retrieve"}


class GuardrailsMiddleware(BaseHTTPMiddleware):
    """
    Thin middleware that validates inputs and sanitizes outputs for LLM endpoints.
    Heavy-handed blocking happens only on injection / toxicity failures.
    PII redaction in outputs is always on.
    """

    def __init__(self, app: ASGIApp, redact_output_pii: bool = True):
        super().__init__(app)
        self.redact_output_pii = redact_output_pii

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path

        # Only guard LLM-facing endpoints
        if not any(path.startswith(p) for p in _GUARDED_PATHS):
            return await call_next(request)

        t0 = time.perf_counter()

        # ── Input guard ───────────────────────────────────────────────────────
        if request.method == "POST":
            try:
                body_bytes = await request.body()
                body = json.loads(body_bytes) if body_bytes else {}
                query = body.get("query") or body.get("message") or ""

                if query:
                    result = validate_query(query)
                    if not result.valid:
                        logger.warning("Input blocked at %s: %s", path, result.errors)
                        _log_guardrail_event("input_blocked", path, query, result.errors)
                        return JSONResponse(
                            status_code=422,
                            content={"detail": result.errors[0], "guardrail": "input_validation"},
                        )

                    # Replace query with cleaned (PII-redacted) version
                    if result.cleaned_text != query:
                        body["query" if "query" in body else "message"] = result.cleaned_text
                        body_bytes = json.dumps(body).encode()
                        if result.warnings:
                            logger.info("Guardrail warnings at %s: %s", path, result.warnings)

                # Reconstruct request with potentially modified body
                async def _body_override():
                    return body_bytes

                request._body = body_bytes  # type: ignore[attr-defined]

            except (json.JSONDecodeError, Exception) as exc:
                logger.debug("Guardrail body parse skipped: %s", exc)

        # ── Call endpoint ─────────────────────────────────────────────────────
        response = await call_next(request)

        # ── Output guard ──────────────────────────────────────────────────────
        if self.redact_output_pii and response.status_code == 200:
            try:
                body_bytes = b""
                async for chunk in response.body_iterator:
                    body_bytes += chunk
                body = json.loads(body_bytes)

                # Redact PII from answer/executive_summary fields
                modified = False
                for field in ("answer", "executive_summary", "content"):
                    val = _deep_get(body, field)
                    if val and isinstance(val, str):
                        cleaned = validate_output(val, redact_pii_in_output=True)
                        if cleaned.pii_detected:
                            _deep_set(body, field, cleaned.cleaned_text)
                            modified = True
                            logger.info("PII redacted from output field '%s' at %s", field, path)

                elapsed = round((time.perf_counter() - t0) * 1000, 1)
                logger.debug("Guardrail processed %s in %dms (modified=%s)", path, elapsed, modified)

                return Response(
                    content=json.dumps(body),
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    media_type="application/json",
                )
            except Exception as exc:
                logger.debug("Output guard skipped: %s", exc)

        return response


def _log_guardrail_event(event_type: str, path: str, query: str, errors: list) -> None:
    """Write guardrail events to the audit log."""
    try:
        from mcp.audit import log_mcp_invocation
        log_mcp_invocation(
            tool_name=f"guardrail:{event_type}",
            arguments={"path": path, "query_preview": query[:100]},
            result={"errors": errors},
            session_id="guardrail",
            server_name="GuardrailsMiddleware",
            success=False,
            error="; ".join(errors),
        )
    except Exception:
        pass


def _deep_get(d: dict, key: str) -> str | None:
    """Search for *key* at any depth in a nested dict."""
    if key in d:
        return d[key]
    for v in d.values():
        if isinstance(v, dict):
            r = _deep_get(v, key)
            if r is not None:
                return r
    return None


def _deep_set(d: dict, key: str, value: str) -> bool:
    """Set *key* to *value* at the first occurrence in a nested dict."""
    if key in d:
        d[key] = value
        return True
    for v in d.values():
        if isinstance(v, dict):
            if _deep_set(v, key, value):
                return True
    return False
