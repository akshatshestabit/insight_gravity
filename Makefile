SHELL := /bin/bash
ROOT := $(shell pwd)
VENV := $(ROOT)/.venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
CELERY := $(VENV)/bin/celery
UVICORN := $(VENV)/bin/uvicorn
EXPORT := PYTHONPATH=$(ROOT)

.PHONY: help setup venv install env infra infra-down worker api dev clean logs

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*##' Makefile | awk 'BEGIN{FS=":.*## "}{printf "  \033[36m%-15s\033[0m %s\n",$$1,$$2}'

# ── Setup ─────────────────────────────────────────────────────────────────────
venv:  ## Create Python virtual environment
	python3 -m venv $(VENV)

install: venv  ## Install all Python dependencies
	$(PIP) install --upgrade pip --quiet
	$(PIP) install -r requirements.txt --quiet
	@echo "✅ Dependencies installed"

env:  ## Copy .env.example → .env (only if .env missing)
	@[ -f .env ] || (cp .env.example .env && echo "⚠️  .env created — fill in GEMINI_API_KEY")

setup: install env  ## Full first-time setup (venv + deps + .env)
	@mkdir -p logs
	@echo "✅ Setup complete!"

# ── Infrastructure ────────────────────────────────────────────────────────────
infra:  ## Start Docker services (PostgreSQL, Redis, MinIO)
	docker compose -f infra/docker-compose.yml up -d
	@echo "✅ Services up: PostgreSQL:5432  Redis:6379  MinIO:9000 (console:9001)"

infra-down:  ## Stop Docker services
	docker compose -f infra/docker-compose.yml down

infra-logs:  ## Tail Docker service logs
	docker compose -f infra/docker-compose.yml logs -f

# ── Application ───────────────────────────────────────────────────────────────
worker:  ## Start Celery worker (foreground)
	@mkdir -p logs
	$(EXPORT) $(CELERY) -A ingestion.celery_app worker --loglevel=info

worker-bg:  ## Start Celery worker (background, logs → logs/celery.log)
	@mkdir -p logs
	$(EXPORT) $(CELERY) -A ingestion.celery_app worker --loglevel=info --logfile=logs/celery.log &
	@echo "✅ Celery worker started in background"

api:  ## Start FastAPI server (port 8000, hot-reload)
	$(EXPORT) $(UVICORN) backend.main:app --host 0.0.0.0 --port 8000 --reload

# ── Dev shortcut ──────────────────────────────────────────────────────────────
dev:  ## Start infra + Celery worker + FastAPI (3 commands, run in 3 terminals)
	@echo ""
	@echo "Run these in 3 separate terminals:"
	@echo ""
	@echo "  Terminal 1 (infra):   make infra"
	@echo "  Terminal 2 (worker):  make worker"
	@echo "  Terminal 3 (api):     make api"
	@echo ""
	@echo "Then open: http://localhost:8000/docs"
	@echo "Dashboard: open frontend/index.html in browser"

# ── Utilities ─────────────────────────────────────────────────────────────────
logs:  ## Tail Celery logs
	tail -f logs/celery.log

clean:  ## Remove venv and __pycache__
	rm -rf $(VENV) logs
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@echo "✅ Clean done"
