"""
Jira / ticketing tool for the InsightForge MCP server.

Stub implementation — replace the _JIRA_DB dict with real Jira REST API calls.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

Priority = Literal["Highest", "High", "Medium", "Low", "Lowest"]

_JIRA_DB: dict[str, dict] = {}
_TICKET_COUNTER = 1000


def create_jira_ticket(
    summary: str,
    description: str,
    priority: Priority = "Medium",
    issue_type: str = "Task",
    assignee: str | None = None,
    labels: list[str] | None = None,
) -> dict:
    """
    Create a new Jira ticket.

    Args:
        summary:     Short ticket title (≤ 255 chars).
        description: Full description / acceptance criteria.
        priority:    Highest | High | Medium | Low | Lowest.
        issue_type:  Task | Bug | Story | Epic.
        assignee:    Jira username to assign to.
        labels:      List of labels to attach.

    Returns:
        Created ticket dict with key, id, and URL.
    """
    global _TICKET_COUNTER
    _TICKET_COUNTER += 1
    key = f"INS-{_TICKET_COUNTER}"
    now = datetime.now(timezone.utc).isoformat()

    ticket = {
        "key": key,
        "id": str(uuid.uuid4()),
        "summary": summary[:255],
        "description": description,
        "priority": priority,
        "issue_type": issue_type,
        "status": "To Do",
        "assignee": assignee,
        "labels": labels or [],
        "created_at": now,
        "updated_at": now,
        "url": f"https://your-org.atlassian.net/browse/{key}",
    }
    _JIRA_DB[key] = ticket
    return ticket


def get_jira_ticket(ticket_key: str) -> dict:
    """
    Fetch a Jira ticket by its key (e.g. "INS-1001").

    Returns:
        Ticket dict, or error dict if not found.
    """
    ticket = _JIRA_DB.get(ticket_key.upper())
    if not ticket:
        return {"error": f"Ticket '{ticket_key}' not found."}
    return ticket


def list_jira_tickets(
    status: str | None = None,
    assignee: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """
    List Jira tickets with optional filters.

    Args:
        status:   Filter by status (e.g. "To Do", "In Progress", "Done").
        assignee: Filter by assignee username.
        limit:    Maximum number of results (default 20).

    Returns:
        List of matching ticket summaries.
    """
    results = []
    for ticket in _JIRA_DB.values():
        if status and ticket["status"].lower() != status.lower():
            continue
        if assignee and ticket.get("assignee", "").lower() != assignee.lower():
            continue
        results.append({
            "key": ticket["key"],
            "summary": ticket["summary"],
            "status": ticket["status"],
            "priority": ticket["priority"],
            "assignee": ticket.get("assignee"),
        })
        if len(results) >= limit:
            break
    return results


def update_jira_ticket(
    ticket_key: str,
    status: str | None = None,
    assignee: str | None = None,
    comment: str | None = None,
) -> dict:
    """
    Update status, assignee, or add a comment to a ticket.

    Returns:
        Updated ticket dict.
    """
    ticket = _JIRA_DB.get(ticket_key.upper())
    if not ticket:
        return {"error": f"Ticket '{ticket_key}' not found."}
    if status:
        ticket["status"] = status
    if assignee:
        ticket["assignee"] = assignee
    if comment:
        ticket.setdefault("comments", []).append({
            "author": "InsightForge MCP",
            "body": comment,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    ticket["updated_at"] = datetime.now(timezone.utc).isoformat()
    return ticket
