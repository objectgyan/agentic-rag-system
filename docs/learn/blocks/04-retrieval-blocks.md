# Block 4 — Retrieval: embeddings and re-rankers

**Files:** `backend/app/services/rag/embedder.py`, `retriever.py`, `ranking.py` ·
**Tests:** `tests/test_embedding_cache.py`, `tests/test_rerank.py`, `tests/test_ranking.py`,
`tests/test_retriever.py`, `tests/test_fusion.py`

Retrieval is covered conceptually in [07 — the RAG pipeline](../07-rag-pipeline.md). This page is
about the **interfaces** and how to swap implementations.

## Embeddings

`EmbeddingService` has two entry points on purpose:

```python
await embedder.embed_texts(chunks)      # documents, at ingestion time
await embedder.embed_query(question)    # queries, at search time (Redis-cached)
```

Many modern embedding models are **asymmetric**: they embed a short question and a long passage
differently (Cohere `input_type`, Voyage `input_type`, E5's `"query: "` / `"passage: "` prefixes,
BGE's query instruction). Embedding queries as documents silently lowers recall. The audit found
exactly that: Cohere queries were sent as `search_document`. Now they are `search_query`.

Two more fixes in this phase:

- Local sentence-transformers models were **re-loaded from disk on every call** (seconds each).
  They are now loaded once per process, and `encode()` runs off the event loop.
- Cohere moved to its v2 client (`AsyncClientV2`), batching at its 96-texts limit.

Local models get their **query/document prefixes** automatically (`app/core/embedding_models.py`):
BGE gets its search instruction on queries, E5 gets `query: ` / `passage: `.

### One embedding model per deployment

Queries are embedded with `DEFAULT_EMBEDDING_MODEL`, so every stored chunk must come from the same
model. The collections API therefore rejects a different per-collection `embedding_model` (422),
and ingestion always uses the deployment model. (Before this was enforced, a collection could be
ingested with one model and searched with another — vectors from different models are not
comparable, so dense search silently returned noise or failed on the size.)

### The vector column follows the model

`chunks.embedding` is `vector(N)`, and N must equal the model's output size. Three guards keep them
in step:

1. **Startup:** for known models, `DEFAULT_EMBEDDING_DIMENSIONS` must match
   (`config._embedding_dimensions_match_model`) — misconfiguration fails at boot, not at upload.
2. **Migration 007:** sizes the column to the config on a fresh install. If embeddings already
   exist at another size it **stops** with instructions instead of wiping data during startup.
3. **Ingestion:** a wrong-size vector fails the document with a clear message.

**Changing the model on a live database** is an explicit operator action
(`backend/app/cli/embeddings.py`, logic in `app/services/rag/vector_schema.py`):

```bash
docker compose exec backend python -m app.cli.embeddings status         # column vs config
# set DEFAULT_EMBEDDING_MODEL + DEFAULT_EMBEDDING_DIMENSIONS in .env, recreate the containers, then:
docker compose exec backend python -m app.cli.embeddings resize --yes   # clear + resize + rebuild index
docker compose exec backend python -m app.cli.embeddings reembed        # embed stored chunk text
```

`reembed` reuses the chunk text already in Postgres — no re-extraction, no re-upload. Dense search
returns nothing between `resize` and the end of `reembed`, so schedule it. Both commands connect as
the table owner (`DATABASE_SYNC_URL`), because DDL needs ownership and the owner bypasses RLS so
every tenant is covered. pgvector's HNSW index supports up to 2,000 dimensions; above that the
column works but search scans every row (the tool warns).

## Recipe — run without OpenAI (Claude + local models)

```env
DEFAULT_LLM_MODEL=claude-opus-5-5
DEFAULT_FAST_MODEL=claude-haiku-4-5
DEFAULT_EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
DEFAULT_EMBEDDING_DIMENSIONS=384
LOCAL_RERANKER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
```

Both local models (~220 MB together) download once into the `model-cache` volume (`HF_HOME=/models`).
Verified end-to-end: intent routing, retrieval ranking and re-ranking on the local models, answers on
Claude. For multilingual corpora use `intfloat/multilingual-e5-base` (768) instead.

## Re-rankers

One port, used at two levels:

```python
class Reranker(Protocol):
    async def rerank(self, query, chunks, top_k) -> List[RetrievedChunk]: ...
```

- **Semantic** re-ranking ("is it relevant?") — `HybridRetriever(..., reranker=...)`. With none
  injected, the built-in routing picks Cohere → local cross-encoder → no-op, fail-soft.
- **Business** re-ranking ("of the relevant ones, which do we surface?") —
  `MetadataBoostReranker`, applied by the `business_rerank` stage from the request's `boosts`.
  See [RERANKING.md](../../RERANKING.md).

## Recipe — swap in a different re-ranker

```python
class VoyageReranker:
    def __init__(self, client, model="rerank-2.5"):
        self.client, self.model = client, model

    async def rerank(self, query, chunks, top_k):
        res = await self.client.rerank(query=query, documents=[c.content for c in chunks],
                                       model=self.model, top_k=top_k)
        out = []
        for r in res.results:
            chunk = chunks[r.index]
            chunk.score = r.relevance_score
            out.append(chunk)
        return out

retriever = HybridRetriever(db, tenant_id, reranker=VoyageReranker(voyage_client))
```

(Check the vendor's current SDK for exact method names — the shape above is the pattern.)

## Choosing for a client — what to weigh

| Question | Why it matters |
|---|---|
| Language(s) of the corpus? | English-only models underperform on multilingual corpora; pick a multilingual embedder. |
| Domain jargon (legal, medical, code)? | Domain-tuned models and re-rankers can beat bigger general ones. |
| Data residency? | Hosted APIs vs local models (sentence-transformers, BGE, E5) on the client's hardware. |
| Latency budget? | A cross-encoder over 50 candidates costs ~100ms+ on CPU; a hosted re-ranker adds a network hop. |
| **What does the eval say?** | Decide with [the eval harness](../15-evaluation.md): recall@k / nDCG before and after. |

## Vocabulary

*asymmetric embeddings, input_type, embedding dimension, re-embedding migration, bi-encoder vs
cross-encoder, semantic vs business re-ranking, fail-soft.*
