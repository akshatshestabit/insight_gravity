"""
Input and output validators for InsightForge API.

Input:
- Query length and character limits
- Injection check (delegates to injection.py)
- PII redaction (delegates to pii.py)
- Toxicity scoring (regex-based + optional LLM judge)

Output:
- PII in responses (redact before returning)
- JSON schema enforcement
- Response length sanity check
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

from guardrails.pii import redact, pii_report
from guardrails.injection import check_injection

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
MAX_QUERY_LEN  = 2000
MIN_QUERY_LEN  = 3
MAX_OUTPUT_LEN = 50_000

# Crude toxicity patterns (replace with an LLM judge or moderation API in prod)
_TOXIC_PATTERNS = [
    r"\b(?:kill|murder|bomb|attack|terrorist)\b.{0,30}\b(?:how|instructions|steps|guide)\b",
    r"\b(?:hack|exploit|breach|pwn)\b.{0,20}\b(?:system|database|server|network)\b",
    r"\b(?:synthesize|make|produce)\b.{0,20}\b(?:drug|meth|explosive|poison)\b",
]
_TOXIC_RE = [re.compile(p, re.IGNORECASE) for p in _TOXIC_PATTERNS]


@dataclass
class ValidationResult:
    valid: bool
    cleaned_text: str
    errors: list[str]
    warnings: list[str]
    pii_detected: bool = False
    injection_score: float = 0.0


# ── Input validation ───────────────────────────────────────────────────────────

def validate_query(query: str, redact_pii: bool = True) -> ValidationResult:
    """
    Full input validation pipeline for user queries.

    Steps:
    1. Length check
    2. Injection detection (block if score >= threshold)
    3. Toxicity check
    4. PII detection and optional redaction
    """
    errors: list[str] = []
    warnings: list[str] = []

    # 1. Length
    if not query or len(query.strip()) < MIN_QUERY_LEN:
        return ValidationResult(False, query, ["Query is too short (min 3 characters)."], [])
    if len(query) > MAX_QUERY_LEN:
        return ValidationResult(False, query, [f"Query exceeds {MAX_QUERY_LEN} characters."], [])

    cleaned = query.strip()

    # 2. Injection
    inj = check_injection(cleaned)
    if inj.blocked:
        return ValidationResult(False, cleaned, [inj.reason], [], injection_score=inj.score)
    if inj.score > 0:
        warnings.append(f"Suspicious input detected (score={inj.score:.2f}): {inj.matched_patterns}")

    # 3. Toxicity
    for pattern in _TOXIC_RE:
        if pattern.search(cleaned):
            return ValidationResult(False, cleaned, ["Query contains disallowed content."], [])

    # 4. PII (regex-only in hot path — Presidio NER is too slow for middleware)
    pii_info = pii_report(cleaned, use_presidio=False)
    pii_detected = pii_info["has_pii"]
    if pii_detected:
        warnings.append(f"PII detected in query: {pii_info['entity_types']}")
        if redact_pii:
            cleaned = redact(cleaned, use_presidio=False)

    return ValidationResult(
        valid=True,
        cleaned_text=cleaned,
        errors=errors,
        warnings=warnings,
        pii_detected=pii_detected,
        injection_score=inj.score,
    )


def validate_document_content(content: str) -> ValidationResult:
    """
    Validate document content before embedding — catches indirect injection.
    PII is NOT redacted from source docs (only from outputs).
    """
    errors: list[str] = []
    warnings: list[str] = []

    inj = check_injection(content)
    if inj.blocked:
        errors.append(f"Document contains prompt injection: {inj.matched_patterns}")
        return ValidationResult(False, content, errors, [], injection_score=inj.score)

    pii_info = pii_report(content)
    if pii_info["has_pii"]:
        warnings.append(f"Document contains PII ({pii_info['entity_types']}) — consider anonymizing before indexing.")

    return ValidationResult(True, content, errors, warnings,
                            pii_detected=pii_info["has_pii"], injection_score=inj.score)


# ── Output validation ──────────────────────────────────────────────────────────

def validate_output(text: str, redact_pii_in_output: bool = True) -> ValidationResult:
    """
    Sanitize LLM output before returning to the client.

    - Redacts any PII the model may have reproduced from source docs.
    - Checks output length.
    """
    errors: list[str] = []
    warnings: list[str] = []
    cleaned = text

    if len(text) > MAX_OUTPUT_LEN:
        warnings.append(f"Output truncated from {len(text)} to {MAX_OUTPUT_LEN} characters.")
        cleaned = text[:MAX_OUTPUT_LEN]

    pii_info = pii_report(cleaned, use_presidio=False)
    pii_detected = pii_info["has_pii"]
    if pii_detected and redact_pii_in_output:
        warnings.append(f"PII redacted from output: {pii_info['entity_types']}")
        cleaned = redact(cleaned, use_presidio=False)

    return ValidationResult(
        valid=True,
        cleaned_text=cleaned,
        errors=errors,
        warnings=warnings,
        pii_detected=pii_detected,
    )


def validate_json_schema(data: Any, schema: dict) -> tuple[bool, list[str]]:
    """
    Validate *data* against a JSON schema using jsonschema.
    Returns (valid, list_of_errors).
    """
    try:
        import jsonschema
        jsonschema.validate(instance=data, schema=schema)
        return True, []
    except ImportError:
        return True, ["jsonschema not installed — schema validation skipped"]
    except jsonschema.ValidationError as e:
        return False, [e.message]
