# Production-Grade Roadmap

This document is the plan of record for taking AgentRAG from "impressive demo" to "production grade."
It is written for learning: every item explains the **problem** (with evidence), the **production
lesson** behind it, the **fix**, and how to **verify** it. Each item has a stable ID (e.g. `F1`) so
commits can reference it (`git commit -m "F1: enforce RLS via non-owner app role"`).

Status legend: ⬜ not started · 🟦 in progress · ✅ done · ⏭️ deferred

---

## Guiding principles

1. **Correctness before features.** A multi-tenant system that can leak data is not "90% done," it is
   unshippable. We fix the isolation foundation before building anything on top of it.
2. **Defense in depth.** Tenant isolation should hold at *two* layers: Postgres RLS (so a bug in app
   code can't leak data) *and* explicit `tenant_id`/ownership filters in queries. Today only the second
   layer actually works.
3. **Fail loud in dev, degrade gracefully in prod — but never silently.** Every swallowed exception is
   a future 3am incident with no logs.
4. **Verify with the real stack.** You run Docker locally, so each item ends with a concrete command or
   test that proves it works (or proves the old behavior was broken).

---

## Audit summary (verified against source)

The initial multi-agent audit was useful but had two **false criticals** that I verified and corrected —
worth internalizing, because both are classic RAG/Postgres traps:

- **"No vector index" — FALSE.** `migrations/versions/001_initial.py:150` already creates an HNSW index
  (`USING hnsw (embedding vector_cosine_ops)`). The audit only read `init.sql` (extensions only) and
  missed the migration.
- **"RLS fails open when context unset" — FALSE (it fails *closed*).** `current_setting('app.current_tenant', true)`
  returns `NULL` when unset, so `tenant_id::text = NULL` → `NULL` → row hidden. Deny-by-default. Safe.

The real keystone issue the audit *missed*:

- **RLS is currently inert.** The app connects to Postgres as `agentrag`, which **owns** the tables.
  Postgres **exempts table owners from RLS** unless `FORCE ROW LEVEL SECURITY` is also set. So every RLS
  policy in the migration is dead weight right now — isolation depends *entirely* on the app remembering
  to add `tenant_id = ...` to every query. That is the bug class this whole phase exists to close.

What's genuinely solid (do **not** rewrite): hybrid retrieval (dense + BM25 + RRF), HyDE/multi-query,
the five chunking strategies, and citation filtering. These are real and well-built.

---

## Phase 0 — Foundation (this pass)

Ordered by dependency. Items 0.1 are safe and land first; the RLS enforcement (0.3) is the riskiest and
lands only after the pieces it depends on are in place and tested.

**Progress:** ✅ 0.1 (F1–F3) · ✅ 0.2 (F4–F8) · ✅ 0.3 (F9–F10) · ✅ 0.4 (F11–F13). **Phase 0 COMPLETE.**
52 tests passing.

**RLS is now real — validated live.** App + worker connect as the non-owner `agentrag_app` role;
direct-DB proof showed `ctx=A` sees only A, an explicit `WHERE tenant=B` returns 0, no-context returns
0, and a foreign-tenant INSERT raises `InsufficientPrivilegeError`. Full pipeline (register → login →
upload → worker ingest → RAG query with citation) works under the restricted role.

> **Learning note (corrected during F9):** `FORCE ROW LEVEL SECURITY` does **not** constrain a *superuser*
> table owner — superusers always bypass RLS (`rolbypassrls`). Our migration role `agentrag` is a
> superuser, so it still sees everything; that's fine because it's used *only* for DDL. The actual
> guarantee comes from running the app as a **separate non-owner, non-superuser role** (`agentrag_app`),
> which makes the existing `ENABLE` policies apply. `FORCE` is kept as defense-in-depth for the
> non-superuser-owner case, not as the primary control.

### 0.1 — Safe correctness fixes (no behavior risk) — ✅ DONE

#### F1 — Parameterize `set_tenant_context` (kill the f-string SQL)
- **Problem:** `app/core/database.py:35` runs `sa_text(f"SET app.current_tenant = '{tenant_id}'")`.
  String-interpolated SQL. `tenant_id` is a UUID from a signed JWT today, so not *currently* exploitable,
  but it is the exact pattern that becomes a breach the day the value's provenance changes.
- **Lesson:** Never interpolate into SQL, even "trusted" values. Postgres `SET` can't take bind params,
  but `set_config(name, value, is_local)` can.
- **Fix:** `SELECT set_config('app.current_tenant', :tid, false)` with a bound parameter; validate `tid`
  parses as a UUID first and raise on failure.
- **Verify:** Unit test passing `'; DROP TABLE users;--` as tenant_id raises a validation error instead
  of generating SQL.

#### F2 — Explicit access-control checks on collection-scoped endpoints
- **Problem:** `app/api/v1/query.py` (query + create_conversation) passes caller-supplied `collection_ids`
  / `collection_id` straight through with no check that those collections exist in the caller's tenant or
  are visible to them. Same gap on collection update (ownership of `private` collections not checked).
- **Lesson:** RLS is the safety net, not the gate. The app should still validate authorization explicitly
  so denials are 403s with clear errors, not silent empty results — and so isolation survives even if RLS
  is ever misconfigured.
- **Fix:** A small `assert_collections_accessible(db, user, collection_ids)` helper used by query,
  streaming query, conversation create, and agent execute. Enforce `visibility`/`owner_id` rules for
  `private`.
- **Verify:** Request another tenant's collection ID → 403/404, not an empty answer.

#### F3 — Give `messages` a `tenant_id` and an RLS policy
- **Problem:** `messages` has no `tenant_id` column and is excluded from the RLS loop
  (`001_initial.py:236`). It's isolated only transitively via `conversation_id`. Once RLS is enforced
  (0.3), a direct `messages` query would be unprotected.
- **Fix:** New migration `002_*`: add `messages.tenant_id` (backfill from parent conversation), index it,
  enable + (force) RLS with the standard tenant policy. Set `tenant_id` on message insert in the app.
- **Verify:** With RLS enforced, querying `messages` as tenant A returns zero of tenant B's rows.

### 0.2 — Security hardening — ✅ DONE

#### F4 — Parameterize the pgvector dense search
- **Problem:** `app/services/rag/retriever.py:78-86` builds the embedding literal by string-joining floats
  into the SQL (`'[...]'::vector`). Low exploitability (numeric data), but same anti-pattern as F1 and it
  defeats statement caching.
- **Fix:** Bind the vector as a parameter (pgvector's SQLAlchemy types support this), or pass via
  `:embedding` param cast to `::vector`.
- **Verify:** Retrieval still returns identical top-k on a known corpus; query plan uses the HNSW index.

#### F5 — Rate-limit unauthenticated auth endpoints (brute-force protection)
- **Problem:** `app/middleware/rate_limiter.py` keys limits on `tenant_id`, which is `None` before login,
  so `/auth/login` and `/auth/register` are effectively unlimited. Password brute force is wide open.
- **Lesson:** Pre-auth endpoints must be limited by something you have pre-auth — client IP — and more
  strictly than authenticated traffic.
- **Fix:** IP-keyed sliding-window limit specifically for `/api/v1/auth/*` (e.g. 5–10/min/IP), honoring
  `X-Forwarded-For` when behind the nginx proxy. Return `429` with `Retry-After`.
- **Verify:** Loop 20 logins from one IP → `429` after the threshold.

#### F6 — O(1) API-key validation
- **Problem:** `app/api/deps/auth.py:59-64` loads *all* active API keys and bcrypt-verifies each. The
  schema already has an indexed `key_prefix` column — it's just unused. With N keys this is N bcrypt
  hashes per request (DoS + timing surface).
- **Fix:** Issue keys as `prefix.secret`; store `key_prefix`; look up the single row by prefix, then one
  bcrypt verify. Constant work per request.
- **Verify:** Seed 1k keys; assert auth does exactly one bcrypt verify (timing/log assertion).

#### F7 — Enforce upload size limit
- **Problem:** `max_upload_size_mb=100` is defined but never checked; `documents.py` does
  `await file.read()` into memory with no cap. One large upload OOMs the worker/api.
- **Fix:** Reject on `Content-Length` over the limit; stream the body with a hard byte cap; also set
  uvicorn/nginx body-size limits as a backstop.
- **Verify:** Upload a 200MB file → `413 Payload Too Large`, memory stays flat.

#### F8 — Fail-fast config validation for secrets
- **Problem:** Defaults like `jwt_secret="change-me-..."` and `minio…="minioadmin"` will silently run in
  prod if `.env` is incomplete.
- **Fix:** Pydantic validator: when `app_env != "development"`, refuse to boot if any secret is still its
  default or too short. Loud failure at startup beats a quiet vulnerability.
- **Verify:** `APP_ENV=production` with default JWT secret → process exits with a clear error.

### 0.3 — Make RLS real (the keystone) — ✅ DONE

#### F9 — Dedicated non-owner DB role + `FORCE ROW LEVEL SECURITY`
- **Problem:** As above — the app runs as the table owner, so RLS is bypassed entirely.
- **Lesson:** RLS only protects you if the connecting role is (a) **not** the table owner/superuser and
  (b) subject to `FORCE ROW LEVEL SECURITY`. This is the single most important multi-tenant Postgres fact.
- **Fix:**
  - Migration/bootstrap: create role `agentrag_app` with `LOGIN`, granted `SELECT/INSERT/UPDATE/DELETE`
    on app tables but **owning nothing**; `ALTER TABLE … FORCE ROW LEVEL SECURITY` on every tenant table.
  - Alembic/DDL keeps running as the owner `agentrag`; the **app and Celery** connect as `agentrag_app`
    (new `DATABASE_URL` in docker-compose).
- **Risk:** High. If any session forgets to `set_config('app.current_tenant', …)`, it now sees **zero**
  rows (fail-closed) — which will surface immediately in tests, by design.
- **Discovered context-setting gaps (must fix as part of F9, else these break under FORCE RLS):** the
  RLS policies have only a `USING` clause, so Postgres copies it to `WITH CHECK` — meaning **INSERTs**
  into RLS tables also require the GUC to match. `login`/`register` insert `audit_logs` *before* any
  tenant context is set (they don't depend on `get_current_user`), so they must call
  `set_tenant_context` after loading the user/tenant. Reads/writes on `users`/`tenants` are safe (those
  tables have no RLS). The Celery worker's chunk INSERTs are covered by F10.
- **Migration/app split:** the backend container runs *both* `alembic upgrade head` and the app. Alembic
  uses `DATABASE_SYNC_URL` (keep as owner `agentrag` — DDL needs ownership); the app/worker use
  `DATABASE_URL` (point at the restricted `agentrag_app`). This cleanly separates migration privileges
  from runtime privileges without a second container.
- **Verification is non-destructive:** migration 004 creates the role + grants + FORCE RLS incrementally
  on the existing DB; we recreate the backend/worker containers (not `down -v`) and test
  register → login → upload → query live. No local data is destroyed.
- **Verify (the money test):** Integration test using the `agentrag_app` role — set tenant A, insert/read;
  switch to tenant B, confirm A's rows are invisible; unset context, confirm zero rows.

#### F10 — Set tenant context in Celery workers
- **Problem:** `app/services/processing/tasks.py` opens its own session and never sets tenant context.
  Harmless today (RLS inert), **breaks ingestion** the moment F9 lands (worker would see zero rows).
- **Fix:** After loading the document's `tenant_id`, call the parameterized `set_config` on the worker
  session before any tenant-scoped read/write. (Workers run as `agentrag_app` too.)
- **Verify:** End-to-end upload → processed `completed` with chunks, under the enforced-RLS role.

### 0.4 — Reliability (resilience of external calls) — ✅ DONE

#### F11 — Timeouts + retries on every external API call
- **Problem:** `embedder.py`, `generator.py`, `query_enhancer.py`, and the vision/rerank paths call
  OpenAI/Anthropic/Cohere with **no timeout and no retry**. One slow/blipping upstream hangs a request or
  fails a whole ingestion. `tenacity` is already a dependency and unused.
- **Lesson:** Every network call needs a timeout (so you fail fast) *and* bounded retry with exponential
  backoff + jitter (so transient blips self-heal). The two are complementary.
- **Fix:** A thin client factory that sets explicit timeouts and wraps calls in `tenacity` retry
  (retry on timeout/5xx/rate-limit, cap attempts, jittered backoff). Apply across all three services.
- **Verify:** Inject a failing/slow mock client; assert it retries N times then raises a typed error
  (not a hang).

#### F12 — Replace silent `except: pass` with logged handling + graceful degradation
- **Problem:** `main.py:21` swallows MinIO bucket init; `retriever.py:193` swallows rerank failures;
  storage delete swallows errors. Failures vanish.
- **Fix:** Log every caught exception with context (tenant/doc/operation). Where degradation is
  *intended* (e.g. rerank down → fall back to fused order), log a warning **and** flag it in the response
  (`"degraded": ["reranking"]`) so it's observable. Where it's not (bucket missing at boot), fail loudly.
- **Verify:** Kill Cohere key → query still answers, logs a warning, response notes degraded reranking.

#### F13 — Celery retry semantics + engine cleanup
- **Problem:** Retries exist (`max_retries=3`) but with fixed delay and the per-task engine is only
  disposed on the success path; the failure path can leak connections.
- **Fix:** Exponential backoff with jitter; `engine.dispose()` in a `finally`; mark terminal failures
  distinctly from retryable ones.
- **Verify:** Force an extractor error; observe backoff in worker logs and no connection-pool growth.

### Phase 0 exit criteria — ✅ ALL MET
- ✅ Isolation integration test passes under the `agentrag_app` (non-owner) role (2 tests, skip w/o DB).
- ✅ End-to-end upload→process→query works under that role (validated live, returns a cited answer).
- ✅ Brute-force (429 after 10/min/IP), oversized-upload (413), and default-secret-boot are all blocked.
- ✅ External-call failures retry/timeout (30s + 3 retries) and degrade observably (`QueryResponse.degraded`,
  logged warnings) instead of hanging or vanishing.
- ✅ A `tests/` suite (52 tests) covers the above; RLS integration tests run against the live DB. Broader
  coverage (CI, ruff/mypy, more units) is Phase 2.

**Done in 14 commits on `production-hardening`, each tracing to an item ID. Ready for Phase 1.**

---

## Later phases (sketch — detailed when we reach them)

### Phase 1 — Complete the half-built features — ✅ DONE

All four wired and validated live (conversation memory recalls across turns; `/agents/types` only lists
real tools; `evaluate=true` returns RAG scores; web search is Tavily-or-honestly-unconfigured). 64 tests.

#### C1 — Wire conversation memory into generation
- **Problem:** `/query` is fully stateless. The `Message` model is never written; `ChatRequest` is
  imported but unused; `generator.generate` accepts `conversation_history` but `pipeline.query` never
  passes it. So multi-turn chat has no memory.
- **Fix:** add optional `conversation_id` to `QueryRequest`. When present: authorize the conversation
  (tenant+user), load prior `messages` as history, pass to the pipeline → generator, and persist the new
  user + assistant messages (with citations and `tenant_id`, exercising the F3 RLS column). Update the
  conversation's `updated_at`.
- **Verify:** create a conversation, ask "My name is X", then "What's my name?" in the same
  conversation → the second answer reflects the first turn; messages rows exist with the right tenant_id.

#### C2 — Make `/agents/types` reflect the real tool registry
- **Problem:** `/agents/types` advertises a `code_execution` tool that has no handler in `ToolRegistry`.
  The orchestrator only ever shows the LLM the registry's real tools, so the model never emits it — but
  the API still lies about capabilities (and `code_execution` would just return "Unknown tool").
- **Fix:** derive the advertised tool lists from the registry's actual tools; drop the phantom
  `code_execution`. (Real sandboxed code execution needs container isolation — out of scope; don't ship
  an unsafe `exec`.)
- **Verify:** `/agents/types` lists only tools that exist in `ToolRegistry.execute_tool`.

#### C3 — Expose the dead `RAGEvaluator`
- **Problem:** `RAGEvaluator` (faithfulness/relevance/precision/completeness) is fully implemented but
  never instantiated anywhere.
- **Fix:** an optional `evaluate=true` on a query (or a dedicated `/query/evaluate` endpoint) that runs
  the evaluator over the produced answer + retrieved contexts and returns the scores.
- **Verify:** a query with evaluation returns metric scores in [0,1].

#### C4 — Honest web search tool
- **Problem:** the `web_search` tool returns a hardcoded "integration pending" string while being
  advertised as a real capability.
- **Fix:** integrate a real provider when a key is configured (e.g. Tavily/SerpAPI via
  `web_search_api_key`); when no key is set, return a clear "web search not configured" result and omit
  the tool from the advertised list rather than pretending.
- **Verify:** with no key, the tool/advertisement reflects unavailability; with a key, it returns live
  results.

### Phase 2 — Ops & quality gates — ✅ DONE (73 tests, CI green)
- ✅ **O1** GitHub Actions CI (lint job: ruff; test job: pgvector+redis services, runs migrations + pytest
  --cov). Added `backend/pyproject.toml` (ruff) and fixed all lint findings.
- ✅ **O2** Custom Prometheus metrics (`rag_queries_total{status}`, `rag_retrieval_seconds`,
  `rag_generation_seconds`) on the query path; verified on `/metrics/`. (Worker exporter = follow-up.)
- ✅ **O3** Structured logging on stdlib with request-ID + tenant correlation (contextvars + filter +
  pure-ASGI middleware; X-Request-ID echoed). Verified: `[req=… tenant=…]` correlated across a request.
- ✅ **O4** Production Docker: non-root backend (`appuser`) + frontend (nginx-unprivileged), `.dockerignore`
  for both, standalone `docker-compose.prod.yml` (no `--reload`, restart policies, celery healthcheck,
  resource limits, internal-only datastores, `APP_ENV=production`). (Helm still not built — README should be
  corrected to drop that claim, or add charts; tracked for later.)
- ✅ **O5** Broadened coverage: RBAC matrix (admin/member/viewer) and RRF fusion, on top of existing
  chunker/auth/etc. tests. 73 tests total.

**Remaining known follow-ups (small, non-blocking):** worker metrics exporter (separate process), Helm
charts (README now honestly marks these as roadmap), and a deeper mypy pass.

### Phase 3 — The ambitious net-new features (the "AGI-adjacent" learning) — in progress
- ✅ **A1** Multi-hop / recursive retrieval (`use_multi_hop`): iterative retrieve→reason→retrieve, adaptive
  (0 hops when the first pass suffices), follow-ups surfaced in `QueryResponse.hops`. Verified live.
- ✅ **A2** Contextual compression (`use_compression`): a cheap model distills each chunk to query-relevant
  content and drops irrelevant chunks; fail-safe. Verified live (filler stripped, fact kept).
- ⬜ **A3** Knowledge-graph retrieval (entity/relation extraction + graph-augmented retrieval) — biggest lift.
- ⬜ **A4** Real multi-agent delegation (agents calling agents, not just different prompts).
- ⬜ **A5** Honest README pass for any remaining unimplemented claims.

**Bonus reliability fixes found while testing A1:** chunker infinite-loop when `chunk_overlap >= chunk_size`
(hung the worker — a DoS via collection config), and per-task event-loop wedging in the Celery worker
(`run_async` now reuses one loop per process). Both fixed with tests.

---

## Working agreement
- I implement Phase 0 in small, explained commits, each referencing its item ID.
- After each item (or small group), I'll give you the exact Docker/test commands to verify, and we
  confirm before moving on.
- We don't start Phase 1 until the Phase 0 exit criteria are green.
