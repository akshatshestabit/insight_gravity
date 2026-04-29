"""
POST /retrieve  — unified multimodal retrieval with provenance.
GET  /graph/{job_id} — knowledge graph summary for a job.
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from backend.models import (
    RetrieveRequest, RetrieveResponse, RetrievalHit, Provenance, GraphSummary
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/retrieve", tags=["Retrieve"])


@router.post("", response_model=RetrieveResponse)
async def retrieve_docs(request: RetrieveRequest):
    """
    Search indexed documents across text, table, and image collections.
    Returns hits with score, modality, and provenance (filename, page, bbox).
    """
    try:
        from retrieval.retriever import retrieve
        raw = retrieve(
            query=request.query,
            top_k=request.top_k,
            job_id=request.job_id,
            modality=request.modality,
        )
    except Exception as e:
        logger.exception("Retrieval failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Retrieval error: {e}")

    hits = []
    for r in raw:
        prov_data = r.get("provenance", {})
        hits.append(RetrievalHit(
            content=r.get("content", ""),
            score=r.get("score", 0.0),
            modality=r.get("modality", "text"),
            provenance=Provenance(
                filename=prov_data.get("filename", ""),
                page=prov_data.get("page", 0),
                bbox=prov_data.get("bbox"),
                job_id=prov_data.get("job_id", ""),
            ),
        ))

    return RetrieveResponse(
        query=request.query,
        modality_used=request.modality,
        total=len(hits),
        results=hits,
    )


@router.get("/graph/{job_id}", response_model=GraphSummary)
async def graph_summary(job_id: str):
    """Return knowledge graph stats for a given job."""
    try:
        from retrieval.knowledge_graph import get_graph_summary
        summary = get_graph_summary(job_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return GraphSummary(job_id=job_id, **summary)


@router.get("/graph/{job_id}/query")
async def graph_query(job_id: str, entity: str = Query(..., description="Entity name to look up")):
    """Query the knowledge graph for an entity's relationships."""
    try:
        from retrieval.knowledge_graph import query_graph
        result = query_graph(job_id, entity)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"entity": entity, "context": result}
