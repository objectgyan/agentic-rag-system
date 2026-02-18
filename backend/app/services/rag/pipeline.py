"""Main RAG pipeline orchestrating retrieval, enhancement, and generation."""

import time
from typing import List, Optional, Dict, Any, AsyncIterator
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.rag.retriever import HybridRetriever, RetrievedChunk
from app.services.rag.embedder import EmbeddingService
from app.services.rag.generator import GenerationService
from app.services.rag.query_enhancer import QueryEnhancer
from app.core.config import settings


class RAGPipeline:
    """End-to-end RAG pipeline with configurable strategies."""

    def __init__(self, db: AsyncSession, tenant_id: str, user_id: str):
        self.db = db
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.embedder = EmbeddingService()
        self.retriever = HybridRetriever(db=db, tenant_id=tenant_id, embedding_service=self.embedder)
        self.query_enhancer = QueryEnhancer()

    async def query(
        self,
        query: str,
        collection_ids: Optional[List[str]] = None,
        top_k: int = 5,
        model: Optional[str] = None,
        use_reranking: bool = True,
        use_hyde: bool = False,
        use_multi_query: bool = False,
        temperature: float = 0.1,
        include_citations: bool = True,
    ) -> Dict[str, Any]:
        """Execute a full RAG query."""
        retrieval_start = time.time()

        # Query enhancement
        search_query = query
        all_chunks: List[RetrievedChunk] = []

        if use_hyde:
            hyde_doc = await self.query_enhancer.hyde_generate(query)
            search_query = hyde_doc

        if use_multi_query:
            queries = await self.query_enhancer.multi_query_expand(query)
            queries.append(query)
            seen_ids = set()
            for q in queries:
                chunks = await self.retriever.retrieve(
                    q, collection_ids=collection_ids, top_k=top_k, use_reranking=False
                )
                for c in chunks:
                    if c.chunk_id not in seen_ids:
                        seen_ids.add(c.chunk_id)
                        all_chunks.append(c)
            # Re-rank the combined results
            if use_reranking:
                all_chunks = await self.retriever._rerank(query, all_chunks, top_k)
            else:
                all_chunks = all_chunks[:top_k]
        else:
            all_chunks = await self.retriever.retrieve(
                search_query, collection_ids=collection_ids, top_k=top_k, use_reranking=use_reranking
            )

        retrieval_time = (time.time() - retrieval_start) * 1000

        # Generate answer
        generator = GenerationService(model=model, temperature=temperature)
        result = await generator.generate(query, all_chunks)

        # Build citations
        citations = []
        if include_citations:
            from app.models.document import Document
            from sqlalchemy import select
            for chunk in all_chunks:
                doc_result = await self.db.execute(
                    select(Document).where(Document.id == chunk.document_id)
                )
                doc = doc_result.scalar_one_or_none()
                citations.append({
                    "document_id": chunk.document_id,
                    "document_name": doc.original_filename if doc else "Unknown",
                    "chunk_id": chunk.chunk_id,
                    "content": chunk.content[:300],
                    "page_number": chunk.page_number,
                    "score": chunk.score,
                })

        result["citations"] = citations
        result["retrieval_time_ms"] = retrieval_time

        # Track usage
        await self._track_usage(result.get("tokens_used"), model)

        return result

    async def query_stream(
        self,
        query: str,
        collection_ids: Optional[List[str]] = None,
        top_k: int = 5,
        model: Optional[str] = None,
        use_reranking: bool = True,
        temperature: float = 0.1,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Stream a RAG query response."""
        chunks = await self.retriever.retrieve(
            query, collection_ids=collection_ids, top_k=top_k, use_reranking=use_reranking
        )

        generator = GenerationService(model=model, temperature=temperature)
        async for token in generator.generate_stream(query, chunks):
            yield token

    async def _track_usage(self, tokens: Optional[int], model: Optional[str]):
        """Record usage for billing and limits."""
        from datetime import datetime, timezone
        from app.models.usage import UsageRecord

        record = UsageRecord(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            resource_type="query",
            tokens_used=tokens,
            model_used=model or settings.default_llm_model,
            period=datetime.now(timezone.utc).strftime("%Y-%m"),
        )
        self.db.add(record)
        await self.db.flush()
