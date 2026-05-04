"""
Tenant isolation middleware — scopes Qdrant searches and DB queries
to the authenticated principal's tenant_id.

In Qdrant, tenant isolation works via payload filters on a `tenant_id` field.
All ingested chunks should include tenant_id in their payload.

This module:
1. Provides a FastAPI middleware that injects the tenant scope into requests.
2. Provides a helper to build Qdrant tenant filters.
3. Provides a DB row-level-security helper for PostgreSQL queries.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# Special sentinel — admin users can cross tenants
GLOBAL_TENANT = "*"


# ── Qdrant tenant filter builder ──────────────────────────────────────────────

def qdrant_tenant_filter(tenant_id: str) -> Optional[object]:
    """
    Build a Qdrant Filter that restricts search to *tenant_id*.
    Returns None for global (admin) access.

    Usage:
        flt = qdrant_tenant_filter(principal.tenant_id)
        client.search(..., query_filter=flt)
    """
    if tenant_id == GLOBAL_TENANT:
        return None
    try:
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        return Filter(must=[FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))])
    except ImportError:
        return None


# ── SQLAlchemy row-level security helper ──────────────────────────────────────

def tenant_where_clause(tenant_id: str, column: str = "tenant_id") -> str:
    """
    Return a SQL WHERE fragment for tenant filtering.
    Returns empty string for global access (admin).

    Usage:
        clause = tenant_where_clause(principal.tenant_id)
        query = f"SELECT * FROM research_sessions {('WHERE ' + clause) if clause else ''}"
    """
    if tenant_id == GLOBAL_TENANT:
        return ""
    # Use parameterised value via SQLAlchemy text() in the caller
    return f"{column} = :tenant_id"


# ── Request-level tenant injection middleware ─────────────────────────────────

class TenantMiddleware(BaseHTTPMiddleware):
    """
    Reads the authenticated principal from request.state (set by RBAC)
    and injects tenant_id into request.state for downstream use.
    """

    async def dispatch(self, request: Request, call_next):
        principal = getattr(request.state, "principal", None)
        if principal:
            request.state.tenant_id = principal.tenant_id
        else:
            request.state.tenant_id = "default"
        return await call_next(request)
