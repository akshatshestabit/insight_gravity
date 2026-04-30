"""
Email drafting and sending tool for the InsightForge MCP server.

Stub implementation — connect to SMTP or SendGrid / AWS SES in production.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Literal

_DRAFTS: dict[str, dict] = {}
_SENT: list[dict] = []


def draft_email(
    to: str | list[str],
    subject: str,
    body: str,
    cc: str | list[str] | None = None,
    bcc: str | list[str] | None = None,
    reply_to: str | None = None,
    format: Literal["text", "html"] = "text",
) -> dict:
    """
    Create an email draft.

    Args:
        to:       Recipient address or list of addresses.
        subject:  Email subject line.
        body:     Email body (plain text or HTML depending on `format`).
        cc:       Carbon copy recipients.
        bcc:      Blind carbon copy recipients.
        reply_to: Reply-to address.
        format:   "text" or "html".

    Returns:
        Draft dict with a draft_id that can be used to send later.
    """
    draft_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    draft = {
        "draft_id": draft_id,
        "status": "draft",
        "to": [to] if isinstance(to, str) else to,
        "cc": ([cc] if isinstance(cc, str) else cc) or [],
        "bcc": ([bcc] if isinstance(bcc, str) else bcc) or [],
        "reply_to": reply_to,
        "subject": subject,
        "body": body,
        "format": format,
        "created_at": now,
        "from": os.getenv("EMAIL_FROM_ADDRESS", "noreply@insightforge.ai"),
    }
    _DRAFTS[draft_id] = draft
    return draft


def send_email(draft_id: str) -> dict:
    """
    Send a previously drafted email.

    Args:
        draft_id: The draft_id returned by draft_email.

    Returns:
        Send receipt dict with message_id and timestamp.
    """
    draft = _DRAFTS.get(draft_id)
    if not draft:
        return {"error": f"Draft '{draft_id}' not found."}

    message_id = f"<{uuid.uuid4()}@insightforge.ai>"
    now = datetime.now(timezone.utc).isoformat()

    sent = {
        **draft,
        "status": "sent",
        "message_id": message_id,
        "sent_at": now,
    }
    _SENT.append(sent)
    del _DRAFTS[draft_id]

    # In production: call SMTP / SendGrid / SES here
    # e.g. import smtplib; server.sendmail(...)

    return {
        "ok": True,
        "message_id": message_id,
        "to": sent["to"],
        "subject": sent["subject"],
        "sent_at": sent["sent_at"],
    }


def list_drafts() -> list[dict]:
    """List all pending email drafts."""
    return [
        {"draft_id": d["draft_id"], "to": d["to"], "subject": d["subject"], "created_at": d["created_at"]}
        for d in _DRAFTS.values()
    ]
