"""
REST wrapper around the MCP tool implementations.
Exposes: CRM, Jira, Slack, Email, Audit log — for UI testing.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from mcp.audit import log_mcp_invocation, verify_entry
from mcp.tools.crm import crm_lookup, search_crm
from mcp.tools.jira import create_jira_ticket, get_jira_ticket, list_jira_tickets, update_jira_ticket
from mcp.tools.slack import post_to_slack, list_slack_channels
from mcp.tools.email_tool import draft_email, send_email, list_drafts

router = APIRouter(prefix="/mcp", tags=["mcp"])

SESSION_ID = "ui-test"


def _log(name: str, args: dict, result: Any) -> None:
    log_mcp_invocation(
        tool_name=name,
        arguments=args,
        result=result,
        session_id=SESSION_ID,
        server_name="InsightForge Enterprise MCP",
        success="error" not in (result if isinstance(result, dict) else {}),
    )


# ── CRM ───────────────────────────────────────────────────────────────────────

@router.get("/crm/{customer_id}")
async def api_crm_lookup(customer_id: str):
    result = crm_lookup(customer_id)
    _log("crm_lookup", {"customer_id": customer_id}, result)
    return result


class CRMSearch(BaseModel):
    name: Optional[str] = None
    tier: Optional[str] = None

@router.post("/crm/search")
async def api_crm_search(body: CRMSearch):
    result = search_crm(name=body.name, tier=body.tier)
    _log("crm_search", body.model_dump(exclude_none=True), result)
    return result


# ── Jira ──────────────────────────────────────────────────────────────────────

class JiraCreate(BaseModel):
    summary: str
    description: str
    priority: str = "Medium"
    issue_type: str = "Task"
    assignee: Optional[str] = None
    labels: Optional[list[str]] = None

@router.post("/jira/ticket")
async def api_create_jira(body: JiraCreate):
    result = create_jira_ticket(**body.model_dump(exclude_none=True))
    _log("create_jira_ticket", body.model_dump(exclude_none=True), result)
    return result


@router.get("/jira/ticket/{ticket_key}")
async def api_get_jira(ticket_key: str):
    result = get_jira_ticket(ticket_key)
    _log("get_jira_ticket", {"ticket_key": ticket_key}, result)
    return result


@router.get("/jira/tickets")
async def api_list_jira(status: Optional[str] = None, assignee: Optional[str] = None, limit: int = 20):
    result = list_jira_tickets(status=status, assignee=assignee, limit=limit)
    _log("list_jira_tickets", {"status": status, "assignee": assignee}, result)
    return result


class JiraUpdate(BaseModel):
    status: Optional[str] = None
    assignee: Optional[str] = None
    comment: Optional[str] = None

@router.patch("/jira/ticket/{ticket_key}")
async def api_update_jira(ticket_key: str, body: JiraUpdate):
    result = update_jira_ticket(ticket_key=ticket_key, **body.model_dump(exclude_none=True))
    _log("update_jira_ticket", {"ticket_key": ticket_key, **body.model_dump(exclude_none=True)}, result)
    return result


# ── Slack ─────────────────────────────────────────────────────────────────────

class SlackPost(BaseModel):
    channel: str
    message: str
    username: str = "InsightForge Bot"

@router.post("/slack/post")
async def api_slack_post(body: SlackPost):
    result = post_to_slack(**body.model_dump())
    _log("post_to_slack", body.model_dump(), result)
    return result


@router.get("/slack/channels")
async def api_slack_channels():
    result = list_slack_channels()
    _log("list_slack_channels", {}, result)
    return result


# ── Email ─────────────────────────────────────────────────────────────────────

class EmailDraft(BaseModel):
    to: str
    subject: str
    body: str
    cc: Optional[str] = None
    format: str = "text"

@router.post("/email/draft")
async def api_draft_email(body: EmailDraft):
    result = draft_email(**body.model_dump(exclude_none=True))
    _log("draft_email", body.model_dump(exclude_none=True), result)
    return result


@router.post("/email/send/{draft_id}")
async def api_send_email(draft_id: str):
    result = send_email(draft_id)
    _log("send_email", {"draft_id": draft_id}, result)
    return result


@router.get("/email/drafts")
async def api_list_drafts():
    return list_drafts()


# ── Audit log ─────────────────────────────────────────────────────────────────

@router.get("/audit")
async def api_audit_log(limit: int = 50):
    """Return the last N entries from audit.log with tamper status."""
    audit_file = Path("audit.log")
    if not audit_file.exists():
        return []
    lines = audit_file.read_text(encoding="utf-8").strip().splitlines()
    entries = []
    for line in lines[-limit:]:
        try:
            entry = json.loads(line)
            entry["_valid"] = verify_entry(entry)
            entries.append(entry)
        except Exception:
            pass
    return list(reversed(entries))
