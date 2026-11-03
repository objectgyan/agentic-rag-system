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
cd frontend && npm ci           # install exactly what package-lock.json pins (Node >= 22.12)
cd frontend && npm run dev      # vite dev server
cd frontend && npm run build    # tsc typecheck (app + vite.config.ts) + vite build

# Live check of the configured models (real API calls, prints cost)
cd backend && python -m app.cli.smoke [--model X] [--no-agent]

# Full-stack end-to-end test (CI job `e2e`; no API key needed - uses e2e/stub_llm.py)
cp e2e/e2e.env .env && docker compose -f docker-compose.yml -f e2e/compose.e2e.yml up -d --build
python e2e/run_e2e.py --base http://localhost:3001
```

## Architecture

### Multi-tenancy & request flow
Tenant isolation is the central invariant. Two layers enforce it:
1. **`TenantContextMiddleware`** (`app/middleware/tenant_context.py`) decodes the JWT (or `X-API-Key`) and stamps `request.state.tenant_id`/`tenant_tier`/`user_id`.
2. **`RateLimitMiddleware`** reads that tier and applies a Redis sliding-window limit (`TierLimits` in `app/core/config.py`).
3. The **`get_current_user`** dependency (`app/api/deps/auth.py`) re-validates the JWT, loads the `User`, and calls **`set_tenant_context`** which runs `SELECT set_config('app.current_tenant', '<id>', false)` (bound param, not string-interpolated — F1) so Postgres Row-Level Security policies (created in the Alembic migrations — `backend/init.sql` only enables extensions) scope every query. **Any route touching tenant data must depend on `get_current_user`** (or `require_admin`/`require_member`) — without it, RLS context is never set.

Middleware sets state but does NOT authenticate; the dependency is the enforcement point. Tiers are `free`/`pro`/`enterprise`; `TierLimits.get()` is the source of truth for limits (the README table can drift from it).

### RAG pipeline (`app/services/rag/`)
`RAGPipeline` (`pipeline.py`) runs an ordered list of **stages** (`stages.py`: route → HyDE → retrieve (plain / multi-query / multi-hop) → business re-rank → compression → graph → conversation memory) over a shared `QueryContext`, then `GenerationService`, then a shared finalize (citations in one query, optional self-eval, trace, usage). `query()` and `query_stream()` share the same stages and finalize — keep it that way; never re-add a separate streaming path. Optional stages are `fail_soft` (failure → name appended to `degraded`). Custom stages: `RAGPipeline(..., stages=[...])`. Pipeline options are the fields of `stages.QueryOptions`; unknown options raise `TypeError`. The retriever (`retriever.py`) does **dense (pgvector cosine) + sparse keyword search fused with Reciprocal Rank Fusion**, then optional Cohere/local cross-encoder re-ranking (no-op if neither configured). Sparse search runs **in Postgres** via a GIN-indexed `chunks.content_tsv` generated column (`ts_rank`/`websearch_to_tsquery`, migration 006) — it replaced an in-Python BM25 that loaded ≤1000 rows per query. An optional per-query trace + cost estimate (`tracing.py`, `trace=true`), intent routing (`intent.py`, `use_routing`), and business/metadata re-ranking (`ranking.py`, `boosts`) layer on top. Citations are post-filtered: only sources the LLM actually referenced as `[Source N]` in its answer are returned (see `_extract_cited_sources`). Usage (tokens + estimated cost) is recorded to `UsageRecord` per query for billing/limits.

### Agentic layer (`app/services/agents/`)
`AgentOrchestrator` (`orchestrator.py`) runs a **native tool-calling loop** (`run()` yields events; `execute()` / `execute_stream()` are views over it) over a `ToolRegistry` (`tools.py`), whose `TOOLS` carry JSON Schemas. Agent types (`research`/`analyst`/`summarizer`/`code`) are different system prompts. Tools run sequentially (they share the request's `AsyncSession`). Tool args are model-generated and therefore attacker-influenced (prompt injection): never `eval` them (the calculator uses `safe_math.py`), and take scope such as `collection_ids` from the authorized request (`ToolRegistry.collection_ids`), never from tool arguments.

### LLM provider layer (`app/llm/`)
All chat-model calls go through the `LLMProvider` port: `get_llm(model)` / `get_fast_llm()` from `app.llm`. **Never import the `openai`/`anthropic` SDKs for chat calls elsewhere.** Model strings are bare (`claude-*` → Anthropic, else OpenAI) or provider-qualified (`anthropic:claude-opus-5-5`, `ollama:llama3.1:8b` for endpoints declared in `LLM_ENDPOINTS`). Adapters: `anthropic_provider.py` (no sampling params — the Claude API rejects them; system prompt cached; server-side refusal fallback; thinking blocks replayed unchanged via `LLMResponse.native`), `openai_provider.py` (also every OpenAI-compatible server). Tests use `with use_llm(FakeLLM([...])):` — don't monkeypatch SDK clients. `core/llm_clients.py` remains only for non-chat APIs (OpenAI embeddings/Whisper/vision, Cohere embed/rerank). Defaults: `default_llm_model`, `default_fast_model` (helper calls), `default_embedding_model`, `default_reranker_model` in `config.py`. Compression / conversation summaries / graph extraction use `settings.compression_model` (`DEFAULT_COMPRESSION_MODEL`, falling back to `default_fast_model`) — never hard-code a vendor model name in a service. Known exception: image vision captioning (`extractors.py`) and audio/video transcription still call OpenAI directly; without an OpenAI key images fall back to OCR.

### Embeddings & the vector column
One embedding model per deployment (`DEFAULT_EMBEDDING_MODEL`); collections may not override it (422) and ingestion always uses it. `chunks.embedding` is `vector(DEFAULT_EMBEDDING_DIMENSIONS)`: config validation rejects a size that doesn't match a known model (`core/embedding_models.py`, which also holds BGE/E5 query/document prefixes), migration 007 sizes the column on fresh installs but refuses to wipe existing embeddings, and model switches on a live DB go through `python -m app.cli.embeddings status|resize --yes|reembed` (owner connection via `DATABASE_SYNC_URL`; logic in `services/rag/vector_schema.py`). Local models cache under `HF_HOME=/models` (the `model-cache` compose volume).

### Policy & metering
Every query/stream/WS/agent route calls `assert_model_allowed` (`api/deps/access.py`) — tier allowlists in `TierLimits.models`, tier read from the DB. Every billable run calls `record_usage` (`services/usage.py`); prices in `tracing.MODEL_PRICING` (longest-prefix match — keep it current). Docs for every block: `docs/learn/blocks/`.

### Async ingestion (`app/services/processing/`)
Uploads create a `Document` (status `pending`) and enqueue the Celery `process_document` task. The worker (`tasks.py`) extracts (`extractors.py` dispatches by `doc_type`: pdf/docx/txt/csv/xlsx/html/image-OCR-or-vision/audio-whisper/video/url) → chunks (`chunker.py`, strategy configured per-collection) → embeds → stores `Chunk` rows with pgvector embeddings. Failed docs are marked `failed` and do **not** auto-retry beyond Celery's 3 attempts.

Celery specifics: tasks are sync wrappers that run async coroutines via `run_async` (new event loop per task), each opening its **own** engine/session (not the request session). The worker must listen to **all three queues** — `celery,processing,rag` (set in `docker-compose.yml` worker command; routing in `celery_app.py`). `celery-beat` runs scheduled tasks.

### Models & API surface
SQLAlchemy models in `app/models/` (tenant, user, api_key, collection, document, chunk, conversation, usage, audit_log) all carry `tenant_id`. Routes are versioned under `/api/v1` and aggregated in `app/api/v1/router.py` (auth, collections, documents, query, agents, admin, health). Admin actions are recorded via `app/core/audit.py` (`user.login`, `user.created`, `documents.uploaded`, `collection.created`, `tenant.tier_updated`).

### Frontend (`frontend/src/`)
React 19 + React Router 7 + Vite 8 + Tailwind 3. Route pages are lazy-loaded (`App.tsx`) under a `Suspense` in `DashboardLayout`. State via Zustand (`store/authStore.ts`, `store/themeStore.ts`), data fetching via TanStack Query, API client in `services/api.ts`, streaming chat over SSE via the API client (the old `useWebSocket` hook was removed). Built/served behind nginx (`nginx.conf`) in the container. Env: `VITE_API_URL`, `VITE_WS_URL`.

## Conventions & gotchas
- **Async everywhere** on the backend: SQLAlchemy async sessions, `asyncpg` driver. Use `await db.execute(select(...))`. Sync URL (`database_sync_url`) exists only for Alembic.
- Enum columns are string enums — use the enum directly, not `.value` (a past bug source noted in `.github/copilot-instructions.md`).
- Text is sanitized of null bytes / control chars before storage (Postgres rejects `\x00`) — see `tasks.py`.
- Cost: prefer `claude-haiku-4-5` or `gpt-4o-mini` for local/dev work.
- SSE responses must carry `SSE_HEADERS` (`app/api/sse.py`) or nginx buffers the stream.
- CI fails on known-vulnerable dependencies (`pip-audit`, `npm audit --omit=dev`). The document parsers parse untrusted uploads — keep them current (`tests/test_extractors.py` guards upgrades).
