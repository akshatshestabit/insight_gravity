"""
PII detection and redaction using Microsoft Presidio + regex fallback.

Detects and redacts: email, phone, SSN, credit card, IP address,
person names, US addresses, dates of birth.

Usage:
    from guardrails.pii import redact, detect_pii
    clean = redact("My email is john@example.com")  # → "My email is <EMAIL>"
    hits  = detect_pii("SSN: 123-45-6789")          # → [PIIHit(...)]
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# ── Regex patterns (fast, no model needed) ────────────────────────────────────
_PATTERNS = [
    ("EMAIL",       r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    ("PHONE",       r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
    ("SSN",         r"\b\d{3}-\d{2}-\d{4}\b"),
    ("CREDIT_CARD", r"\b(?:\d[ -]?){13,16}\b"),
    ("IP_ADDRESS",  r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    ("DATE_OF_BIRTH", r"\b(?:dob|date of birth|born)[:\s]+\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\b"),
    ("IBAN",        r"\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}(?:[A-Z0-9]?){0,16}\b"),
    ("PASSWORD",    r"\b(?:password|passwd|pwd)[:\s=]+\S+"),
]
_COMPILED = [(label, re.compile(pat, re.IGNORECASE)) for label, pat in _PATTERNS]


@dataclass
class PIIHit:
    entity_type: str
    text: str
    start: int
    end: int
    score: float = 1.0


def detect_pii(text: str, use_presidio: bool = True) -> list[PIIHit]:
    """Return all PII hits found in *text*."""
    hits: list[PIIHit] = []

    # 1. Fast regex scan
    for label, pattern in _COMPILED:
        for m in pattern.finditer(text):
            hits.append(PIIHit(entity_type=label, text=m.group(), start=m.start(), end=m.end()))

    # 2. Presidio NER (names, locations, orgs) — optional
    if use_presidio:
        try:
            from presidio_analyzer import AnalyzerEngine
            _engine = AnalyzerEngine()
            results = _engine.analyze(text=text, language="en",
                                       entities=["PERSON", "LOCATION", "ORGANIZATION"])
            for r in results:
                # avoid double-reporting regex hits
                if not any(h.start == r.start and h.end == r.end for h in hits):
                    hits.append(PIIHit(
                        entity_type=r.entity_type,
                        text=text[r.start:r.end],
                        start=r.start, end=r.end,
                        score=r.score,
                    ))
        except Exception as exc:
            logger.debug("Presidio scan skipped: %s", exc)

    # Sort by position
    hits.sort(key=lambda h: h.start)
    return hits


def redact(text: str, replacement_map: Optional[dict[str, str]] = None, use_presidio: bool = True) -> str:
    """
    Replace all detected PII with placeholder tokens.

    Args:
        text:            Input string.
        replacement_map: Override labels, e.g. {"EMAIL": "[REDACTED_EMAIL]"}.
        use_presidio:    Enable Presidio NER for name/location detection.

    Returns:
        Redacted string.
    """
    defaults = {
        "EMAIL": "<EMAIL>", "PHONE": "<PHONE>", "SSN": "<SSN>",
        "CREDIT_CARD": "<CREDIT_CARD>", "IP_ADDRESS": "<IP>",
        "DATE_OF_BIRTH": "<DOB>", "IBAN": "<IBAN>", "PASSWORD": "<PASSWORD>",
        "PERSON": "<PERSON>", "LOCATION": "<LOCATION>", "ORGANIZATION": "<ORG>",
    }
    if replacement_map:
        defaults.update(replacement_map)

    hits = detect_pii(text, use_presidio=use_presidio)
    if not hits:
        return text

    # Build redacted string (iterate backwards to preserve indices)
    result = list(text)
    for hit in sorted(hits, key=lambda h: h.start, reverse=True):
        placeholder = defaults.get(hit.entity_type, "<REDACTED>")
        result[hit.start:hit.end] = list(placeholder)

    return "".join(result)


def has_pii(text: str) -> bool:
    """Quick check — returns True if any PII is detected."""
    return bool(detect_pii(text, use_presidio=False))


def pii_report(text: str, use_presidio: bool = False) -> dict:
    """Return a summary dict suitable for logging/audit."""
    hits = detect_pii(text, use_presidio=use_presidio)
    return {
        "has_pii": bool(hits),
        "entity_types": list({h.entity_type for h in hits}),
        "count": len(hits),
    }
