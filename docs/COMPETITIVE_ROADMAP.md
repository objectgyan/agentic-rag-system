# Competitive Roadmap — from "strong project" to "production-competitive"

An honest gap analysis of AgentRAG against real-world, paid RAG products, plus a **sequenced
build plan** where each item doubles as a **learning checklist**. Companion to
[`PRODUCTION_ROADMAP.md`](PRODUCTION_ROADMAP.md) (which hardened the *platform*); this doc is about
making the product *good* and *yours*.

> **Verdict:** The platform is already close to production-grade — multi-tenancy with real
> Postgres RLS, hybrid retrieval + rerank, advanced RAG breadth, correct chat-session persistence,
> auth/billing/audit/streaming/CI. What separates it from products people pay for is **not the
> plumbing** (built) but the **quality loop** (measure → improve) and **scale**. Those are exactly
> the parts most portfolio projects skip — and where an FDE earns their keep.

---

## Where we stand — honest scorecard

| Dimension | State | Notes |
|---|---|---|
| Multi-tenancy & isolation | 🟢 Strong | Postgres RLS via `set_tenant_context` — enforced at the DB, not faked in `WHERE`. Rare and real. |
| Retrieval | 🟢 Strong | Dense + BM25 + RRF + cross-encoder rerank. Ahead of many "dense-only" commercial products. |
| Advanced RAG | 🟢 Strong | HyDE, multi-query, decomposition, multi-hop, compression, knowledge-graph, self-eval. |
| Auth / SaaS scaffolding | 🟢 Strong | JWT+refresh, API keys, RBAC, per-tier rate limits, usage records, audit log. |
| Chat-session correctness | 🟢 Good | Conversations authorized (tenant+user), history persisted, replayed to the generator. |
| **Evaluation / quality loop** | 🔴 **Gap** | Per-query LLM judge exists, but **no offline eval harness / regression suite**. Flying blind on "did my change help?" |
| Conversation context mgmt | 🟢 Addressed | Item ② done: recent window + token budget + running summary of older turns (`conversation_memory.py`). Semantic-memory retrieval still a stretch. |
| Observability / tracing | 🟢 Addressed | Item ③ done: per-query `QueryTrace` (stage latencies, chunk ids+scores, tokens, cost) on `trace=true` + logged every query; cost persisted to `UsageRecord`. OTel export still a stretch. |
| Scale (vectors / cache) | 🟡 Partly | HNSW ANN index verified; query **embedding cache** added (item ⑤). BM25 still loads ≤1000 chunks into Python — the one remaining scale item (move to in-DB `tsvector`), deferred as an eval-gated change since the corpus is still small. |
| **Prompt-injection / output safety** | 🔴 **Gap** | RAG is a huge injection surface; no guardrail/moderation/PII layer. |
| Caching | 🟢 Addressed | Query **embedding cache** in Redis (item ⑤), fail-soft. Semantic *answer* cache still a future win. |

🟢 = competitive · 🟡 = works but limited · 🔴 = missing, and it matters

---

## The sequenced plan

Ordered by leverage. Each item states **why**, **what to build**, **acceptance criteria** (tick
these off — that's how you know it's done), and **what you'll learn** (the vocabulary to talk the
AI language with technical customers).

### ① Evaluation harness  ← *start here, in progress*

**Why:** You cannot improve what you cannot measure. Every later change (rerankers, chunking,
context management) needs a number that says "better or worse." This is *the* skill that separates
hobby RAG from production RAG — real teams live in their eval dashboard.

**What to build:**
- A **golden dataset** — human-labeled `(question → relevant chunk/doc ids, ideal answer)` cases.
- **Retrieval metrics** — `recall@k`, `precision@k`, `MRR`, `hit@k`, `nDCG@k` (pure, unit-tested).
- **Generation metrics** — reuse `RAGEvaluator` (faithfulness, relevance, precision, completeness).
- A **runner** that scores a dataset and emits an aggregate report (JSON for regression diffing).
- A **CLI**: `python -m app.services.rag.evaluation <dataset.json>`.

**Acceptance criteria:**
- [x] Pure metric functions (`metrics.py`) with unit tests that run in CI — 25 checks, verified green.
- [x] Golden dataset format (`dataset.py`) + a worked example (`datasets/example.json`).
- [x] Runner (`runner.py`) produces per-case + aggregate scores against a live tenant — **verified end-to-end** against seeded data (retrieval + `gpt-4o-mini` generation + LLM judge, 0 errors).
- [x] `report.to_dict()` persists to JSON; CLI `--min-recall` / `--min-faithfulness` fail on regression.
- [ ] Documented in the end-to-end tutorial with the metric definitions.

**Vocabulary you'll own:** *golden set, relevance judgments, recall@k vs precision@k, MRR, nDCG,
faithfulness/groundedness, hallucination rate, LLM-as-judge, regression gating, offline vs online eval.*

### ② Conversation context management

**Why:** `_load_conversation_history` loads **all** turns every message (`query.py`). A long chat
blows the context window, inflates cost, and degrades quality. This is the concrete weakness in
"logical chat sessions."

**What to build:**
- **Sliding window** — keep the last *N* turns verbatim.
- **Running summary** — a rolling LLM summary of older turns, prepended as system context.
- (Stretch) **Semantic memory** — embed past turns; retrieve only the relevant ones.
- **Token budgeting** — cap total context tokens with `tiktoken`.

**Acceptance criteria:**
- [x] History passed to the generator is bounded by a configurable token budget (`conversation_token_budget` + `conversation_recent_messages`) — `conversation_memory.py`, wired into `pipeline.query` + `query_stream`.
- [x] Old turns are summarized (running summary via `default_compression_model`), not dropped silently; fail-soft.
- [x] A test proves a 50-message conversation stays bounded (`test_fifty_turn_conversation_stays_bounded`); verified end-to-end that the summary retains an early fact.
- [ ] *(stretch)* Semantic memory — embed past turns and retrieve only the relevant ones.

**Status: ② core complete** (9 tests; full suite 119 pass). Stretch (semantic memory) deferred.

**Vocabulary:** *context window, token budget, sliding-window memory, conversation summarization,
episodic vs semantic memory, memory retrieval.*

### ③ Observability & tracing

**Why:** When a customer says "it answered wrong," you must *see* the pipeline: which chunks, what
scores, latency per stage, tokens/cost. Metrics tell you *that* it's slow; traces tell you *why*.

**What to build:**
- **Structured per-query trace** — a span tree (retrieve → rerank → generate) with timings, chunk
  ids + scores, token counts.
- Export via **OpenTelemetry**, or integrate **Langfuse/Phoenix**.
- A per-query cost estimate (tokens × model price).

**Acceptance criteria:**
- [x] Every query emits a trace with per-stage latency and the retrieved chunk ids+scores (`tracing.py` `QueryTrace`; `trace=true` attaches it, and a summary logs on every query).
- [x] Token + cost recorded per query — `estimate_cost` + `MODEL_PRICING`; cost persisted to `UsageRecord.cost_usd`.
- [x] Can pull up one request end-to-end (verified: `retrieve`/`generate` spans, chunk scores, tokens, cost).
- [ ] *(stretch)* Export to OpenTelemetry / Langfuse / Phoenix.

**Status: ③ core complete** (10 tracing tests; full suite 129 pass; verified end-to-end).

**Vocabulary:** *span/trace, observability vs monitoring, OpenTelemetry, token accounting,
p50/p95 latency, cost-per-query.*

### ④ Customization layer — intent routing + business re-ranking  ← *your FDE wedge*

**Why:** Generic RAG is commoditizing. Value is in *customizable, deployed-at-the-customer* RAG —
the FDE job. This is where the [`RERANKING.md`](RERANKING.md) work lands.

**What to build:**
- **Intent router** — classify the query (product-search / comparison / chit-chat) and branch
  (skip retrieval for chit-chat). Built as a cross-encoder-argmax classifier (reuses the reranker).
- **Metadata filtering** — restrict retrieval by structured fields before ranking.
- **Business re-ranking** — the `ProductReranker`: blend relevance + manufacturer + context +
  popularity; soft boosts over hard filters; config-driven weights.

**Acceptance criteria:**
- [x] Router picks intent and changes the path — `intent.py` (embedding argmax); `use_routing` makes chit-chat skip retrieval. Verified with real embeddings.
- [x] A pluggable re-ranker interface (`ranking.py` `Reranker` protocol) so a customer-specific ranker drops in.
- [x] Business signals blended with normalized weights (`MetadataBoostReranker`); `test_categorical_boost_reorders` shows a manufacturer boost reordering past a more-relevant chunk. `boosts` exposed on the query API; retriever now populates chunk metadata.
- [ ] *(stretch)* Learning-to-rank model behind the same interface once click data exists.

**Status: ④ core complete** (15 tests across intent + ranking; full suite 144 pass; verified end-to-end).

**Vocabulary:** *query intent / routing, zero-shot classification, metadata filtering,
learning-to-rank (LambdaMART), signal fusion, boosting vs filtering, personalization.*

### ⑤ Scale hardening

**Why:** Do this when a corpus outgrows the demo. Premature otherwise.

**What to build:**
- Move **BM25 out of Python** → Postgres `tsvector` / ParadeDB (or OpenSearch).
- Confirm/'add **HNSW index** on the pgvector column (not a sequential scan).
- **Semantic cache** (repeated queries) + **embedding cache**.
- Batch/concurrent ingestion tuning.

**Acceptance criteria:**
- [x] Vector search uses an ANN index — **HNSW verified** (`ix_chunks_embedding USING hnsw (embedding vector_cosine_ops)` via `pg_indexes`).
- [x] Cache hit path cuts the embedding call for repeated queries — `EmbeddingService` Redis cache (`embedding_cache_enabled`), fail-soft; verified end-to-end against real Redis.
- [ ] **Deferred:** move sparse (BM25) search into the DB (`tsvector` generated column + GIN, or ParadeDB). It's the last scale item; deliberately deferred because (a) the corpus is still small and (b) it changes retrieval semantics, so it warrants an **eval-gated** rollout using item ①'s harness (compare recall@k / MRR before vs after) with a human check — not an unvalidated autonomous push.
- [ ] *(stretch)* Semantic *answer* cache; batch/concurrent ingestion tuning.

**Status: ⑤ partly complete** — HNSW verified, embedding cache done (5 tests). Sparse-in-DB deferred (plan above).

**Vocabulary:** *ANN / HNSW / IVFFlat, inverted index, tsvector/BM25 in-DB, semantic caching,
recall/latency trade-off, sharding.*

---

## Competitive positioning (read before you "compete")

Don't fight horizontal RAG SaaS (Glean, Vectara) head-on — you'll lose on distribution. Your wedge
is your own framing: **custom solutions like an FDE**. The architecture (multi-tenant + agentic +
pluggable retrieval/rerank) is *built* for rapid per-customer customization. So the strategy is:

1. Nail the **quality loop** (①) so you can prove improvements with numbers to a customer.
2. Build the **customization layer** (④) so "make it do *this* customer's thing" is a config/plugin,
   not a rewrite.
3. Keep everything **debuggable** (③) so you can support it in production.

That trio — measurable, customizable, debuggable — *is* the FDE value proposition.

---

## Progress log

- **2026-07-05** — Roadmap captured. Built ① eval harness: `app/services/rag/evaluation/`
  (`metrics`, `dataset`, `runner`, CLI) + `tests/test_eval_metrics.py`. Unit tests green in-container
  (14 passed). **Ran end-to-end** against a seeded tenant (single-doc, so retrieval trivially 1.0;
  generation faithfulness/completeness 1.0, answers correctly cited).
- **2026-07-05** — **Item ⑤ partly done** — HNSW ANN index verified; query embedding cache in Redis
  (`embedder.py`, fail-soft, 5 tests, verified vs real Redis). BM25→in-DB `tsvector` deliberately
  deferred as an eval-gated change (corpus still small). Full suite 149 pass.
- **2026-07-05** — **Item ④ done** — customization layer. ④a intent routing (`intent.py`,
  embedding-argmax zero-shot; `use_routing` skips retrieval for chit-chat). ④b pluggable business
  re-ranking (`ranking.py` `Reranker` + `MetadataBoostReranker`: normalized relevance + weighted
  metadata boosts); retriever now populates chunk metadata; `boosts` on the query API. 15 tests,
  full suite 144 pass, verified end-to-end.
- **2026-07-05** — **Item ③ done** — per-query tracing (`tracing.py`): `QueryTrace` with
  `retrieve`/`generate` spans, chunk ids+scores, token split, and `estimate_cost` (persisted to
  `UsageRecord.cost_usd`); `trace=true` on the query API attaches it, a summary logs every query.
  10 tests, full suite 129 pass, verified end-to-end.
- **2026-07-05** — Wrote the tutorial (docs/learn/13–15). Built a **multi-document** eval fixture and
  ran it: metrics now move (`recall@5`/`mrr` 1.0 but `precision@5` 0.23, and a brittle
  `answer_must_contain` false-negative caught by the LLM judge). **Item ② done** — conversation memory
  (`conversation_memory.py`): recent window + token budget + running summary, wired into the pipeline,
  9 tests, full suite 119 pass, verified end-to-end (summary retains early facts).

See also: [`RERANKING.md`](RERANKING.md) · [Learning Guide](learn/README.md) ·
[`PRODUCTION_ROADMAP.md`](PRODUCTION_ROADMAP.md)
