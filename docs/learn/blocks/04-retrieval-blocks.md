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

**Changing the embedding model is a migration, not a config flip.** Stored vectors are only
comparable with vectors from the same model, and the column has a fixed dimension. Re-embed the
corpus (re-run ingestion) and check `DEFAULT_EMBEDDING_DIMENSIONS`.

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
