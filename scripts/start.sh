#!/usr/bin/env bash
# InsightForge — Day 1 startup script
# Usage: bash scripts/start.sh

set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VENV="$ROOT/.venv"
PYTHON="$VENV/bin/python"
PIP="$VENV/bin/pip"

echo ""
echo "⚡ InsightForge Day 1 — Setup & Start"
echo "======================================"

# ── 1. Create venv if needed ──────────────────────────────────────────────────
if [ ! -f "$PYTHON" ]; then
  echo "📦 Creating virtual environment..."
  python3 -m venv "$VENV"
fi

# ── 2. Install dependencies ───────────────────────────────────────────────────
echo "📦 Installing Python dependencies..."
"$PIP" install --quiet --upgrade pip
"$PIP" install --quiet -r requirements.txt
echo "✅ Dependencies installed"

# ── 3. Copy .env if missing ───────────────────────────────────────────────────
if [ ! -f "$ROOT/.env" ]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  echo "⚠️  Created .env from .env.example — please fill in your GEMINI_API_KEY"
fi

# ── 4. Start Docker services ──────────────────────────────────────────────────
echo ""
echo "🐳 Starting Docker services (PostgreSQL, Redis, MinIO)..."
docker compose -f infra/docker-compose.yml up -d
echo "⏳ Waiting for services to be healthy..."
sleep 5

# ── 5. Export PYTHONPATH so imports work ──────────────────────────────────────
export PYTHONPATH="$ROOT"

# ── 6. Start Celery worker in background ─────────────────────────────────────
echo ""
echo "🔄 Starting Celery worker..."
"$VENV/bin/celery" -A ingestion.celery_app worker --loglevel=info --logfile=logs/celery.log &
CELERY_PID=$!
echo "   Celery PID: $CELERY_PID"

# ── 7. Start FastAPI ──────────────────────────────────────────────────────────
echo ""
echo "🚀 Starting FastAPI server on http://localhost:8000"
echo "   API docs → http://localhost:8000/docs"
echo "   Dashboard → open frontend/index.html in your browser"
echo ""
"$VENV/bin/uvicorn" backend.main:app --host 0.0.0.0 --port 8000 --reload

# Cleanup on exit
trap "kill $CELERY_PID 2>/dev/null; docker compose -f infra/docker-compose.yml stop" EXIT
