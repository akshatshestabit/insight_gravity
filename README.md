# InsightForge — Day 1

> **Multimodal RAG + Agentic AI Platform** | LangGraph ReAct Agent · Gemini · PostgreSQL · Celery

---

## What's built

| Component | Location | Description |
|---|---|---|
| FastAPI backend | `backend/` | `/chat` + `/ingest` endpoints, async, CORS |
| LangGraph ReAct Agent | `agents/` | Gemini-powered agent with 3 tools |
| Ingestion pipeline | `ingestion/` | Celery tasks, PDF/text parsers, MinIO storage |
| Progress dashboard | `frontend/` | Dark-mode SPA with live SSE progress |
| Infrastructure | `infra/` | Docker Compose: PostgreSQL + Redis + MinIO |

---

## Quick Start

### 1. Prerequisites
- Python 3.11+
- Docker + Docker Compose
- A [Gemini API key](https://aistudio.google.com/app/apikey) (free)
- *(Optional)* A [Tavily API key](https://tavily.com) for web search (free tier available)

### 2. Setup

```bash
# Clone / open the repo, then:
make setup          # creates .venv, installs deps, copies .env

# Fill in your keys:
nano .env           # set GEMINI_API_KEY (and optionally TAVILY_API_KEY)
```

### 3. Start services (3 terminals)

**Terminal 1 — Infrastructure:**
```bash
make infra
# Starts PostgreSQL:5432, Redis:6379, MinIO:9000
```

**Terminal 2 — Celery worker:**
```bash
make worker
# Picks up ingestion tasks from Redis queue
```

**Terminal 3 — FastAPI:**
```bash
make api
# http://localhost:8000
# http://localhost:8000/docs  ← Swagger UI
```

**Dashboard:**
```bash
open frontend/index.html   # or double-click in file manager
```

---

## API Reference

### `POST /chat`
Send a message to the LangGraph ReAct agent.

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What is sqrt(144) + 100?"}'
```

Response includes `answer` + full `trace` (action → observation → answer).

### `POST /ingest`
Upload documents for distributed processing.

```bash
curl -X POST http://localhost:8000/ingest \
  -F "files=@mydoc.pdf" \
  -F "mode=batch"
# Returns: {"job_id": "...", "status": "queued", "total": 1}
```

### `GET /ingest/status/{job_id}`
Poll for progress.

```bash
curl http://localhost:8000/ingest/status/<job_id>
```

### `GET /ingest/stream/{job_id}`
Live Server-Sent Events stream (used by the dashboard automatically).

---

## Architecture

```
POST /ingest
    │
    ├── Save files to disk (/tmp/insightforge_uploads/<job_id>/)
    ├── Init job state in Redis
    └── Dispatch N × ingest_document Celery tasks
              │
              ├── PDFParser  → pdfplumber (text+tables) + PyMuPDF (images)
              ├── TextParser → plain chunking
              │
              ├── Persist images → MinIO (or local FS fallback)
              ├── Store tables as JSON + Markdown
              └── Update job progress in Redis
                        │
                  GET /ingest/stream/{id}  ← SSE → Dashboard progress bar
```

```
POST /chat
    │
    └── LangGraph create_react_agent(Gemini, [calculator, web_search, file_reader])
              │
              ├── Thought → Action (tool call) → Observation → loop
              └── Final answer + full trace returned
```

---

## Project Layout

```
insight_forge_gravity/
├── backend/
│   ├── main.py          ← FastAPI app
│   ├── config.py        ← Settings (pydantic-settings)
│   ├── database.py      ← Async SQLAlchemy + PostgreSQL
│   ├── models.py        ← ORM models + Pydantic schemas
│   └── routers/
│       ├── chat.py      ← POST /chat
│       └── ingest.py    ← POST /ingest, GET /ingest/status, GET /ingest/stream
├── agents/
│   ├── react_agent.py   ← LangGraph ReAct agent (Gemini)
│   └── tools/
│       ├── calculator.py
│       ├── web_search.py
│       └── file_reader.py
├── ingestion/
│   ├── celery_app.py    ← Celery + Redis config
│   ├── tasks.py         ← ingest_document task (retry × 3)
│   ├── parsers/
│   │   ├── base.py      ← ParseResult, TextChunk, TableChunk, ImageChunk
│   │   ├── pdf_parser.py
│   │   └── text_parser.py
│   └── storage.py       ← MinIO / local FS fallback
├── infra/
│   └── docker-compose.yml
├── frontend/
│   ├── index.html       ← SPA dashboard
│   ├── style.css        ← Dark-mode premium UI
│   └── app.js           ← Tab nav, SSE progress, chat trace
├── Makefile             ← Dev workflow shortcuts
├── requirements.txt
└── .env.example
```

---

## Day 1 Concepts Covered

- ✅ ReAct agent pattern (Thought → Action → Observation loop)
- ✅ LangGraph `create_react_agent` with Gemini
- ✅ Tool-use: calculator, web search (Tavily), file reader
- ✅ Full reasoning trace extraction + display
- ✅ Distributed ingestion (Celery + Redis)
- ✅ Layout-aware PDF extraction (text blocks, tables, images with bbox)
- ✅ Idempotency + retry (task_acks_late, max_retries=3)
- ✅ Job checkpointing in Redis
- ✅ SSE streaming for live progress
- ✅ MinIO object storage with local fallback
- ✅ PostgreSQL via async SQLAlchemy
