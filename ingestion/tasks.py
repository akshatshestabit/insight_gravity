"""
Celery tasks for document ingestion.

Flow:
  POST /ingest → save files → [for each file] ingest_document.delay()
                                    ↓
                          parse (text/table/image)
                                    ↓
                          store images to MinIO/local
                                    ↓
                          update job progress in Redis

Idempotency: tasks use job_id + file_path as a logical key.
Retry: automatic retry (up to 3×) with 5s back-off on failure.
"""
import json
import logging
import os
from pathlib import Path

import redis as _redis

from ingestion.celery_app import celery_app
from ingestion.parsers.pdf_parser import PDFParser
from ingestion.parsers.text_parser import TextParser
from ingestion.storage import StorageClient

logger = logging.getLogger(__name__)

_PARSERS = [PDFParser(), TextParser()]


def _get_redis() -> _redis.Redis:
    return _redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))


def _get_parser(file_path: str):
    ext = Path(file_path).suffix
    for p in _PARSERS:
        if p.supports(ext):
            return p
    return TextParser()  # fallback


def _read_job(r: _redis.Redis, job_id: str) -> dict:
    raw = r.get(f"job:{job_id}")
    return json.loads(raw) if raw else {"job_id": job_id, "results": [], "processed": 0, "failed": 0}


def _write_job(r: _redis.Redis, job_id: str, data: dict):
    r.set(f"job:{job_id}", json.dumps(data), ex=3600)


@celery_app.task(bind=True, name="ingest_document", max_retries=3, default_retry_delay=5)
def ingest_document(self, job_id: str, file_path: str, doc_index: int, total: int):
    """
    Parse one document and persist its artifacts.
    Updates the shared job state in Redis after each document.
    """
    logger.info("[%s] Processing %d/%d: %s", job_id, doc_index + 1, total, file_path)
    r = _get_redis()

    try:
        parser = _get_parser(file_path)
        result = parser.parse(file_path)

        doc_summary = {
            "filename": result.filename,
            "text_chunks": len(result.text_chunks),
            "tables": len(result.table_chunks),
            "images": len(result.image_chunks),
            "tables_data": [],
            "image_urls": [],
            "error": result.error,
        }

        if result.error:
            logger.warning("[%s] Parse error: %s", job_id, result.error)
        else:
            storage = StorageClient()

            # Persist table data (JSON + markdown)
            for i, tbl in enumerate(result.table_chunks):
                doc_summary["tables_data"].append({
                    "page": tbl.page_number,
                    "markdown": tbl.content_markdown,
                    "json": tbl.content_json,
                })

            # Persist images to storage
            for i, img in enumerate(result.image_chunks):
                key = f"{job_id}/{result.filename}/img_p{img.page_number}_{i}.{img.extension}"
                content_type = f"image/{img.extension}"
                url = storage.put_object(key, img.image_bytes, content_type)
                doc_summary["image_urls"].append({"url": url, "caption": img.caption, "page": img.page_number})

        # Atomic read-modify-write of job state
        job = _read_job(r, job_id)
        job["results"].append(doc_summary)

        if result.error:
            job["failed"] = job.get("failed", 0) + 1
        else:
            job["processed"] = job.get("processed", 0) + 1

        done = job.get("processed", 0) + job.get("failed", 0)
        job["progress"] = round(done / total, 4)
        job["status"] = "done" if done >= total else "processing"

        _write_job(r, job_id, job)
        logger.info("[%s] Progress %.1f%%", job_id, job["progress"] * 100)

    except Exception as exc:
        logger.exception("[%s] Task failed: %s", job_id, exc)
        # Mark as failed in job state
        job = _read_job(r, job_id)
        job["failed"] = job.get("failed", 0) + 1
        done = job.get("processed", 0) + job.get("failed", 0)
        job["progress"] = round(done / total, 4)
        job["status"] = "done" if done >= total else "processing"
        _write_job(r, job_id, job)
        raise self.retry(exc=exc)
