"""
Qdrant vector store — 3 collections: text_chunks, table_chunks, image_chunks.

Unified payload schema per point:
  {doc_id, job_id, filename, page, bbox, content, chunk_type}
"""
import logging
import uuid
from typing import Any, Dict, List, Optional

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct,
    Filter, FieldCondition, MatchValue,
)

from backend.config import settings

logger = logging.getLogger(__name__)

VECTOR_SIZE = 3072  # gemini-embedding-001 output dimension
COLLECTIONS = ["text_chunks", "table_chunks", "image_chunks"]

_client: Optional[QdrantClient] = None


def get_client() -> QdrantClient:
    global _client
    if _client is None:
        _client = QdrantClient(url=settings.QDRANT_URL)
    return _client


def ensure_collections() -> None:
    """Create Qdrant collections if they don't already exist."""
    client = get_client()
    existing = {c.name for c in client.get_collections().collections}
    for name in COLLECTIONS:
        if name not in existing:
            client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
            )
            logger.info("Created Qdrant collection: %s", name)


def upsert_chunks(
    collection: str,
    vectors: List[List[float]],
    payloads: List[Dict[str, Any]],
) -> int:
    """Upsert points into a collection. Returns count upserted."""
    if not vectors:
        return 0
    client = get_client()
    points = [
        PointStruct(id=str(uuid.uuid4()), vector=vec, payload=payload)
        for vec, payload in zip(vectors, payloads)
    ]
    client.upsert(collection_name=collection, points=points)
    return len(points)


def delete_by_job_id(job_id: str) -> int:
    """Delete all vectors for a given job_id from every collection. Returns total deleted."""
    from qdrant_client.models import FilterSelector
    client = get_client()
    total = 0
    flt = Filter(must=[FieldCondition(key="job_id", match=MatchValue(value=job_id))])
    for col in COLLECTIONS:
        try:
            result = client.delete(
                collection_name=col,
                points_selector=FilterSelector(filter=flt),
            )
            # result.operation_id signals success; count removed is not returned directly
            total += 1
            logger.info("Deleted job_id=%s from %s", job_id, col)
        except Exception as exc:
            logger.warning("Qdrant delete failed on %s: %s", col, exc)
    return total


def search(
    collection: str,
    query_vector: List[float],
    top_k: int = 10,
    job_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Dense vector search. Optionally filter by job_id."""
    client = get_client()
    filter_ = None
    if job_id:
        filter_ = Filter(
            must=[FieldCondition(key="job_id", match=MatchValue(value=job_id))]
        )
    try:
        hits = client.search(
            collection_name=collection,
            query_vector=query_vector,
            limit=top_k,
            query_filter=filter_,
            score_threshold=0.65,
            with_payload=True,
        )
        return [
            {
                "score": hit.score,
                "content": hit.payload.get("content", ""),
                "modality": hit.payload.get("chunk_type", collection.replace("_chunks", "")),
                "provenance": {
                    "filename": hit.payload.get("filename", ""),
                    "page": hit.payload.get("page", 0),
                    "bbox": hit.payload.get("bbox"),
                    "job_id": hit.payload.get("job_id", ""),
                },
            }
            for hit in hits
        ]
    except Exception as e:
        logger.warning("Qdrant search failed on %s: %s", collection, e)
        return []
