"""
Unified retrieval pipeline.

Flow:
  query → route_query() → collections to search
        → expand_query() → 2 query variants
        → for each variant: dense (Qdrant) + BM25 → RRF
        → final RRF across all variants
        → crag_retry() if best score < 0.3
        → List[hit dicts with provenance]
"""
import logging
from typing import Any, Dict, List, Optional

from retrieval.embedder import embed_text
from retrieval.qdrant_store import search as qdrant_search, ensure_collections
from retrieval.bm25_index import bm25_search
from retrieval.reranker import reciprocal_rank_fusion
from retrieval.query_transform import expand_query, crag_retry
from retrieval.router import route_query

logger = logging.getLogger(__name__)

MODALITY_COLLECTIONS = {
    "text":  ["text_chunks"],
    "table": ["table_chunks"],
    "image": ["image_chunks"],
    "graph": ["text_chunks"],  # graph uses text context
    "all":   ["text_chunks", "table_chunks", "image_chunks"],
}


def retrieve(
    query: str,
    top_k: int = 5,
    job_id: Optional[str] = None,
    modality: str = "auto",
) -> List[Dict[str, Any]]:
    """
    Main retrieval entry point.
    Returns top_k results: {content, score, modality, provenance:{filename,page,bbox,job_id}}
    """
    # Ensure Qdrant collections exist
    try:
        ensure_collections()
    except Exception as e:
        logger.warning("Qdrant not ready: %s", e)
        return []

    # 1. Route
    if modality == "auto":
        modality = route_query(query)
    collections = MODALITY_COLLECTIONS.get(modality, MODALITY_COLLECTIONS["all"])

    # 2. Multi-query expansion (use original + 1 variant to limit API calls)
    queries = expand_query(query)[:2]

    all_hits: List[Dict[str, Any]] = []
    for q in queries:
        vec = embed_text(q)
        for col in collections:
            dense = qdrant_search(col, vec, top_k=top_k, job_id=job_id)
            keyword = bm25_search(q, dense, top_k=top_k)
            fused = reciprocal_rank_fusion(dense, keyword, top_k=top_k)
            all_hits.extend(fused)

    # 3. Final fusion across all queries
    final = reciprocal_rank_fusion(all_hits, top_k=top_k)

    # 4. CRAG retry if confidence is low
    def _retry(rewritten: str) -> List[Dict[str, Any]]:
        vec = embed_text(rewritten)
        hits = []
        for col in collections:
            hits.extend(qdrant_search(col, vec, top_k=top_k, job_id=job_id))
        return reciprocal_rank_fusion(hits, top_k=top_k)

    final = crag_retry(query, final, _retry, threshold=0.3)

    return final[:top_k]
