# Architecture

This reflects the **hardened** system (after Phases 0–3 and the frontend pass), not the
original demo. See [`PRODUCTION_ROADMAP.md`](PRODUCTION_ROADMAP.md) for how it got here.

```mermaid
flowchart TB
    BROWSER[Browser SPA]
    APICLI[API and WebSocket clients]

    subgraph Edge
        FE[Frontend nginx-unprivileged]
    end

    BROWSER --> FE
    FE -->|/api and /ws proxy| MW
    APICLI -->|JWT or X-API-Key| MW

    subgraph Backend[FastAPI backend non-root]
        MW[Middleware chain: RequestContext, TenantContext, RateLimit]
        AUTH[get_current_user: validate JWT, SET app.current_tenant for RLS]
        ROUTES[Routes /api/v1: auth, collections, documents, query, agents, admin]
        METRICS[/metrics Prometheus/]
        MW --> AUTH --> ROUTES
        ROUTES -.-> METRICS

        subgraph RAG[RAG pipeline]
            direction LR
            ENH[Enhance: HyDE, multi-query] --> RET[Hybrid retrieval: dense pgvector + BM25 + RRF]
            RET --> RR[Re-rank: Cohere or local cross-encoder]
            RR --> MH[Multi-hop loop] --> CMP[Contextual compression] --> KG[Knowledge-graph 1-hop facts] --> GEN[Generate + citations]
        end

        subgraph AGENTS[Agent orchestrator]
            REACT[ReAct loop + tools] --> DELEG[delegate to depth-bounded sub-agent]
        end

        ROUTES --> RAG
        ROUTES --> AGENTS
    end

    subgraph Data[Datastores]
        PG[(PostgreSQL + pgvector. RLS forced, app role agentrag_app)]
        REDIS[(Redis: cache, rate-limit, broker)]
        MINIO[(MinIO or S3: document blobs)]
    end

    AUTH --> PG
    RAG --> PG
    ROUTES --> REDIS
    ROUTES --> MINIO

    subgraph Workers[Celery non-root]
        WORKER[Worker pool: extract, chunk, embed, graph]
        BEAT[Beat scheduler]
        WMETRICS[/worker :9100 metrics/]
        WORKER -.-> WMETRICS
    end

    ROUTES -->|enqueue process_document| REDIS
    REDIS --> WORKER
    BEAT --> REDIS
    WORKER --> PG
    WORKER --> MINIO

    subgraph External[External providers]
        OAI[OpenAI]
        ANT[Anthropic]
        COH[Cohere]
        TAV[Tavily web search]
    end

    GEN --> OAI
    GEN --> ANT
    RR --> COH
    WORKER --> OAI
    AGENTS --> TAV

    ALEMBIC[Alembic migrations, owner role agentrag] -->|DDL only| PG
```

## How to read it

**Edge → backend.** The browser SPA is served by a non-root nginx that reverse-proxies
`/api` and `/ws` to the backend. API/WS clients hit the backend directly with a JWT (or
`X-API-Key`).

**Request path (the security spine).** Every request passes the middleware chain —
`RequestContextMiddleware` binds an `X-Request-ID` for log correlation, `TenantContext`
decodes the JWT, `RateLimit` applies tier (and IP, for `/auth`) limits. The
`get_current_user` dependency is the *enforcement point*: it validates the JWT, loads the
user, and runs `SET app.current_tenant` so Postgres Row-Level Security scopes every query.

**Tenant isolation (the keystone).** The app and worker connect as **`agentrag_app`** — a
non-owner, non-superuser role — with `FORCE ROW LEVEL SECURITY` on every tenant table, so
RLS actually applies. Alembic connects as the owner role for DDL only.

**RAG pipeline.** Hybrid retrieval (dense pgvector + BM25, fused with RRF) → optional
re-rank (Cohere or a local cross-encoder) → optional multi-hop, contextual compression, and
knowledge-graph facts → generation with filtered citations. Every advanced step is opt-in
per request and degrades gracefully.

**Agents.** A ReAct orchestrator over a tool registry; agents can `delegate` to a
depth-bounded sub-agent.

**Async ingestion.** Uploads enqueue a Celery task via Redis; the worker pool extracts →
chunks → embeds → (optionally) extracts knowledge-graph triples, writing to Postgres and
MinIO. Workers run non-root with exponential-backoff retries and export their own metrics.

**Observability.** The backend exposes `/metrics`; the worker exposes its own metrics on
`:9100` (prometheus multiprocess mode). Logs carry request/tenant correlation.
