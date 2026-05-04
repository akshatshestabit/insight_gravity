from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.database import init_db
from backend.routers import chat, ingest, retrieve
from backend.routers import research, session as session_router
from backend.routers.eval_router import router as eval_router
from guardrails.middleware import GuardrailsMiddleware
from audit.chain import log_request


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run startup tasks (DB init) then yield."""
    await init_db()
    print("✅ Database tables ready")
    yield


app = FastAPI(
    title="InsightForge API",
    description="Multimodal RAG + Multi-Agent Orchestration + MCP — Day 3",
    version="0.3.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Day 4: guardrails on all LLM-facing endpoints
app.add_middleware(GuardrailsMiddleware, redact_output_pii=True)

# Routers — Day 1 / 2
app.include_router(chat.router)
app.include_router(ingest.router)
app.include_router(retrieve.router)

# Routers — Day 3: multi-agent research + session management
app.include_router(research.router)
app.include_router(session_router.router)

# Routers — Day 4: eval, red-team, guardrail validation, audit chain
app.include_router(eval_router)

# Mount the frontend UI (must be absolute or relative to the working dir)
app.mount("/ui", StaticFiles(directory="frontend", html=True), name="frontend")


@app.get("/", tags=["health"])
async def root():
    return {"status": "ok", "service": "InsightForge API", "version": "0.3.0"}


@app.get("/health", tags=["health"])
async def health():
    return {"status": "healthy"}


@app.get("/agents", tags=["health"])
async def agents_info():
    """List available agents and their capabilities."""
    return {
        "crew": {
            "topology": "supervisor-hierarchical",
            "agents": ["planner", "retriever", "table_analyst", "chart_analyst", "synthesizer", "critic"],
            "checkpointing": "postgres",
            "hitl": True,
            "budget_enforcement": True,
        },
        "mcp_servers": {
            "custom": ["crm_lookup", "create_jira_ticket", "post_to_slack", "draft_email"],
            "public": ["github", "filesystem"],
        },
    }
