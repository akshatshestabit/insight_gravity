from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.database import init_db
from backend.routers import chat, ingest, retrieve


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run startup tasks (DB init) then yield."""
    await init_db()
    print("✅ Database tables ready")
    yield


app = FastAPI(
    title="InsightForge API",
    description="Multimodal RAG + Agentic AI Platform — Day 1",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(chat.router)
app.include_router(ingest.router)
app.include_router(retrieve.router)

# Mount the frontend UI (must be absolute or relative to the working dir)
app.mount("/ui", StaticFiles(directory="frontend", html=True), name="frontend")


@app.get("/", tags=["health"])
async def root():
    return {"status": "ok", "service": "InsightForge API", "version": "0.1.0"}


@app.get("/health", tags=["health"])
async def health():
    return {"status": "healthy"}
