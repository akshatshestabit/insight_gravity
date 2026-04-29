"""
Celery tasks for document ingestion (Day 2 — with VLM captioning + Qdrant indexing).

Flow:
  POST /ingest → save files → ingest_document.delay() per file
                                  ↓
                        parse (text/table/image)
                                  ↓
                        VLM caption each image via Gemini Vision
                                  ↓
                        embed + upsert chunks to Qdrant
                                  ↓
                        build knowledge graph (NetworkX)
                                  ↓
                        store images to MinIO / local FS
                                  ↓
                        update job progress in Redis
"""
import base64
import json
import logging
import os
from pathlib import Path

import redis as _redis
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage

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
    return TextParser()


def _read_job(r: _redis.Redis, job_id: str) -> dict:
    raw = r.get(f"job:{job_id}")
    return json.loads(raw) if raw else {"job_id": job_id, "results": [], "processed": 0, "failed": 0}


def _write_job(r: _redis.Redis, job_id: str, data: dict):
    r.set(f"job:{job_id}", json.dumps(data), ex=3600)


def _vlm_caption(image_bytes: bytes, extension: str) -> str:
    """Generate a caption for an image using Gemini Vision."""
    try:
        from backend.config import settings
        llm = ChatGoogleGenerativeAI(
            model=settings.GEMINI_MODEL,
            google_api_key=settings.GEMINI_API_KEY,
            temperature=0,
        )
        mime = f"image/{extension}" if extension != "jpg" else "image/jpeg"
        b64 = base64.b64encode(image_bytes).decode()
        msg = HumanMessage(content=[
            {"type": "text", "text": "Describe this image or chart concisely for document retrieval. Include key data points, labels, or findings."},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ])
        resp = llm.invoke([msg])
        return resp.content.strip()
    except Exception as e:
        logger.warning("VLM captioning failed: %s", e)
        return ""


def _embed_and_upsert(job_id: str, filename: str, result) -> None:
    """Embed all parsed chunks and upsert into Qdrant."""
    try:
        from retrieval.embedder import embed_texts
        from retrieval.qdrant_store import ensure_collections, upsert_chunks

        ensure_collections()

        # --- Text chunks ---
        if result.text_chunks:
            texts = [c.content for c in result.text_chunks]
            vectors = embed_texts(texts)
            payloads = [
                {
                    "job_id": job_id,
                    "filename": filename,
                    "content": c.content,
                    "page": c.page_number,
                    "bbox": None,
                    "chunk_type": "text",
                }
                for c in result.text_chunks
            ]
            n = upsert_chunks("text_chunks", vectors, payloads)
            logger.info("[%s] Upserted %d text chunks", job_id, n)

        # --- Table chunks ---
        if result.table_chunks:
            texts = [c.content_markdown for c in result.table_chunks]
            vectors = embed_texts(texts)
            payloads = [
                {
                    "job_id": job_id,
                    "filename": filename,
                    "content": c.content_markdown,
                    "page": c.page_number,
                    "bbox": None,
                    "chunk_type": "table",
                }
                for c in result.table_chunks
            ]
            n = upsert_chunks("table_chunks", vectors, payloads)
            logger.info("[%s] Upserted %d table chunks", job_id, n)

        # --- Image chunks ---
        if result.image_chunks:
            texts = [c.caption or f"Image on page {c.page_number}" for c in result.image_chunks]
            vectors = embed_texts(texts)
            payloads = [
                {
                    "job_id": job_id,
                    "filename": filename,
                    "content": c.caption or f"Image on page {c.page_number}",
                    "page": c.page_number,
                    "bbox": list(c.bbox) if c.bbox else None,
                    "chunk_type": "image",
                }
                for c in result.image_chunks
            ]
            n = upsert_chunks("image_chunks", vectors, payloads)
            logger.info("[%s] Upserted %d image chunks", job_id, n)

    except Exception as e:
        logger.warning("[%s] Qdrant upsert failed (non-fatal): %s", job_id, e)


def _build_knowledge_graph(job_id: str, result) -> None:
    """Build the knowledge graph from text chunks (async-safe sync call)."""
    try:
        from retrieval.knowledge_graph import build_graph
        texts = [c.content for c in result.text_chunks]
        triples = build_graph(job_id, texts)
        logger.info("[%s] Knowledge graph: %d triples added", job_id, triples)
    except Exception as e:
        logger.warning("[%s] Knowledge graph build failed (non-fatal): %s", job_id, e)


@celery_app.task(bind=True, name="ingest_document", max_retries=3, default_retry_delay=5)
def ingest_document(self, job_id: str, file_path: str, doc_index: int, total: int):
    """
    Parse one document, caption images, embed+index into Qdrant, build knowledge graph.
    Updates shared job state in Redis after completion.
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

            # 1. VLM captioning for images
            for img in result.image_chunks:
                if not img.caption:
                    img.caption = _vlm_caption(img.image_bytes, img.extension)

            # 2. Persist table metadata
            for tbl in result.table_chunks:
                doc_summary["tables_data"].append({
                    "page": tbl.page_number,
                    "markdown": tbl.content_markdown,
                    "json": tbl.content_json,
                })

            # 3. Persist images to MinIO / local
            for i, img in enumerate(result.image_chunks):
                key = f"{job_id}/{result.filename}/img_p{img.page_number}_{i}.{img.extension}"
                url = storage.put_object(key, img.image_bytes, f"image/{img.extension}")
                doc_summary["image_urls"].append({
                    "url": url,
                    "caption": img.caption,
                    "page": img.page_number,
                    "bbox": list(img.bbox) if img.bbox else None,
                })

            # 4. Embed + upsert to Qdrant
            _embed_and_upsert(job_id, result.filename, result)

            # 5. Build knowledge graph
            _build_knowledge_graph(job_id, result)

        # Update Redis job state
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
        job = _read_job(r, job_id)
        job["failed"] = job.get("failed", 0) + 1
        done = job.get("processed", 0) + job.get("failed", 0)
        job["progress"] = round(done / total, 4)
        job["status"] = "done" if done >= total else "processing"
        _write_job(r, job_id, job)
        raise self.retry(exc=exc)
