import os
from celery import Celery

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "insightforge",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["ingestion.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Reliability settings
    task_track_started=True,
    task_acks_late=True,          # ACK only after task completes (safe retry on crash)
    worker_prefetch_multiplier=1,  # One task at a time per worker for fairness
    task_reject_on_worker_lost=True,
)
