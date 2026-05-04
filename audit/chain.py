"""
Hash-chained tamper-evident audit log.

Extends mcp/audit.py with a blockchain-style chain:
  entry[n].prev_hash = SHA-256(entry[n-1])
  entry[n].hash      = SHA-256(entry[n] + prev_hash)

This means ANY modification to a past entry (or any deletion) breaks
the chain and is detected by verify_chain().

Covers: every prompt, retrieval call, tool call, agent step, and response.

Usage:
    from audit.chain import AuditChain
    chain = AuditChain()
    chain.log_event("llm_call", {"prompt": "...", "model": "gemini-2.5-flash"}, {"response": "..."})
    chain.verify()   # True if chain is intact
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

CHAIN_FILE = Path(os.getenv("AUDIT_CHAIN_PATH", "audit_chain.jsonl"))


def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _entry_content(entry: dict) -> str:
    """Canonical serialization for hashing (excludes 'hash' field)."""
    return json.dumps(
        {k: v for k, v in entry.items() if k != "hash"},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


class AuditChain:
    """
    Append-only hash-chained audit log.

    Each entry contains:
      - timestamp, event_type, session_id, actor
      - input_data, output_data (truncated for storage)
      - prev_hash: SHA-256 of previous entry (all fields)
      - hash: SHA-256 of this entry (including prev_hash)
    """

    def __init__(self, chain_file: Optional[Path] = None):
        self._file = chain_file or CHAIN_FILE
        self._prev_hash = self._get_last_hash()

    def _get_last_hash(self) -> str:
        """Read the hash of the last entry in the chain (or genesis hash)."""
        genesis = _sha256("INSIGHTFORGE_GENESIS_BLOCK")
        if not self._file.exists():
            return genesis
        try:
            last_line = ""
            with self._file.open("r", encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        last_line = line.strip()
            if last_line:
                last = json.loads(last_line)
                return last.get("hash", genesis)
        except Exception:
            pass
        return genesis

    def log_event(
        self,
        event_type: str,
        input_data: Any,
        output_data: Any,
        session_id: str = "system",
        actor: str = "system",
        metadata: Optional[dict] = None,
    ) -> dict:
        """
        Append a new chained entry to the audit log.

        Args:
            event_type:  e.g. "llm_call", "retrieval", "tool_call", "agent_step",
                         "request_in", "response_out", "guardrail_block"
            input_data:  Input payload (prompt, query, arguments…)
            output_data: Output / result
            session_id:  Research session or request ID
            actor:       Which component produced the event (agent name, middleware, etc.)
            metadata:    Any extra fields

        Returns:
            The full entry dict including its chain hash.
        """
        entry: dict = {
            "timestamp":  datetime.now(timezone.utc).isoformat(),
            "seq":        self._next_seq(),
            "event_type": event_type,
            "session_id": session_id,
            "actor":      actor,
            "input":      _truncate(input_data),
            "output":     _truncate(output_data),
            "metadata":   metadata or {},
            "prev_hash":  self._prev_hash,
        }
        entry["hash"] = _sha256(_entry_content(entry))

        try:
            with self._file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, default=str) + "\n")
        except Exception as exc:
            logger.error("Audit chain write failed: %s", exc)

        self._prev_hash = entry["hash"]
        return entry

    def verify(self) -> tuple[bool, list[str]]:
        """
        Verify the integrity of the entire chain.

        Returns:
            (True, []) if chain is intact.
            (False, [error_messages]) if any tampering is detected.
        """
        if not self._file.exists():
            return True, []

        errors: list[str] = []
        prev_hash = _sha256("INSIGHTFORGE_GENESIS_BLOCK")
        total = 0

        with self._file.open("r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                total += 1
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    errors.append(f"Line {lineno}: invalid JSON")
                    continue

                # Check prev_hash link
                if entry.get("prev_hash") != prev_hash:
                    errors.append(
                        f"Line {lineno} (seq={entry.get('seq')}): "
                        f"prev_hash mismatch — chain broken!"
                    )

                # Recompute hash
                expected = _sha256(_entry_content(entry))
                if entry.get("hash") != expected:
                    errors.append(
                        f"Line {lineno} (seq={entry.get('seq')}): "
                        f"hash mismatch — entry tampered!"
                    )

                prev_hash = entry.get("hash", prev_hash)

        if errors:
            logger.error("Audit chain verification FAILED: %d errors in %d entries", len(errors), total)
        else:
            logger.info("Audit chain OK: %d entries verified", total)

        return len(errors) == 0, errors

    def _next_seq(self) -> int:
        if not self._file.exists():
            return 1
        count = 0
        with self._file.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    count += 1
        return count + 1

    def tail(self, n: int = 10) -> list[dict]:
        """Return the last *n* entries from the chain."""
        if not self._file.exists():
            return []
        lines = []
        with self._file.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    lines.append(line)
        return [json.loads(l) for l in lines[-n:]]


def _truncate(data: Any, max_chars: int = 500) -> Any:
    try:
        s = json.dumps(data, default=str)
        if len(s) > max_chars:
            return s[:max_chars] + "…"
        return data
    except Exception:
        return str(data)[:max_chars]


# ── Module-level singleton ────────────────────────────────────────────────────
audit_chain = AuditChain()


# ── Convenience log functions ─────────────────────────────────────────────────

def log_request(session_id: str, path: str, query: str) -> None:
    audit_chain.log_event("request_in", {"path": path, "query": query[:200]},
                          {}, session_id=session_id, actor="api_gateway")


def log_llm_call(session_id: str, agent: str, prompt_preview: str,
                 response_preview: str, tokens: int, cost_usd: float) -> None:
    audit_chain.log_event(
        "llm_call",
        {"agent": agent, "prompt": prompt_preview[:200]},
        {"response": response_preview[:200], "tokens": tokens, "cost_usd": cost_usd},
        session_id=session_id, actor=agent,
    )


def log_retrieval(session_id: str, query: str, modality: str, n_results: int) -> None:
    audit_chain.log_event(
        "retrieval",
        {"query": query[:200], "modality": modality},
        {"n_results": n_results},
        session_id=session_id, actor="retriever",
    )


def log_tool_call(session_id: str, tool_name: str, args: dict, result: Any) -> None:
    audit_chain.log_event(
        "tool_call",
        {"tool": tool_name, "args": args},
        result,
        session_id=session_id, actor="mcp_client",
    )


def log_guardrail(session_id: str, event: str, details: dict) -> None:
    audit_chain.log_event(
        f"guardrail:{event}", details, {},
        session_id=session_id, actor="guardrails",
    )


# ── CLI verifier ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    chain = AuditChain()
    ok, errors = chain.verify()
    if ok:
        print(f"✅ Chain OK — {len(chain.tail(9999))} entries verified.")
    else:
        print(f"❌ Chain BROKEN — {len(errors)} errors:")
        for e in errors:
            print(f"  {e}")
    sys.exit(0 if ok else 1)
