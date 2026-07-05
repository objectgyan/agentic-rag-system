# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

AgentRAG is a multi-tenant Agentic RAG-as-a-Service platform: a FastAPI backend (`backend/`) with async SQLAlchemy + pgvector, a Celery worker pool for document ingestion, and a React + TypeScript + Vite frontend (`frontend/`). Everything runs via `docker-compose`.

## Running & Common Commands

Development is Docker-first. **Backend code changes require a container rebuild** (the `backend` service bind-mounts `./backend` with `--reload`, but the `celery-worker` does NOT — it must be rebuilt for code changes to take effect).

```bash
docker-compose up -d                              # start full stack
docker-compose up -d --build celery-worker        # rebuild worker after backend code changes
docker-compose up -d --force-recreate backend celery-worker   # apply .env changes (restart is not enough)
docker-compose logs -f celery-worker              # tail worker logs (where ingestion failures surface)
```

Service URLs (note these are the host-mapped ports, which differ from internal ports and from some README values):
- Frontend: http://localhost:3001 · Backend/API docs: http://localhost:8000/docs · MinIO console: http://localhost:9001 (minioadmin/minioadmin)
- Postgres is exposed on host **5433**, Redis on **6380** (internal ports are still 5432/6379).

```bash
# Backend tests (run inside the backend container or a venv with requirements.txt)
cd backend && pytest -v --cov=app
cd backend && pytest tests/test_chunker.py -v             # single file
cd backend && pytest tests/test_auth.py::test_name -v     # single test

# DB migrations (run automatically on backend startup via `alembic upgrade head`)
docker exec -it agentic-rag-system-backend-1 alembic upgrade head
docker exec -it agentic-rag-system-backend-1 alembic revision --autogenerate -m "msg"

# Frontend
cd frontend && npm run dev      # vite dev server
cd frontend && npm run build    # tsc typecheck + vite build
cd frontend && npm test         # vitest
```

## Architecture

### Multi-tenancy & request flow
Tenant isolation is the central invariant. Two layers enforce it:
1. **`TenantContextMiddleware`** (`app/middleware/tenant_context.py`) decodes the JWT (or `X-API-Key`) and stamps `request.state.tenant_id`/`tenant_tier`/`user_id`.
2. **`RateLimitMiddleware`** reads that tier and applies a Redis sliding-window limit (`TierLimits` in `app/core/config.py`).
3. The **`get_current_user`** dependency (`app/api/deps/auth.py`) re-validates the JWT, loads the `User`, and calls **`set_tenant_context`** which runs `SELECT set_config('app.current_tenant', '<id>', false)` (bound param, not string-interpolated — F1) so Postgres Row-Level Security policies (created in the Alembic migrations — `backend/init.sql` only enables extensions) scope every query. **Any route touching tenant data must depend on `get_current_user`** (or `require_admin`/`require_member`) — without it, RLS context is never set.

Middleware sets state but does NOT authenticate; the dependency is the enforcement point. Tiers are `free`/`pro`/`enterprise`; `TierLimits.get()` is the source of truth for limits (the README table can drift from it).

### RAG pipeline (`app/services/rag/`)
`RAGPipeline` (`pipeline.py`) orchestrates: optional query enhancement (HyDE / multi-query in `query_enhancer.py`) → `HybridRetriever` → `GenerationService`. The retriever (`retriever.py`) does **dense (pgvector cosine) + sparse keyword search fused with Reciprocal Rank Fusion**, then optional Cohere/local cross-encoder re-ranking (no-op if neither configured). Sparse search runs **in Postgres** via a GIN-indexed `chunks.content_tsv` generated column (`ts_rank`/`websearch_to_tsquery`, migration 006) — it replaced an in-Python BM25 that loaded ≤1000 rows per query. An optional per-query trace + cost estimate (`tracing.py`, `trace=true`), intent routing (`intent.py`, `use_routing`), and business/metadata re-ranking (`ranking.py`, `boosts`) layer on top. Citations are post-filtered: only sources the LLM actually referenced as `[Source N]` in its answer are returned (see `_extract_cited_sources`). Usage (tokens + estimated cost) is recorded to `UsageRecord` per query for billing/limits.

### Agentic layer (`app/services/agents/`)
`AgentOrchestrator` (`orchestrator.py`) is a hand-rolled **ReAct loop** (text-parsed `Thought:`/`Action:`/`Action Input:`/`Final Answer:`, not native tool-calling) over a `ToolRegistry` (`tools.py`). Agent types (`research`/`analyst`/`summarizer`/`code`) are just different system prompts. Both pipeline and orchestrator support streaming variants.

### LLM provider routing
There is no abstraction layer: code branches on the model name string. `model.startswith("claude")` → Anthropic SDK, otherwise OpenAI SDK (see `orchestrator._call_llm`, mirrored in `generator.py`). Default models live in `config.py` (`default_llm_model`, `default_embedding_model`, `default_reranker_model`).

### Async ingestion (`app/services/processing/`)
Uploads create a `Document` (status `pending`) and enqueue the Celery `process_document` task. The worker (`tasks.py`) extracts (`extractors.py` dispatches by `doc_type`: pdf/docx/txt/csv/xlsx/html/image-OCR-or-vision/audio-whisper/video/url) → chunks (`chunker.py`, strategy configured per-collection) → embeds → stores `Chunk` rows with pgvector embeddings. Failed docs are marked `failed` and do **not** auto-retry beyond Celery's 3 attempts.

Celery specifics: tasks are sync wrappers that run async coroutines via `run_async` (new event loop per task), each opening its **own** engine/session (not the request session). The worker must listen to **all three queues** — `celery,processing,rag` (set in `docker-compose.yml` worker command; routing in `celery_app.py`). `celery-beat` runs scheduled tasks.

### Models & API surface
SQLAlchemy models in `app/models/` (tenant, user, api_key, collection, document, chunk, conversation, usage, audit_log) all carry `tenant_id`. Routes are versioned under `/api/v1` and aggregated in `app/api/v1/router.py` (auth, collections, documents, query, agents, admin, health). Admin actions are recorded via `app/core/audit.py` (`user.login`, `user.created`, `documents.uploaded`, `collection.created`, `tenant.tier_updated`).

### Frontend (`frontend/src/`)
React 18 + Vite + Tailwind. State via Zustand (`store/authStore.ts`, `store/themeStore.ts`), data fetching via TanStack Query, API client in `services/api.ts`, streaming chat over SSE via the API client (the old `useWebSocket` hook was removed). Built/served behind nginx (`nginx.conf`) in the container. Env: `VITE_API_URL`, `VITE_WS_URL`.

## Conventions & gotchas
- **Async everywhere** on the backend: SQLAlchemy async sessions, `asyncpg` driver. Use `await db.execute(select(...))`. Sync URL (`database_sync_url`) exists only for Alembic.
- Enum columns are string enums — use the enum directly, not `.value` (a past bug source noted in `.github/copilot-instructions.md`).
- Text is sanitized of null bytes / control chars before storage (Postgres rejects `\x00`) — see `tasks.py`.
- Cost: prefer `gpt-4o-mini` for local/dev work.
