"""
FastAPI guardrails middleware — pure ASGI (no BaseHTTPMiddleware).

Avoids the known Starlette BaseHTTPMiddleware bug where re-emitting
a response body causes "Response content longer than Content-Length".

Applied to: /chat, /research, /retrieve
"""
from __future__ import annotations

import json
import logging
from typing import Callable

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from guardrails.validators import validate_query

logger = logging.getLogger(__name__)

_GUARDED_PATHS = {"/chat", "/research", "/retrieve"}


class GuardrailsMiddleware:
    """Pure ASGI middleware — correctly passes body to downstream handlers."""

    def __init__(self, app: ASGIApp, redact_output_pii: bool = True):
        self.app = app
        self.redact_output_pii = redact_output_pii

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path   = scope.get("path", "")
        method = scope.get("method", "")

        if not any(path.startswith(p) for p in _GUARDED_PATHS):
            await self.app(scope, receive, send)
            return

        # ── Read full body from ASGI stream ──────────────────────────────────
        body_chunks: list[bytes] = []
        if method == "POST":
            while True:
                msg = await receive()
                if msg["type"] == "http.request":
                    body_chunks.append(msg.get("body", b""))
                    if not msg.get("more_body", False):
                        break

        raw_body = b"".join(body_chunks)
        new_body = raw_body

        # ── Input validation ──────────────────────────────────────────────────
        if raw_body:
            try:
                body = json.loads(raw_body)
                query = body.get("query") or body.get("message") or ""

                if query:
                    result = validate_query(query)
                    if not result.valid:
                        logger.warning("Guardrail blocked %s: %s", path, result.errors)
                        _log_guardrail_event("input_blocked", path, query, result.errors)
                        resp_body = json.dumps({
                            "detail": result.errors[0],
                            "guardrail": "input_validation",
                        }).encode()
                        await send({
                            "type": "http.response.start",
                            "status": 422,
                            "headers": [
                                (b"content-type", b"application/json"),
                                (b"content-length", str(len(resp_body)).encode()),
                            ],
                        })
                        await send({"type": "http.response.body", "body": resp_body})
                        return

                    if result.cleaned_text != query:
                        key = "query" if "query" in body else "message"
                        body[key] = result.cleaned_text
                        new_body = json.dumps(body).encode()
                        logger.info("PII redacted from input at %s", path)

            except Exception as exc:
                logger.debug("Guardrail input parse skipped: %s", exc)

        # ── Rebuild receive with (possibly modified) body ─────────────────────
        body_consumed = False

        async def patched_receive() -> dict:
            nonlocal body_consumed
            if not body_consumed:
                body_consumed = True
                return {"type": "http.request", "body": new_body, "more_body": False}
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, patched_receive, send)


def _log_guardrail_event(event_type: str, path: str, query: str, errors: list) -> None:
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
