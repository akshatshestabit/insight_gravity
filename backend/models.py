import uuid
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from sqlalchemy import Column, Float, Integer, JSON, String, Text
from sqlalchemy import DateTime
from sqlalchemy.sql import func

from backend.database import Base


# ─── SQLAlchemy ORM Models ───────────────────────────────────────────────────

class IngestJob(Base):
    """Tracks the state of a document ingestion job."""
    __tablename__ = "ingest_jobs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    status = Column(String, default="queued")          # queued | processing | done | failed
    total_documents = Column(Integer, default=0)
    processed_documents = Column(Integer, default=0)
    failed_documents = Column(Integer, default=0)
    progress = Column(Float, default=0.0)              # 0.0 – 1.0
    results = Column(JSON, default=list)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class DocumentChunk(Base):
    """Persists extracted text / table / image chunks from a document."""
    __tablename__ = "document_chunks"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id = Column(String, nullable=False)
    document_name = Column(String)
    chunk_type = Column(String)                        # text | table | image
    content = Column(Text)
    metadata_ = Column("metadata", JSON, default=dict)
    page_number = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ─── Pydantic Request / Response Schemas ─────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


class TraceStep(BaseModel):
    type: str                            # input | thought | action | observation | answer
    content: Any
    tool: Optional[str] = None


class ChatResponse(BaseModel):
    answer: str
    trace: List[TraceStep]
    session_id: str


class IngestResponse(BaseModel):
    job_id: str
    status: str
    total: int


class JobStatus(BaseModel):
    job_id: str
    status: str
    progress: float
    total_documents: int
    processed_documents: int
    failed_documents: int
    results: List[Dict[str, Any]] = Field(default_factory=list)
    error: Optional[str] = None
