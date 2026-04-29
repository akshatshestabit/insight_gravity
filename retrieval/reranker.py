"""
Reciprocal Rank Fusion (RRF) for combining dense and BM25 results.

RRF score = Σ 1 / (k + rank_i)    k=60 (standard constant)
Higher combined score = more relevant result.
"""
from typing import Any, Dict, List


def reciprocal_rank_fusion(
    *hit_lists: List[Dict[str, Any]],
    k: int = 60,
    top_k: int = 10,
) -> List[Dict[str, Any]]:
    """
    Merge multiple ranked lists using RRF.
    Deduplicates on first 200 chars of 'content'.
    """
    scores: Dict[str, float] = {}
    docs: Dict[str, Dict] = {}

    for hit_list in hit_lists:
        for rank, hit in enumerate(hit_list):
            key = hit.get("content", "")[:200]
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            if key not in docs:
                docs[key] = hit

    sorted_keys = sorted(scores, key=lambda x: scores[x], reverse=True)
    results = []
    for key in sorted_keys[:top_k]:
        doc = dict(docs[key])
        doc["score"] = round(scores[key], 6)
        results.append(doc)
    return results
