"""
/ingest router

Endpoints:
  POST /ingest                → upload files, queue Celery tasks, return job_id
  GET  /ingest/status/{id}   → poll job progress
  GET  /ingest/stream/{id}   → Server-Sent Events for live dashboard updates
"""
import asyncio
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import List, Optional

import redis as _redis
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text

from backend.config import settings
from backend.database import get_db
from backend.models import IngestResponse, JobStatus
from ingestion.tasks import ingest_document

router = APIRouter(prefix="/ingest", tags=["Ingest"])

UPLOAD_DIR = Path(settings.UPLOAD_DIR)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _get_redis() -> _redis.Redis:
    return _redis.from_url(settings.REDIS_URL)


# ── Pydantic schemas for list / delete ───────────────────────────────────────

class DocumentInfo(BaseModel):
    job_id: str
    filename: str
    status: str
    text_chunks: int
    table_chunks: int
    image_chunks: int
    created_at: Optional[str] = None


# ── POST /ingest ──────────────────────────────────────────────────────────────

@router.post("", response_model=IngestResponse, summary="Upload and ingest documents")
async def ingest(
    files: List[UploadFile] = File(..., description="One or more documents to ingest"),
    mode: str = Form(default="batch", description="'batch' (default) queues all at once"),
    job_id: str = Form(default=None, description="Optional: resume an existing job"),
):
    """
    Upload documents and start distributed ingestion.
    Each file is saved then dispatched as an independent Celery task.
    Returns a job_id you can use to poll /ingest/status/{job_id}.
    """
    if not job_id:
        job_id = str(uuid.uuid4())

    # Persist uploaded files to disk
    job_dir = UPLOAD_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    saved_paths: List[str] = []
    for upload in files:
        dest = job_dir / upload.filename
        content = await upload.read()
        dest.write_bytes(content)
        saved_paths.append(str(dest))

    total = len(saved_paths)

    # Initialise job state in Redis
    r = _get_redis()
    r.set(
        f"job:{job_id}",
        json.dumps({
            "job_id": job_id,
            "status": "queued",
            "progress": 0.0,
            "processed": 0,
            "failed": 0,
            "total": total,
            "results": [],
        }),
        ex=3600,
    )

    # Dispatch one Celery task per document
    for idx, path in enumerate(saved_paths):
        ingest_document.delay(job_id, path, idx, total)

    return IngestResponse(job_id=job_id, status="queued", total=total)


# ── GET /ingest/status/{job_id} ───────────────────────────────────────────────

@router.get("/status/{job_id}", response_model=JobStatus, summary="Poll job status")
async def get_status(job_id: str):
    r = _get_redis()
    raw = r.get(f"job:{job_id}")
    if not raw:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    data = json.loads(raw)
    return JobStatus(
        job_id=job_id,
        status=data.get("status", "unknown"),
        progress=data.get("progress", 0.0),
        total_documents=data.get("total", 0),
        processed_documents=data.get("processed", 0),
        failed_documents=data.get("failed", 0),
        results=data.get("results", []),
        error=data.get("error"),
    )


# ── GET /ingest/stream/{job_id} ───────────────────────────────────────────────

@router.get("/stream/{job_id}", summary="Server-Sent Events for live progress")
async def stream_status(job_id: str):
    """
    Streams job progress as Server-Sent Events (SSE).
    The frontend dashboard connects here for live progress bars.
    """
    async def _generator():
        r = _get_redis()
        while True:
            raw = r.get(f"job:{job_id}")
            if raw:
                data = json.loads(raw)
                yield f"data: {json.dumps(data)}\n\n"
                if data.get("status") in ("done", "failed"):
                    break
            else:
                yield f"data: {json.dumps({'job_id': job_id, 'status': 'not_found'})}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── GET /ingest/jobs ──────────────────────────────────────────────────────────

@router.get("/jobs", response_model=List[DocumentInfo], summary="List all ingested documents")
async def list_jobs():
    """
    Returns every ingested document with chunk counts.
    Source of truth is Qdrant (permanent) — Redis keys expire after 1 h.
    Groups by (job_id, filename) across all 3 collections.
    """
    from retrieval.qdrant_store import get_client, COLLECTIONS, ensure_collections
    import asyncio

    try:
        ensure_collections()
        client = get_client()

        # { (job_id, filename): {text, table, image} }
        aggregated: dict = {}

        col_key = {"text_chunks": "text_chunks", "table_chunks": "table_chunks", "image_chunks": "image_chunks"}

        for col in COLLECTIONS:
            offset = None
            while True:
                result, next_offset = client.scroll(
                    collection_name=col,
                    limit=250,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
                for point in result:
                    p = point.payload or {}
                    jid      = p.get("job_id", "unknown")
                    filename = p.get("filename", "unknown")
                    key      = (jid, filename)
                    if key not in aggregated:
                        aggregated[key] = {"text_chunks": 0, "table_chunks": 0, "image_chunks": 0}
                    aggregated[key][col_key[col]] += 1

                if next_offset is None:
                    break
                offset = next_offset

        docs = [
            DocumentInfo(
                job_id=job_id,
                filename=filename,
                status="done",
                text_chunks=counts["text_chunks"],
                table_chunks=counts["table_chunks"],
                image_chunks=counts["image_chunks"],
            )
            for (job_id, filename), counts in aggregated.items()
        ]
        # Sort newest first by job_id (UUID v4 is random, so sort by filename as fallback)
        docs.sort(key=lambda d: d.filename)
        return docs

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── DELETE /ingest/{job_id} ───────────────────────────────────────────────────

@router.delete("/{job_id}", summary="Delete an ingested document and all its vectors")
async def delete_job(job_id: str):
    """
    Permanently removes a document from:
      - PostgreSQL (ingest_jobs + document_chunks)
      - Qdrant (all 3 vector collections)
      - Redis (job state cache)
      - Disk (uploaded files)
    """
    from retrieval.qdrant_store import delete_by_job_id

    errors = []

    # 1. Qdrant
    try:
        delete_by_job_id(job_id)
    except Exception as exc:
        errors.append(f"Qdrant: {exc}")

    # 2. PostgreSQL
    try:
        async for db in get_db():
            await db.execute(
                text("DELETE FROM document_chunks WHERE job_id = :jid"),
                {"jid": job_id},
            )
            await db.execute(
                text("DELETE FROM ingest_jobs WHERE id = :jid"),
                {"jid": job_id},
            )
            await db.commit()
    except Exception as exc:
        errors.append(f"DB: {exc}")

    # 3. Redis
    try:
        r = _get_redis()
        r.delete(f"job:{job_id}")
    except Exception as exc:
        errors.append(f"Redis: {exc}")

    # 4. Disk (uploaded files)
    try:
        job_dir = UPLOAD_DIR / job_id
        if job_dir.exists():
            shutil.rmtree(job_dir)
    except Exception as exc:
        errors.append(f"Disk: {exc}")

    if errors:
        return {"job_id": job_id, "status": "partial_delete", "errors": errors}
    return {"job_id": job_id, "status": "deleted"}
