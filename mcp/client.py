"""
InsightForge MCP Client.

Connects to MCP servers (custom or public) and exposes their tools as
LangChain @tool-compatible callables that can be dropped into any
LangGraph agent.

Supported transports:
  - stdio  — spawn an MCP server subprocess
  - sse    — connect to an HTTP/SSE server

Usage:
    async with MCPClient.from_stdio(["python", "-m", "mcp.server"]) as client:
        tools = await client.get_langchain_tools(session_id="abc-123")
        # tools is a list of @tool functions ready for LangGraph/LangChain
"""
from __future__ import annotations

import json
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from langchain_core.tools import StructuredTool

from mcp.audit import log_mcp_invocation_async

logger = logging.getLogger(__name__)


class MCPClient:
    """
    Thin async wrapper around the MCP Python SDK client.

    Handles:
    - Tool discovery (list_tools)
    - Tool invocation with signed audit logging
    - Permission scoping (allowlist / denylist)
    """

    def __init__(
        self,
        session,                        # mcp.ClientSession
        allowed_tools: list[str] | None = None,
        denied_tools:  list[str] | None = None,
        server_name:   str = "MCP Server",
    ):
        self._session     = session
        self._allowed     = set(allowed_tools) if allowed_tools else None
        self._denied      = set(denied_tools  or [])
        self._server_name = server_name

    # ── Factory constructors ──────────────────────────────────────────────

    @classmethod
    @asynccontextmanager
    async def from_stdio(
        cls,
        command: list[str],
        *,
        allowed_tools: list[str] | None = None,
        denied_tools:  list[str] | None = None,
        server_name:   str = "MCP Server",
    ) -> AsyncIterator["MCPClient"]:
        """
        Spawn an MCP server subprocess and connect via stdio.

        Example:
            async with MCPClient.from_stdio(["python", "-m", "mcp.server"]) as client:
                tools = await client.get_langchain_tools()
        """
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            params = StdioServerParameters(command=command[0], args=command[1:])
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield cls(session, allowed_tools, denied_tools, server_name)
        except ImportError:
            logger.error("mcp package not installed — run: pip install mcp")
            raise

    @classmethod
    @asynccontextmanager
    async def from_sse(
        cls,
        url: str,
        *,
        allowed_tools: list[str] | None = None,
        denied_tools:  list[str] | None = None,
        server_name:   str = "MCP Server",
    ) -> AsyncIterator["MCPClient"]:
        """
        Connect to an MCP server via HTTP/SSE transport.

        Example:
            async with MCPClient.from_sse("http://localhost:8001/sse") as client:
                tools = await client.get_langchain_tools()
        """
        try:
            from mcp import ClientSession
            from mcp.client.sse import sse_client

            async with sse_client(url) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield cls(session, allowed_tools, denied_tools, server_name)
        except ImportError:
            logger.error("mcp package not installed — run: pip install mcp")
            raise

    # ── Core methods ──────────────────────────────────────────────────────

    async def list_tools(self) -> list[dict]:
        """Return all tools exposed by the server, filtered by permissions."""
        response = await self._session.list_tools()
        tools = []
        for tool in response.tools:
            if self._allowed is not None and tool.name not in self._allowed:
                continue
            if tool.name in self._denied:
                continue
            tools.append({"name": tool.name, "description": tool.description, "schema": tool.inputSchema})
        return tools

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        session_id: str | None = None,
    ) -> Any:
        """
        Call a remote MCP tool.  Checks permissions and emits an audit entry.
        """
        session_id = session_id or str(uuid.uuid4())

        # Permission check
        if self._allowed is not None and tool_name not in self._allowed:
            raise PermissionError(f"Tool '{tool_name}' not in allowed list.")
        if tool_name in self._denied:
            raise PermissionError(f"Tool '{tool_name}' is explicitly denied.")

        success = True
        error_msg: str | None = None
        result: Any = None

        try:
            response = await self._session.call_tool(tool_name, arguments)
            # Extract text content from the response
            texts = [c.text for c in response.content if hasattr(c, "text")]
            raw = texts[0] if texts else "{}"
            try:
                result = json.loads(raw)
            except json.JSONDecodeError:
                result = {"text": raw}
        except Exception as exc:
            success = False
            error_msg = str(exc)
            result = {"error": str(exc)}
            logger.error("MCP tool '%s' failed: %s", tool_name, exc)
            raise

        finally:
            await log_mcp_invocation_async(
                tool_name=tool_name,
                arguments=arguments,
                result=result,
                session_id=session_id,
                server_name=self._server_name,
                success=success,
                error=error_msg,
            )

        return result

    async def get_langchain_tools(
        self,
        session_id: str | None = None,
    ) -> list[StructuredTool]:
        """
        Return a list of LangChain StructuredTool objects ready for use
        in create_react_agent() or any LangChain agent.

        Each tool automatically:
        - Validates arguments against the MCP schema
        - Injects session_id for audit logging
        - Writes a signed audit entry on every call
        """
        session_id = session_id or str(uuid.uuid4())
        raw_tools = await self.list_tools()
        lc_tools = []

        for tool_def in raw_tools:
            name        = tool_def["name"]
            description = tool_def.get("description", "")
            schema      = tool_def.get("schema", {})

            # Capture loop variables for the closure
            _name = name
            _sid  = session_id
            _self = self

            async def _run(**kwargs: Any) -> str:
                result = await _self.call_tool(_name, kwargs, session_id=_sid)
                return json.dumps(result, default=str)

            lc_tools.append(
                StructuredTool.from_function(
                    coroutine=_run,
                    name=_name,
                    description=description,
                    args_schema=_build_pydantic_schema(schema, _name),
                )
            )

        return lc_tools


# ── Schema helper ─────────────────────────────────────────────────────────────

def _build_pydantic_schema(json_schema: dict, tool_name: str):
    """
    Convert a JSON Schema dict into a minimal Pydantic v2 model class
    so LangChain can validate tool arguments.
    """
    from pydantic import create_model

    properties = json_schema.get("properties", {})
    required   = set(json_schema.get("required", []))

    field_definitions: dict[str, Any] = {}
    for field, spec in properties.items():
        py_type: type
        match spec.get("type"):
            case "integer":
                py_type = int
            case "number":
                py_type = float
            case "boolean":
                py_type = bool
            case "array":
                py_type = list
            case _:
                py_type = str

        if field in required:
            field_definitions[field] = (py_type, ...)
        else:
            default = spec.get("default")
            field_definitions[field] = (py_type | None, default)

    return create_model(f"{tool_name}_schema", **field_definitions)


# ── Public MCP server helpers ──────────────────────────────────────────────────

@asynccontextmanager
async def github_mcp_client(
    session_id: str | None = None,
    allowed_tools: list[str] | None = None,
):
    """
    Connect to the official GitHub MCP server (requires npx / Node.js).

    Allowed tools default to read-only GitHub operations:
    list_repos, get_repo, search_code, list_issues, get_issue.
    """
    default_allowed = allowed_tools or [
        "list_repos", "get_repo", "search_code",
        "list_issues", "get_issue", "get_pull_request",
        "list_commits", "get_file_contents",
    ]
    async with MCPClient.from_stdio(
        ["npx", "-y", "@modelcontextprotocol/server-github"],
        allowed_tools=default_allowed,
        server_name="GitHub MCP",
    ) as client:
        yield client


@asynccontextmanager
async def filesystem_mcp_client(
    root_dir: str = ".",
    session_id: str | None = None,
    allowed_tools: list[str] | None = None,
):
    """
    Connect to the official Filesystem MCP server (requires npx / Node.js).

    Scoped to read-only operations on root_dir by default.
    """
    default_allowed = allowed_tools or [
        "read_file", "list_directory", "search_files", "get_file_info",
    ]
    async with MCPClient.from_stdio(
        ["npx", "-y", "@modelcontextprotocol/server-filesystem", root_dir],
        allowed_tools=default_allowed,
        server_name="Filesystem MCP",
    ) as client:
        yield client
