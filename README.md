# PerfectTaskTracker

Workspace for organizing, connecting, and visualizing tasks and ideas.
Filesystem is the source of truth; PostgreSQL is a rebuildable index;
the in-memory tree mirrors disk for fast UI traversal.

See [docs/TECH_SPEC.md](docs/TECH_SPEC.md) for the full architecture.

## Quick start

```bash
# 1. Copy env template
cp .env.example .env

# 2. Install deps
uv sync

# 3. Start Postgres
docker compose up -d postgres

# 4. Run the backend
uv run uvicorn backend.main:app --reload
```

## Health check

```bash
curl http://localhost:8000/health
```# perfect-task-tracker
