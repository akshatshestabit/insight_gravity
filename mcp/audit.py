"""
Signed audit log for every MCP tool invocation.

Every entry is HMAC-SHA256 signed so log entries cannot be tampered with
without invalidating the signature. Entries are written to:
  1. The flat audit.log file (JSONL — one JSON object per line)
  2. The PostgreSQL audit_log table (async, best-effort)

Verification:
    python -m mcp.audit verify audit.log
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Secret must be set in production via AUDIT_SECRET_KEY env var
_SECRET = os.getenv("AUDIT_SECRET_KEY", "insightforge-dev-secret-change-me")
_AUDIT_FILE = Path(os.getenv("AUDIT_LOG_PATH", "audit.log"))

# ── Signing ───────────────────────────────────────────────────────────────────

def _sign(entry: dict) -> str:
    """Return HMAC-SHA256 hex digest of the canonicalised entry (signature excluded)."""
    canonical = json.dumps(
        {k: v for k, v in entry.items() if k != "signature"},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hmac.new(_SECRET.encode(), canonical.encode(), hashlib.sha256).hexdigest()


def verify_entry(entry: dict) -> bool:
    """Return True if the entry's signature is valid."""
    expected = _sign(entry)
    provided = entry.get("signature", "")
    return hmac.compare_digest(expected, provided)


# ── Logging ───────────────────────────────────────────────────────────────────

def log_mcp_invocation(
    *,
    tool_name: str,
    arguments: dict,
    result: object,
    session_id: str,
    server_name: str = "InsightForge MCP",
    success: bool = True,
    error: str | None = None,
) -> dict:
    """
    Synchronously append a signed audit entry to audit.log and return it.
    DB write is fire-and-forget via a background task.
    """
    entry: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "server_name": server_name,
        "tool_name": tool_name,
        "arguments": arguments,
        "result_summary": _summarise(result),
        "success": success,
        "error": error,
        "version": "1.0",
    }
    entry["signature"] = _sign(entry)

    _write_to_file(entry)

    # Best-effort async DB write (non-blocking)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_write_to_db(entry))
    except RuntimeError:
        pass

    return entry


async def log_mcp_invocation_async(
    *,
    tool_name: str,
    arguments: dict,
    result: object,
    session_id: str,
    server_name: str = "InsightForge MCP",
    success: bool = True,
    error: str | None = None,
) -> dict:
    """Async variant — awaits the DB write."""
    entry: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "server_name": server_name,
        "tool_name": tool_name,
        "arguments": arguments,
        "result_summary": _summarise(result),
        "success": success,
        "error": error,
        "version": "1.0",
    }
    entry["signature"] = _sign(entry)
    _write_to_file(entry)
    await _write_to_db(entry)
    return entry


# ── Helpers ───────────────────────────────────────────────────────────────────

def _summarise(result: object) -> str:
    try:
        s = json.dumps(result, default=str)
    except Exception:
        s = str(result)
    return s[:500] + ("…" if len(s) > 500 else "")


def _write_to_file(entry: dict) -> None:
    try:
        with _AUDIT_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
    except Exception as exc:
        logger.error("Audit file write failed: %s", exc)


async def _write_to_db(entry: dict) -> None:
    try:
        from backend.database import get_db
        from sqlalchemy import text
        async for session in get_db():
            await session.execute(
                text(
                    "INSERT INTO audit_log "
                    "(timestamp, session_id, server_name, tool_name, arguments, "
                    " result_summary, success, error, signature) "
                    "VALUES (:timestamp, :session_id, :server_name, :tool_name, "
                    "        :arguments, :result_summary, :success, :error, :signature)"
                ),
                {
                    "timestamp":      entry["timestamp"],
                    "session_id":     entry["session_id"],
                    "server_name":    entry["server_name"],
                    "tool_name":      entry["tool_name"],
                    "arguments":      json.dumps(entry["arguments"], default=str),
                    "result_summary": entry["result_summary"],
                    "success":        entry["success"],
                    "error":          entry.get("error"),
                    "signature":      entry["signature"],
                },
            )
            await session.commit()
    except Exception as exc:
        logger.debug("Audit DB write skipped: %s", exc)


# ── CLI verifier ──────────────────────────────────────────────────────────────

def verify_log(log_path: str) -> None:
    """Read audit.log and verify every entry's signature."""
    path = Path(log_path)
    if not path.exists():
        print(f"Log file not found: {log_path}")
        sys.exit(1)

    total = ok = tampered = 0
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                entry = json.loads(line)
                if verify_entry(entry):
                    ok += 1
                else:
                    tampered += 1
                    print(f"  TAMPERED  line {lineno}: {entry.get('tool_name')} @ {entry.get('timestamp')}")
            except json.JSONDecodeError as exc:
                tampered += 1
                print(f"  INVALID JSON  line {lineno}: {exc}")

    print(f"\nVerification complete: {ok}/{total} valid, {tampered} tampered/invalid.")
    sys.exit(0 if tampered == 0 else 1)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "verify":
        verify_log(sys.argv[2])
    else:
        print("Usage: python -m mcp.audit verify <audit.log>")
