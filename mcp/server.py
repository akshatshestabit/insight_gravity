"""
InsightForge Custom MCP Server.

Exposes enterprise tools via the Model Context Protocol (MCP):
  - CRM lookup
  - Jira ticket management
  - Slack messaging
  - Email drafting & sending

Transport: stdio (default) — can also be run via HTTP/SSE.

Usage:
    # As a stdio server (for Claude Desktop / other MCP clients)
    python -m mcp.server

    # From another process via mcp.client.MCPClient
    client = MCPClient(); await client.connect()
"""
from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolResult,
    ListToolsResult,
    TextContent,
    Tool,
)

from mcp.audit import log_mcp_invocation
from mcp.tools.crm import crm_lookup, search_crm
from mcp.tools.jira import (
    create_jira_ticket,
    get_jira_ticket,
    list_jira_tickets,
    update_jira_ticket,
)
from mcp.tools.slack import post_to_slack, list_slack_channels
from mcp.tools.email_tool import draft_email, send_email, list_drafts

logger = logging.getLogger(__name__)

SERVER_NAME    = "InsightForge Enterprise MCP"
SERVER_VERSION = "1.0.0"

# ── Tool schema registry ───────────────────────────────────────────────────────

_TOOLS: list[Tool] = [
    Tool(
        name="crm_lookup",
        description="Look up a customer record in the CRM by customer ID.",
        inputSchema={
            "type": "object",
            "properties": {
                "customer_id": {"type": "string", "description": "Customer ID, e.g. CUST-001"},
            },
            "required": ["customer_id"],
        },
    ),
    Tool(
        name="crm_search",
        description="Search CRM customers by name fragment or tier.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name substring to match"},
                "tier": {"type": "string", "enum": ["Enterprise", "Pro", "Starter"]},
            },
        },
    ),
    Tool(
        name="create_jira_ticket",
        description="Create a new Jira issue/ticket.",
        inputSchema={
            "type": "object",
            "properties": {
                "summary":     {"type": "string", "description": "Short ticket title"},
                "description": {"type": "string", "description": "Full description"},
                "priority":    {"type": "string", "enum": ["Highest", "High", "Medium", "Low", "Lowest"], "default": "Medium"},
                "issue_type":  {"type": "string", "enum": ["Task", "Bug", "Story", "Epic"], "default": "Task"},
                "assignee":    {"type": "string"},
                "labels":      {"type": "array", "items": {"type": "string"}},
            },
            "required": ["summary", "description"],
        },
    ),
    Tool(
        name="get_jira_ticket",
        description="Fetch a Jira ticket by its key (e.g. INS-1001).",
        inputSchema={
            "type": "object",
            "properties": {
                "ticket_key": {"type": "string", "description": "Jira ticket key"},
            },
            "required": ["ticket_key"],
        },
    ),
    Tool(
        name="list_jira_tickets",
        description="List Jira tickets with optional status/assignee filters.",
        inputSchema={
            "type": "object",
            "properties": {
                "status":   {"type": "string", "description": "Filter by status"},
                "assignee": {"type": "string", "description": "Filter by assignee"},
                "limit":    {"type": "integer", "default": 20},
            },
        },
    ),
    Tool(
        name="update_jira_ticket",
        description="Update a Jira ticket status, assignee, or add a comment.",
        inputSchema={
            "type": "object",
            "properties": {
                "ticket_key": {"type": "string"},
                "status":     {"type": "string"},
                "assignee":   {"type": "string"},
                "comment":    {"type": "string"},
            },
            "required": ["ticket_key"],
        },
    ),
    Tool(
        name="post_to_slack",
        description="Post a message to a Slack channel.",
        inputSchema={
            "type": "object",
            "properties": {
                "channel":    {"type": "string", "description": "Channel name, e.g. #engineering"},
                "message":    {"type": "string", "description": "Message text (Slack markdown)"},
                "username":   {"type": "string", "default": "InsightForge Bot"},
                "icon_emoji": {"type": "string", "default": ":robot_face:"},
                "thread_ts":  {"type": "string", "description": "Reply to this thread"},
            },
            "required": ["channel", "message"],
        },
    ),
    Tool(
        name="list_slack_channels",
        description="List Slack channels the bot can post to.",
        inputSchema={
            "type": "object",
            "properties": {
                "include_private": {"type": "boolean", "default": False},
            },
        },
    ),
    Tool(
        name="draft_email",
        description="Create an email draft for review before sending.",
        inputSchema={
            "type": "object",
            "properties": {
                "to":       {"type": "string", "description": "Recipient email address"},
                "subject":  {"type": "string"},
                "body":     {"type": "string", "description": "Email body"},
                "cc":       {"type": "string"},
                "reply_to": {"type": "string"},
                "format":   {"type": "string", "enum": ["text", "html"], "default": "text"},
            },
            "required": ["to", "subject", "body"],
        },
    ),
    Tool(
        name="send_email",
        description="Send a previously drafted email by draft_id.",
        inputSchema={
            "type": "object",
            "properties": {
                "draft_id": {"type": "string"},
            },
            "required": ["draft_id"],
        },
    ),
]


# ── Server instance ───────────────────────────────────────────────────────────

server = Server(SERVER_NAME)


@server.list_tools()
async def list_tools() -> list[Tool]:
    return _TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Route a tool call to the appropriate implementation and log it."""
    session_id = arguments.pop("__session_id__", "unknown")
    result: Any
    success = True
    error_msg: str | None = None

    try:
        result = _dispatch(name, arguments)
    except Exception as exc:
        logger.error("Tool '%s' raised: %s", name, exc)
        result = {"error": str(exc)}
        success = False
        error_msg = str(exc)

    # Signed audit log — every invocation, including failures
    log_mcp_invocation(
        tool_name=name,
        arguments=arguments,
        result=result,
        session_id=session_id,
        server_name=SERVER_NAME,
        success=success,
        error=error_msg,
    )

    return [TextContent(type="text", text=json.dumps(result, default=str))]


def _dispatch(name: str, args: dict) -> Any:
    match name:
        case "crm_lookup":
            return crm_lookup(args["customer_id"])
        case "crm_search":
            return search_crm(name=args.get("name"), tier=args.get("tier"))
        case "create_jira_ticket":
            return create_jira_ticket(
                summary=args["summary"],
                description=args["description"],
                priority=args.get("priority", "Medium"),
                issue_type=args.get("issue_type", "Task"),
                assignee=args.get("assignee"),
                labels=args.get("labels"),
            )
        case "get_jira_ticket":
            return get_jira_ticket(args["ticket_key"])
        case "list_jira_tickets":
            return list_jira_tickets(
                status=args.get("status"),
                assignee=args.get("assignee"),
                limit=args.get("limit", 20),
            )
        case "update_jira_ticket":
            return update_jira_ticket(
                ticket_key=args["ticket_key"],
                status=args.get("status"),
                assignee=args.get("assignee"),
                comment=args.get("comment"),
            )
        case "post_to_slack":
            return post_to_slack(
                channel=args["channel"],
                message=args["message"],
                username=args.get("username", "InsightForge Bot"),
                icon_emoji=args.get("icon_emoji", ":robot_face:"),
                thread_ts=args.get("thread_ts"),
            )
        case "list_slack_channels":
            return list_slack_channels(include_private=args.get("include_private", False))
        case "draft_email":
            return draft_email(
                to=args["to"],
                subject=args["subject"],
                body=args["body"],
                cc=args.get("cc"),
                reply_to=args.get("reply_to"),
                format=args.get("format", "text"),
            )
        case "send_email":
            return send_email(args["draft_id"])
        case _:
            raise ValueError(f"Unknown tool: {name}")


# ── Entry point ───────────────────────────────────────────────────────────────

async def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
