# 11 · Build it yourself

The real test of understanding is rebuilding from an empty folder. This is a staged plan — each
stage is runnable and adds one concept. Don't try to build the whole thing at once; you'll learn
far more by getting a tiny version working end-to-end and growing it.

> Tip: resist copying this repo. Type it. When you get stuck, *then* compare. The struggle is
> where the learning happens.

## Stage 0 — Hello, async API (½ day)

**Goal:** a FastAPI app you can run and hit.
- Install Python 3.11, create a virtualenv, `pip install fastapi uvicorn`.
- One file with `app = FastAPI()` and a `GET /health` returning `{"status":"ok"}`.
- Run `uvicorn main:app --reload`, open `/docs` (free Swagger UI).

✅ *You've got it when:* you understand `async def`, the `app` object, and that `/docs` is
generated from your function signatures.

## Stage 1 — Data + the ORM (1 day)

**Goal:** persist something.
- `docker run` a Postgres. `pip install sqlalchemy[asyncio] asyncpg alembic`.
- Define a `Collection` model, set up the async engine + session, add `get_db` as a dependency.
- `POST /collections` (insert) and `GET /collections` (list).
- Wire up Alembic; create your first migration.

✅ *You've got it when:* a row survives a restart, and you can explain what `Depends(get_db)`
injects and when the session closes.

## Stage 2 — Auth (1 day)

**Goal:** users, login, protected routes.
- `pip install "python-jose[cryptography]" "passlib[bcrypt]"`.
- `User` model with a bcrypt password hash. `POST /auth/register`, `POST /auth/login` returning a
  JWT.
- A `get_current_user` dependency that validates the JWT and loads the user. Protect a route.

✅ *You've got it when:* an unauthenticated request gets `401`, a valid token gets through, and
you understand what's *inside* a JWT (and why the secret matters).

## Stage 3 — Multi-tenancy + RLS (1 day, the important one)

**Goal:** real isolation.
- Add `tenant_id` to every table. Put `tenant_id` in the JWT.
- Create a Postgres **RLS policy** per table and a **non-owner role** the app connects as.
- In `get_current_user`, `SET app.current_tenant`.
- **Prove it:** with two tenants, connect to the DB as the app role and confirm tenant A's
  context cannot see tenant B's rows — even with an explicit `WHERE tenant_id = B`.

✅ *You've got it when:* you can demonstrate isolation at the database level, and you can explain
why connecting as the table owner would silently break it.

## Stage 4 — Documents + background ingestion (2 days)

**Goal:** upload a file and process it off-thread.
- `pip install celery redis minio pypdf`. Add MinIO (S3) for blobs.
- `POST /documents/upload` stores the file, inserts a `pending` Document, enqueues a Celery task.
- A Celery worker that downloads the file, extracts text, and (for now) just marks it
  `completed`. Poll status from the UI/curl.
- Get the worker connecting as the restricted role and setting tenant context (RLS reaches here).

✅ *You've got it when:* the upload returns instantly, the worker processes independently, and you
understand why it's a separate process.

## Stage 5 — Embeddings + retrieval (2 days, the core)

**Goal:** ask a question, get relevant chunks.
- Add `pgvector` to Postgres. Add an `embedding Vector(1536)` column to a `Chunk` table.
- In the worker: chunk the extracted text, call an embedding API per chunk, store chunk + vector.
- A retriever: embed the question, run a pgvector nearest-neighbor query (cosine), return top-k.
- `POST /query` that retrieves chunks and stuffs them into an LLM prompt → an answer.

✅ *You've got it when:* a question returns passages about the *meaning*, not just keyword
matches, and you can explain what an embedding is.

## Stage 6 — Make the RAG good (ongoing)

Now layer in the production-grade pieces *one at a time*, each as an opt-in flag, each with a
graceful fallback:
- **Hybrid search** — add a keyword pass (start with BM25/`rank-bm25` for simplicity; this repo now
  does it in-DB with Postgres full-text search) and fuse with RRF.
- **Re-ranking** — a cross-encoder over the top candidates.
- **Citations** — return only the sources the answer actually cited.
- **Streaming** — yield tokens over SSE.
- **Conversation memory** — persist turns, feed history back.
- Then the ambitious ones: **multi-hop**, **contextual compression**, **knowledge graph**.

✅ *You've got it when:* you can articulate *which failure mode* each addition fixes.

## Stage 7 — Agents (2 days)

**Goal:** multi-step tool use.
- A `ToolRegistry` (retrieval, calculator, ...). A ReAct loop that prompts the LLM, parses
  `Action`, runs the tool, feeds back the `Observation`.
- Add `delegate` with a depth cap.

## Stage 8 — Frontend (2-3 days)

**Goal:** a UI.
- `npm create vite` (React + TS). Auth pages, a collections page, a streaming chat page.
- axios with the token interceptor + refresh-on-401. TanStack Query for server data, Zustand for
  auth/theme.

## Stage 9 — Production hardening (ongoing — this is the real skill)

This is where "works on my machine" becomes "production-grade," and it's the most transferable
skill. Work through the categories in [`../PRODUCTION_ROADMAP.md`](../PRODUCTION_ROADMAP.md):
- **Security:** parameterized SQL, rate-limit auth, enforce upload limits, fail-fast on default
  secrets, the non-owner DB role.
- **Reliability:** timeouts + retries on every external call, graceful degradation, never swallow
  errors silently.
- **Ops:** CI (lint + typecheck + tests against a real DB), Prometheus metrics, correlated
  logging, non-root containers, a prod compose, a Helm chart.
- **Honesty:** make the README claim only what's actually built.

✅ *You've got it when:* you instinctively ask "what happens if this external call hangs?",
"can this leak across tenants?", "will this hang the worker?", "does a failure here vanish
silently?" — and you **verify the answers against the running system**, not just the code.

## What "I can build this" actually means

You don't need to memorize every library API — you'll look those up forever. Being able to build
this means you can **reason about the architecture**: where the trust boundaries are, why work is
split across processes, how data flows from a file to an answer, and what breaks under load or
attack. If you can sketch the [architecture diagram](../ARCHITECTURE.md) from memory and explain
*why* each box exists and *what would go wrong without it*, you can build it.

---

Prev: [Ops, Docker & deploy](11-ops-and-deploy.md) · Next: [The RAG glossary →](13-rag-glossary.md)
