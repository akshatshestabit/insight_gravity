"""
CRM lookup tool for the InsightForge MCP server.

In production replace the stub data store with your actual CRM API calls
(Salesforce, HubSpot, etc.).  The interface is intentionally provider-agnostic.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

# Stub data — replace with real CRM API client in production
_CRM_DB: dict[str, dict] = {
    "CUST-001": {
        "id": "CUST-001",
        "name": "Acme Corp",
        "email": "contact@acme.com",
        "phone": "+1-555-0101",
        "tier": "Enterprise",
        "arr_usd": 240_000,
        "csm": "Jane Smith",
        "health_score": 87,
        "last_activity": "2026-04-25",
        "open_opportunities": 2,
        "tags": ["fintech", "data-platform"],
    },
    "CUST-002": {
        "id": "CUST-002",
        "name": "Beta Dynamics",
        "email": "info@betadyn.io",
        "phone": "+1-555-0202",
        "tier": "Pro",
        "arr_usd": 48_000,
        "csm": "Carlos Rivera",
        "health_score": 62,
        "last_activity": "2026-04-10",
        "open_opportunities": 1,
        "tags": ["saas", "hr-tech"],
    },
    "CUST-003": {
        "id": "CUST-003",
        "name": "Gamma Systems",
        "email": "bd@gammasys.com",
        "phone": "+1-555-0303",
        "tier": "Starter",
        "arr_usd": 12_000,
        "csm": "Priya Nair",
        "health_score": 91,
        "last_activity": "2026-04-28",
        "open_opportunities": 0,
        "tags": ["iot", "manufacturing"],
    },
}


def crm_lookup(customer_id: str) -> dict:
    """
    Retrieve customer information from the CRM by customer ID.

    Args:
        customer_id: The unique customer identifier (e.g. "CUST-001").

    Returns:
        Customer record dict, or an error dict if not found.
    """
    record = _CRM_DB.get(customer_id.upper())
    if not record:
        return {
            "error": f"Customer '{customer_id}' not found.",
            "available_ids": list(_CRM_DB.keys()),
        }
    return {**record, "retrieved_at": datetime.now(timezone.utc).isoformat()}


def search_crm(name: str | None = None, tier: str | None = None) -> list[dict]:
    """
    Search the CRM by name fragment or tier.

    Args:
        name: Substring to match against customer name (case-insensitive).
        tier: Exact tier to filter by (Enterprise | Pro | Starter).

    Returns:
        List of matching customer records (id, name, tier, health_score only).
    """
    results = []
    for record in _CRM_DB.values():
        if name and name.lower() not in record["name"].lower():
            continue
        if tier and record["tier"].lower() != tier.lower():
            continue
        results.append({
            "id": record["id"],
            "name": record["name"],
            "tier": record["tier"],
            "health_score": record["health_score"],
        })
    return results
