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
import uuid
from pathlib import Path
from typing import List

import redis as _redis
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from backend.config import settings
from backend.models import IngestResponse, JobStatus
from ingestion.tasks import ingest_document

router = APIRouter(prefix="/ingest", tags=["Ingest"])

UPLOAD_DIR = Path(settings.UPLOAD_DIR)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _get_redis() -> _redis.Redis:
    return _redis.from_url(settings.REDIS_URL)


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
