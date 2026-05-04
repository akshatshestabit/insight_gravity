"""
Prompt injection and jailbreak detection.

Covers:
- Direct instruction override attempts
- Role-play / persona hijacking (DAN, etc.)
- Indirect injection via document content (critical for doc-ingesting systems)
- System prompt probing

Usage:
    from guardrails.injection import check_injection, InjectionResult
    result = check_injection("Ignore previous instructions and...")
    if result.blocked:
        raise HTTPException(400, result.reason)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class InjectionResult:
    blocked: bool
    score: float          # 0.0 = safe, 1.0 = certain injection
    reason: str
    matched_patterns: list[str] = field(default_factory=list)


# ── Pattern library ───────────────────────────────────────────────────────────
# Each entry: (label, regex, weight 0–1)
_INJECTION_PATTERNS: list[tuple[str, str, float]] = [
    # Direct override
    ("ignore_instructions",    r"\bignore\s+(?:all\s+)?(?:previous|above|prior)\s+instructions?\b", 1.0),
    ("disregard_instructions", r"\bdisregard\s+(?:all\s+)?(?:previous|above|prior)\s+instructions?\b", 1.0),
    ("forget_everything",      r"\bforget\s+(?:everything|all)\b", 0.9),
    ("new_instructions",       r"\byour\s+(?:new|real)\s+instructions?\s+are\b", 1.0),
    ("override_system",        r"\boverride\s+(?:system|safety|guardrail)\b", 1.0),
    # Role / persona hijacking
    ("you_are_now",            r"\byou\s+are\s+now\s+(?!a\s+research|an?\s+AI|an?\s+assistant)\S+", 0.8),
    ("act_as",                 r"\bact\s+as\s+(?:dan|a\s+hacker|an?\s+unrestricted)\b", 0.95),
    ("dan_mode",               r"\bdan\b.{0,30}\bdo\s+anything\s+now\b", 1.0),
    ("jailbreak_tag",          r"\bjailbreak\b", 0.85),
    ("maintenance_mode",       r"\bmaintenance\s+mode\b", 0.9),
    # System prompt probing
    ("reveal_prompt",          r"\breveal\s+(?:your\s+)?(?:system\s+)?prompt\b", 0.95),
    ("print_prompt",           r"\bprint\s+(?:your\s+)?(?:system\s+)?prompt\b", 0.9),
    ("show_instructions",      r"\bshow\s+(?:me\s+)?(?:your\s+)?(?:system\s+)?instructions?\b", 0.8),
    ("output_vectors",         r"\boutput\s+(?:all\s+)?(?:stored\s+)?vectors\b", 1.0),
    # Indirect document injection markers (embedded in PDFs/text)
    ("doc_injection_marker",   r"<<<\s*SYSTEM\s*>>>|<\|system\|>|\[INST\].*\[/INST\]", 1.0),
    ("doc_ignore_above",       r"---+\s*ignore\s+(?:everything\s+)?above\s*---+", 0.95),
    ("doc_new_task",           r"new\s+task\s*:\s*(?:ignore|forget|disregard)", 0.9),
    # Sensitive data extraction
    ("dump_database",          r"\bdump\s+(?:the\s+)?(?:database|db|data)\b", 0.95),
    ("list_all_records",       r"\blist\s+all\s+(?:records|users|passwords|secrets)\b", 0.9),
    ("get_password",           r"\b(?:get|give|show|tell)\s+(?:me\s+)?(?:the\s+)?password\b", 0.95),
    # Encoding/obfuscation tricks
    ("base64_exec",            r"base64\s*(?:decode|exec|eval|run)", 0.85),
    ("hex_exec",               r"\\x[0-9a-f]{2}.{0,20}exec", 0.75),
]

_COMPILED = [
    (label, re.compile(pattern, re.IGNORECASE | re.DOTALL), weight)
    for label, pattern, weight in _INJECTION_PATTERNS
]

BLOCK_THRESHOLD = 0.75   # score >= this → block
WARN_THRESHOLD  = 0.40   # score >= this → log warning


def check_injection(text: str) -> InjectionResult:
    """
    Scan *text* for prompt injection / jailbreak patterns.

    Returns InjectionResult with blocked=True if the combined score
    exceeds BLOCK_THRESHOLD.
    """
    if not text or not text.strip():
        return InjectionResult(blocked=False, score=0.0, reason="empty input")

    matched: list[tuple[str, float]] = []
    for label, pattern, weight in _COMPILED:
        if pattern.search(text):
            matched.append((label, weight))

    if not matched:
        return InjectionResult(blocked=False, score=0.0, reason="clean")

    # Score = max single weight (not sum — avoids false positives from multiple mild hits)
    score = max(w for _, w in matched)
    labels = [l for l, _ in matched]

    if score >= BLOCK_THRESHOLD:
        reason = f"Prompt injection detected: {', '.join(labels)}"
        logger.warning("INJECTION BLOCKED — score=%.2f patterns=%s text_preview=%s",
                       score, labels, text[:100])
        return InjectionResult(blocked=True, score=score, reason=reason, matched_patterns=labels)

    if score >= WARN_THRESHOLD:
        logger.warning("INJECTION WARNING — score=%.2f patterns=%s", score, labels)

    return InjectionResult(blocked=False, score=score, reason="suspicious but allowed",
                           matched_patterns=labels)


def check_document_injection(document_text: str) -> InjectionResult:
    """
    Scan ingested document content for embedded prompt injection.
    Critical for PDF/OCR pipelines where malicious instructions can be hidden.
    """
    result = check_injection(document_text)
    if result.blocked:
        logger.error("INDIRECT DOCUMENT INJECTION DETECTED — patterns=%s", result.matched_patterns)
    return result
