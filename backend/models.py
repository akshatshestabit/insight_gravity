import uuid
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from sqlalchemy import Boolean, Column, Float, Integer, JSON, String, Text
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


# ─── Day 2: Retrieval Schemas ─────────────────────────────────────────────────

class RetrieveRequest(BaseModel):
    query: str
    job_id: Optional[str] = None
    top_k: int = 5
    modality: str = "auto"   # auto | text | table | image | graph | all


class Provenance(BaseModel):
    filename: str
    page: int
    bbox: Optional[List[float]] = None
    job_id: str = ""


class RetrievalHit(BaseModel):
    content: str
    score: float
    modality: str
    provenance: Provenance


class RetrieveResponse(BaseModel):
    query: str
    modality_used: str = "auto"
    total: int
    results: List[RetrievalHit]


class GraphSummary(BaseModel):
    job_id: str
    nodes: int
    edges: int
    entities: List[str]


# ─── Day 3: Multi-Agent & MCP Models ─────────────────────────────────────────

class ResearchSession(Base):
    """Persists multi-agent research session metadata and outputs."""
    __tablename__ = "research_sessions"

    id             = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    query          = Column(Text, nullable=False)
    plan           = Column(JSON, nullable=True)
    final_report   = Column(JSON, nullable=True)
    critique       = Column(JSON, nullable=True)
    cost_usd       = Column(Float, default=0.0)
    duration_ms    = Column(Integer, default=0)
    awaiting_approval = Column(Boolean, default=False)
    error          = Column(Text, nullable=True)
    created_at     = Column(DateTime(timezone=True), server_default=func.now())
    updated_at     = Column(DateTime(timezone=True), onupdate=func.now())


class AuditLog(Base):
    """Signed audit trail for every MCP tool invocation."""
    __tablename__ = "audit_log"

    id             = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    timestamp      = Column(String, nullable=False)
    session_id     = Column(String, nullable=True)
    server_name    = Column(String, nullable=True)
    tool_name      = Column(String, nullable=False)
    arguments      = Column(Text, nullable=True)       # JSON-serialised
    result_summary = Column(Text, nullable=True)
    success        = Column(Boolean, default=True)
    error          = Column(Text, nullable=True)
    signature      = Column(String(64), nullable=False) # HMAC-SHA256 hex
