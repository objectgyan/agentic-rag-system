"""Hybrid retriever: dense vector search + BM25 sparse search + re-ranking."""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.rag.embedder import EmbeddingService

logger = logging.getLogger(__name__)

# Cache the local cross-encoder per process — loading it pulls torch and is expensive.
_local_reranker = None


def _get_local_reranker(model_name: str):
    global _local_reranker
    if _local_reranker is None:
        from sentence_transformers import CrossEncoder

        _local_reranker = CrossEncoder(model_name)
    return _local_reranker


@dataclass
class RetrievedChunk:
    chunk_id: str
    document_id: str
    content: str
    score: float
    page_number: Optional[int] = None
    section_title: Optional[str] = None
    metadata: Optional[dict] = None


class HybridRetriever:
    """Combines dense vector search, BM25 sparse search, and cross-encoder re-ranking."""

    def __init__(
        self,
        db: AsyncSession,
        tenant_id: str,
        embedding_service: Optional[EmbeddingService] = None,
    ):
        self.db = db
        self.tenant_id = tenant_id
        self.embedder = embedding_service or EmbeddingService()

    async def retrieve(
        self,
        query: str,
        collection_ids: Optional[List[str]] = None,
        top_k: int = 5,
        use_reranking: bool = True,
        dense_weight: float = 0.7,
        sparse_weight: float = 0.3,
    ) -> List[RetrievedChunk]:
        """Hybrid retrieval with fusion scoring."""
        # Get candidates from both methods
        dense_results = await self._dense_search(query, collection_ids, top_k=top_k * 3)
        sparse_results = await self._sparse_search(query, collection_ids, top_k=top_k * 3)

        # Reciprocal Rank Fusion
        fused = self._rrf_fusion(dense_results, sparse_results, dense_weight, sparse_weight)

        # Re-rank if enabled
        if use_reranking and fused:
            fused = await self._rerank(query, fused, top_k)
        else:
            fused = fused[:top_k]

        return fused

    async def _dense_search(
        self, query: str, collection_ids: Optional[List[str]], top_k: int
    ) -> List[RetrievedChunk]:
        """Vector similarity search using pgvector."""
        query_embedding = await self.embedder.embed_query(query)

        # Build query with pgvector cosine distance
        conditions = ["c.tenant_id = :tenant_id", "c.embedding IS NOT NULL"]
        params: dict = {"tenant_id": self.tenant_id, "top_k": top_k}

        if collection_ids:
            conditions.append("c.collection_id = ANY(:collection_ids)")
            params["collection_ids"] = collection_ids

        where_clause = " AND ".join(conditions)
        # Bind the embedding as a parameter (pgvector accepts the text form
        # '[1,2,3]' cast to ::vector) instead of interpolating it into the SQL (F4).
        # Low exploitability since the values are numeric, but it removes the
        # injection pattern and lets Postgres reuse the prepared statement.
        params["embedding"] = "[" + ",".join(str(x) for x in query_embedding) + "]"

        sql = f"""
            SELECT c.id, c.document_id, c.content, c.page_number, c.section_title,
                   c.metadata_extra,
                   1 - (c.embedding <=> (:embedding)::vector) as score
            FROM chunks c
            WHERE {where_clause}
            ORDER BY c.embedding <=> (:embedding)::vector
            LIMIT :top_k
        """

        result = await self.db.execute(sa_text(sql), params)
        rows = result.fetchall()

        return [
            RetrievedChunk(
                chunk_id=str(row[0]),
                document_id=str(row[1]),
                content=row[2],
                score=float(row[6]) if row[6] else 0.0,
                page_number=row[3],
                section_title=row[4],
                metadata=row[5],
            )
            for row in rows
        ]

    async def _sparse_search(
        self, query: str, collection_ids: Optional[List[str]], top_k: int
    ) -> List[RetrievedChunk]:
        """Sparse keyword retrieval via Postgres full-text search.

        Ranks chunks with ``ts_rank`` over the GIN-indexed ``content_tsv`` column (migration 006),
        instead of loading up to 1000 rows and scoring BM25 in-process. ``websearch_to_tsquery``
        parses the user's words into a tsquery (tolerant of stopwords / punctuation); a query that
        reduces to nothing simply matches nothing and sparse contributes zero to the fusion.
        """
        conditions = ["c.tenant_id = :tenant_id", "c.content_tsv @@ q.tsq"]
        params: dict = {"tenant_id": self.tenant_id, "query": query, "top_k": top_k}
        if collection_ids:
            conditions.append("c.collection_id = ANY(:collection_ids)")
            params["collection_ids"] = collection_ids

        where_clause = " AND ".join(conditions)
        sql = f"""
            WITH q AS (SELECT websearch_to_tsquery('english', :query) AS tsq)
            SELECT c.id, c.document_id, c.content, c.page_number, c.section_title,
                   c.metadata_extra, ts_rank(c.content_tsv, q.tsq) AS score
            FROM chunks c, q
            WHERE {where_clause}
            ORDER BY score DESC
            LIMIT :top_k
        """

        result = await self.db.execute(sa_text(sql), params)
        rows = result.fetchall()

        return [
            RetrievedChunk(
                chunk_id=str(row[0]),
                document_id=str(row[1]),
                content=row[2],
                score=float(row[6]) if row[6] else 0.0,
                page_number=row[3],
                section_title=row[4],
                metadata=row[5],
            )
            for row in rows
        ]

    def _rrf_fusion(
        self,
        dense: List[RetrievedChunk],
        sparse: List[RetrievedChunk],
        dense_weight: float,
        sparse_weight: float,
        k: int = 60,
    ) -> List[RetrievedChunk]:
        """Reciprocal Rank Fusion to combine dense and sparse results."""
        scores: Dict[str, float] = {}
        chunk_map: Dict[str, RetrievedChunk] = {}

        for rank, chunk in enumerate(dense):
            scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0) + dense_weight / (k + rank + 1)
            chunk_map[chunk.chunk_id] = chunk

        for rank, chunk in enumerate(sparse):
            scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0) + sparse_weight / (k + rank + 1)
            if chunk.chunk_id not in chunk_map:
                chunk_map[chunk.chunk_id] = chunk

        sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
        results = []
        for cid in sorted_ids:
            chunk = chunk_map[cid]
            chunk.score = scores[cid]
            results.append(chunk)
        return results

    async def _rerank(
        self, query: str, chunks: List[RetrievedChunk], top_k: int
    ) -> List[RetrievedChunk]:
        """Re-rank with Cohere if configured, else a local cross-encoder if configured.

        Re-ranking is best-effort: on any failure (provider down, quota, model load) we
        fall back to the fusion order rather than failing retrieval (F12).
        """
        if not chunks:
            return chunks

        if settings.cohere_api_key:
            try:
                return await self._rerank_cohere(query, chunks, top_k)
            except Exception:
                logger.warning("Cohere re-ranking failed; using fused order", exc_info=True)
                return chunks[:top_k]

        if settings.local_reranker_model:
            try:
                return await self._rerank_local(query, chunks, top_k)
            except Exception:
                logger.warning("local re-ranking failed; using fused order", exc_info=True)
                return chunks[:top_k]

        return chunks[:top_k]

    async def _rerank_cohere(self, query, chunks, top_k):
        from app.core.llm_clients import cohere_client

        response = await cohere_client().rerank(
            model=settings.default_reranker_model,
            query=query,
            documents=[c.content for c in chunks],
            top_n=top_k,
        )
        reranked = []
        for r in response.results:
            chunk = chunks[r.index]
            chunk.score = r.relevance_score
            reranked.append(chunk)
        return reranked

    async def _rerank_local(self, query, chunks, top_k):
        import asyncio

        model = _get_local_reranker(settings.local_reranker_model)
        pairs = [(query, c.content) for c in chunks]
        # CrossEncoder.predict is sync + CPU-bound; keep it off the event loop.
        scores = await asyncio.to_thread(model.predict, pairs)
        ranked = sorted(zip(chunks, scores), key=lambda cs: cs[1], reverse=True)[:top_k]
        out = []
        for chunk, score in ranked:
            chunk.score = float(score)
            out.append(chunk)
        return out
