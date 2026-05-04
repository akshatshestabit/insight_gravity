"""
Role-Based Access Control (RBAC) for InsightForge API.

Roles:
  admin   — full access (all endpoints, all tenants)
  analyst — read/write research + retrieval; own tenant only
  viewer  — read-only retrieval; own tenant only

API keys are stored as environment variables or in PostgreSQL.
Pass the key via the X-API-Key header.

Usage (FastAPI dependency):
    from security.rbac import require_role
    @router.post("/research")
    async def research(req, user=Depends(require_role("analyst"))): ...
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from dataclasses import dataclass
from typing import Literal

from fastapi import Depends, Header, HTTPException, status

logger = logging.getLogger(__name__)

Role = Literal["admin", "analyst", "viewer"]

ROLE_HIERARCHY = {"admin": 3, "analyst": 2, "viewer": 1}


@dataclass
class Principal:
    api_key_id: str
    tenant_id: str
    role: Role
    email: str = ""


# ── Key store (env-based for Day 4 — swap for DB in production) ───────────────

def _load_keys() -> dict[str, Principal]:
    """
    Load API keys from environment variables.

    Format:
        INSIGHTFORGE_KEY_<ID>=<hashed_key>:<tenant_id>:<role>:<email>

    Generate a key hash:
        python -c "import hashlib; print(hashlib.sha256(b'my-secret-key').hexdigest())"
    """
    keys: dict[str, Principal] = {}

    # Built-in dev keys (disable in production via DISABLE_DEV_KEYS=true)
    if os.getenv("DISABLE_DEV_KEYS", "false").lower() != "true":
        keys[_hash("dev-admin-key")] = Principal("dev-admin",  "default", "admin",   "admin@insightforge.ai")
        keys[_hash("dev-analyst-key")] = Principal("dev-analyst", "default", "analyst", "analyst@insightforge.ai")
        keys[_hash("dev-viewer-key")]  = Principal("dev-viewer",  "default", "viewer",  "viewer@insightforge.ai")

    # Production keys from env
    for key, val in os.environ.items():
        if key.startswith("INSIGHTFORGE_KEY_"):
            try:
                key_hash, tenant_id, role, email = val.split(":", 3)
                key_id = key.removeprefix("INSIGHTFORGE_KEY_").lower()
                keys[key_hash] = Principal(key_id, tenant_id, role, email)  # type: ignore
            except ValueError:
                logger.warning("Malformed key spec in %s — expected hash:tenant:role:email", key)

    return keys


_KEY_STORE: dict[str, Principal] | None = None


def _get_store() -> dict[str, Principal]:
    global _KEY_STORE
    if _KEY_STORE is None:
        _KEY_STORE = _load_keys()
    return _KEY_STORE


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


# ── FastAPI dependencies ───────────────────────────────────────────────────────

async def _authenticate(x_api_key: str = Header(default="")) -> Principal:
    """Resolve an API key to a Principal. Raises 401 if invalid."""
    if not x_api_key:
        # Allow unauthenticated in dev mode
        if os.getenv("INSIGHTFORGE_AUTH_REQUIRED", "false").lower() != "true":
            return Principal("anonymous", "default", "analyst")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="X-API-Key header required.")

    store = _get_store()
    principal = store.get(_hash(x_api_key))
    if not principal:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key.")

    logger.debug("Authenticated: tenant=%s role=%s", principal.tenant_id, principal.role)
    return principal


def require_role(min_role: Role):
    """FastAPI dependency factory — requires at least *min_role*."""
    async def _dep(principal: Principal = Depends(_authenticate)) -> Principal:
        if ROLE_HIERARCHY.get(principal.role, 0) < ROLE_HIERARCHY[min_role]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{principal.role}' insufficient. Required: '{min_role}'.",
            )
        return principal
    return _dep


# Convenience shortcuts
require_admin   = require_role("admin")
require_analyst = require_role("analyst")
require_viewer  = require_role("viewer")
