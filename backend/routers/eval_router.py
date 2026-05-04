"""
GET  /eval/health          — Check all Day 4 components
POST /eval/run             — Trigger evaluation run
POST /eval/red-team        — Trigger red-team suite
GET  /eval/audit-chain     — Verify audit chain integrity
POST /eval/validate        — Validate a query through guardrails
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/eval", tags=["eval"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class EvalRunRequest(BaseModel):
    job_id: Optional[str] = None
    limit:  Optional[int] = 20
    categories: Optional[list[str]] = None
    output_path: str = "eval_results.json"


class ValidateRequest(BaseModel):
    text: str
    check_injection: bool = True
    check_pii:       bool = True


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/health", summary="Day 4 component health check")
async def eval_health():
    """Verify all Day 4 modules are importable and functional."""
    status = {}

    try:
        from guardrails.injection import check_injection
        r = check_injection("hello world")
        status["guardrails_injection"] = "ok"
    except Exception as e:
        status["guardrails_injection"] = str(e)

    try:
        from guardrails.pii import detect_pii
        detect_pii("test@example.com", use_presidio=False)
        status["guardrails_pii"] = "ok"
    except Exception as e:
        status["guardrails_pii"] = str(e)

    try:
        from audit.chain import AuditChain
        AuditChain()
        status["audit_chain"] = "ok"
    except Exception as e:
        status["audit_chain"] = str(e)

    try:
        from observability.langfuse_tracer import tracer
        status["langfuse"] = "ok (keys required for tracing)"
    except Exception as e:
        status["langfuse"] = str(e)

    try:
        from security.rbac import _get_store
        store = _get_store()
        status["rbac"] = f"ok ({len(store)} keys loaded)"
    except Exception as e:
        status["rbac"] = str(e)

    try:
        import ragas
        status["ragas"] = ragas.__version__
    except Exception as e:
        status["ragas"] = str(e)

    all_ok = all(v.startswith("ok") or "." in v for v in status.values())
    return {"status": "healthy" if all_ok else "degraded", "components": status}


@router.post("/validate", summary="Run guardrails validation on a query")
async def validate_query_endpoint(req: ValidateRequest):
    """
    Run the full guardrails validation pipeline on a query string.
    Returns whether the input would be blocked and any PII detected.
    """
    from guardrails.validators import validate_query
    result = validate_query(req.text, redact_pii=True)
    return {
        "valid": result.valid,
        "cleaned_text": result.cleaned_text,
        "errors": result.errors,
        "warnings": result.warnings,
        "pii_detected": result.pii_detected,
        "injection_score": result.injection_score,
    }


@router.post("/run", summary="Run golden-dataset evaluation")
async def run_evaluation(req: EvalRunRequest, background_tasks: BackgroundTasks):
    """
    Trigger an evaluation run against the golden dataset.
    Returns immediately with a task ID; results are written to output_path.
    For synchronous results use limit <= 5.
    """
    if req.limit and req.limit <= 5:
        from evals.runner import run_eval, print_report
        result = await run_eval(
            job_id=req.job_id,
            limit=req.limit,
            categories=req.categories,
            output_path=req.output_path,
        )
        return {
            "status": "complete",
            "composite_score": result.composite_score,
            "passed": result.passed,
            "failed": result.failed,
            "total": result.total,
            "by_category": result.by_category,
            "by_metric": result.by_metric,
            "duration_s": result.duration_s,
        }

    # Large runs → background
    async def _bg():
        from evals.runner import run_eval
        await run_eval(job_id=req.job_id, limit=req.limit,
                       categories=req.categories, output_path=req.output_path)

    background_tasks.add_task(_bg)
    return {
        "status": "running",
        "message": f"Evaluation started in background. Results will be written to {req.output_path}",
        "limit": req.limit,
    }


@router.post("/red-team", summary="Run prompt injection red-team suite")
async def run_red_team_endpoint(background_tasks: BackgroundTasks,
                                 categories: Optional[list[str]] = None):
    """Run the red-team test suite (20 cases) against the live API."""
    async def _bg():
        from security.red_team import run_red_team
        await run_red_team(categories=categories, output_path="red_team_results.json")

    background_tasks.add_task(_bg)
    return {"status": "running", "message": "Red-team suite started. Results → red_team_results.json"}


@router.get("/audit-chain", summary="Verify audit chain integrity")
async def verify_audit_chain():
    """Verify the hash-chained audit log has not been tampered with."""
    from audit.chain import AuditChain
    chain = AuditChain()
    ok, errors = chain.verify()
    tail = chain.tail(5)
    return {
        "intact": ok,
        "error_count": len(errors),
        "errors": errors[:10],
        "recent_entries": [
            {"seq": e.get("seq"), "event_type": e.get("event_type"),
             "actor": e.get("actor"), "timestamp": e.get("timestamp")}
            for e in tail
        ],
    }
