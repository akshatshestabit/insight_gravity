"""
Semantic caching layer — sits in front of every LLM + retrieval call.

Strategy:
  1. Embed the incoming query using the same model as retrieval
  2. Scan Redis for cached responses whose query embedding is within
     cosine-similarity threshold (default 0.92)
  3. Cache hit  → return cached response instantly (0 LLM cost)
  4. Cache miss → run pipeline, store result in Redis with TTL

Target: 30%+ cost reduction on repeated / semantically similar queries.

Usage:
    from retrieval.semantic_cache import SemanticCache
    cache = SemanticCache()
    hit = await cache.get("What is InsightForge revenue?")
    if hit:
        return hit
    result = await expensive_pipeline(query)
    await cache.set(query, result)
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import time
from typing import Any, Optional

import redis.asyncio as aioredis

from backend.config import settings

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS  = int(os.getenv("SEMANTIC_CACHE_TTL",  "3600"))   # 1 h
SIM_THRESHOLD      = float(os.getenv("SEMANTIC_CACHE_SIM", "0.92"))   # cosine ≥ 0.92 = hit
MAX_CACHE_ENTRIES  = int(os.getenv("SEMANTIC_CACHE_MAX",   "10000"))
CACHE_KEY_PREFIX   = "insightforge:scache:"
EMBEDDING_KEY_PFX  = "insightforge:semb:"


def _cosine(a: list[float], b: list[float]) -> float:
    dot   = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _query_hash(query: str) -> str:
    return hashlib.sha256(query.strip().lower().encode()).hexdigest()[:16]


class SemanticCache:
    """
    Redis-backed semantic cache for LLM responses.

    Storage layout (per cached entry):
      insightforge:scache:<hash>  →  JSON {query, embedding, response, ts, hit_count}
    An index key stores all entry hashes for similarity scan:
      insightforge:scache:index   →  JSON list of hashes
    """

    def __init__(self):
        self._redis: Optional[aioredis.Redis] = None
        self._embedder = None
        self.hits   = 0
        self.misses = 0

    async def _client(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        return self._redis

    async def _embed(self, text: str) -> list[float]:
        """Embed query text using the same model as retrieval."""
        if self._embedder is None:
            from retrieval.embedder import embed_text
            self._embedder = embed_text
        try:
            return self._embedder(text)
        except Exception as exc:
            logger.warning("Semantic cache embedding failed: %s", exc)
            return []

    # ── Public API ─────────────────────────────────────────────────────────────

    async def get(self, query: str) -> Optional[dict]:
        """
        Return cached response if a semantically similar query was cached.
        Returns None on miss (caller should run the pipeline and call set()).
        """
        t0 = time.perf_counter()
        try:
            r = await self._client()
            index_raw = await r.get(f"{CACHE_KEY_PREFIX}index")
            if not index_raw:
                self.misses += 1
                return None

            hashes = json.loads(index_raw)
            if not hashes:
                self.misses += 1
                return None

            query_emb = await self._embed(query)
            if not query_emb:
                self.misses += 1
                return None

            best_sim   = 0.0
            best_entry = None

            for h in hashes:
                raw = await r.get(f"{CACHE_KEY_PREFIX}{h}")
                if not raw:
                    continue
                entry = json.loads(raw)
                sim = _cosine(query_emb, entry["embedding"])
                if sim > best_sim:
                    best_sim   = sim
                    best_entry = entry

            if best_sim >= SIM_THRESHOLD and best_entry:
                # Cache hit — update hit_count and return
                best_entry["hit_count"] = best_entry.get("hit_count", 0) + 1
                await r.set(
                    f"{CACHE_KEY_PREFIX}{_query_hash(best_entry['query'])}",
                    json.dumps(best_entry),
                    ex=CACHE_TTL_SECONDS,
                )
                self.hits += 1
                elapsed = round((time.perf_counter() - t0) * 1000, 1)
                logger.info("Semantic cache HIT  sim=%.3f  %dms  query=%s",
                            best_sim, elapsed, query[:60])
                return {**best_entry["response"], "_cache_hit": True, "_similarity": best_sim}

            self.misses += 1
            return None

        except Exception as exc:
            logger.warning("Semantic cache get error: %s", exc)
            self.misses += 1
            return None

    async def set(self, query: str, response: dict, ttl: int = CACHE_TTL_SECONDS) -> bool:
        """Store a query-response pair in the semantic cache."""
        try:
            r = await self._client()
            embedding = await self._embed(query)
            if not embedding:
                return False

            h = _query_hash(query)
            entry = {
                "query":     query,
                "embedding": embedding,
                "response":  response,
                "ts":        time.time(),
                "hit_count": 0,
            }
            await r.set(f"{CACHE_KEY_PREFIX}{h}", json.dumps(entry), ex=ttl)

            # Update index
            index_raw = await r.get(f"{CACHE_KEY_PREFIX}index")
            hashes = json.loads(index_raw) if index_raw else []
            if h not in hashes:
                hashes.append(h)
                if len(hashes) > MAX_CACHE_ENTRIES:
                    hashes = hashes[-MAX_CACHE_ENTRIES:]
                await r.set(f"{CACHE_KEY_PREFIX}index", json.dumps(hashes), ex=ttl * 2)

            logger.info("Semantic cache SET  query=%s", query[:60])
            return True

        except Exception as exc:
            logger.warning("Semantic cache set error: %s", exc)
            return False

    async def invalidate(self, query: str | None = None) -> int:
        """Invalidate a specific query (or flush entire cache if None)."""
        try:
            r = await self._client()
            if query is None:
                # Flush all cache keys
                index_raw = await r.get(f"{CACHE_KEY_PREFIX}index")
                hashes = json.loads(index_raw) if index_raw else []
                for h in hashes:
                    await r.delete(f"{CACHE_KEY_PREFIX}{h}")
                await r.delete(f"{CACHE_KEY_PREFIX}index")
                return len(hashes)
            else:
                h = _query_hash(query)
                await r.delete(f"{CACHE_KEY_PREFIX}{h}")
                return 1
        except Exception as exc:
            logger.warning("Semantic cache invalidate error: %s", exc)
            return 0

    async def stats(self) -> dict:
        """Return cache statistics."""
        try:
            r = await self._client()
            index_raw = await r.get(f"{CACHE_KEY_PREFIX}index")
            hashes = json.loads(index_raw) if index_raw else []
            total = self.hits + self.misses
            return {
                "entries":    len(hashes),
                "hits":       self.hits,
                "misses":     self.misses,
                "hit_rate":   round(self.hits / total, 3) if total else 0.0,
                "threshold":  SIM_THRESHOLD,
                "ttl_s":      CACHE_TTL_SECONDS,
            }
        except Exception:
            return {"entries": 0, "hits": self.hits, "misses": self.misses}


# ── Module-level singleton ────────────────────────────────────────────────────
_cache: Optional[SemanticCache] = None


def get_semantic_cache() -> SemanticCache:
    global _cache
    if _cache is None:
        _cache = SemanticCache()
    return _cache
