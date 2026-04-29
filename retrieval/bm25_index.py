"""
BM25 keyword search over a list of hit dicts.
Built in-memory on each call — no persistence needed for Day 2.
"""
import logging
from typing import Any, Dict, List

from rank_bm25 import BM25Okapi

logger = logging.getLogger(__name__)


def bm25_search(
    query: str,
    corpus: List[Dict[str, Any]],
    top_k: int = 10,
) -> List[Dict[str, Any]]:
    """
    Run BM25 over corpus (list of dicts with a 'content' key).
    Returns top_k items sorted by keyword relevance with 'score' added.
    """
    if not corpus:
        return []

    tokenized = [doc.get("content", "").lower().split() for doc in corpus]
    bm25 = BM25Okapi(tokenized)
    scores = bm25.get_scores(query.lower().split())

    ranked = sorted(zip(scores, corpus), key=lambda x: x[0], reverse=True)
    results = []
    for score, doc in ranked[:top_k]:
        hit = dict(doc)
        hit["score"] = float(score)
        results.append(hit)
    return results
