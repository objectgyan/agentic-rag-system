"""Main RAG pipeline orchestrating retrieval, enhancement, and generation."""

import logging
import re
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Set

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.rag.embedder import EmbeddingService
from app.services.rag.generator import GenerationService
from app.services.rag.query_enhancer import QueryEnhancer
from app.services.rag.retriever import HybridRetriever, RetrievedChunk

logger = logging.getLogger(__name__)


class RAGPipeline:
    """End-to-end RAG pipeline with configurable strategies."""

    def __init__(self, db: AsyncSession, tenant_id: str, user_id: str):
        self.db = db
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.embedder = EmbeddingService()
        self.retriever = HybridRetriever(db=db, tenant_id=tenant_id, embedding_service=self.embedder)
        self.query_enhancer = QueryEnhancer()

    def _extract_cited_sources(self, answer: str) -> Set[int]:
        """Extract which [Source N] numbers were actually cited in the answer."""
        pattern = r'\[Source\s+(\d+)\]'
        matches = re.findall(pattern, answer, re.IGNORECASE)
        return set(int(m) for m in matches)

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
        conversation_history: Optional[List[Dict[str, str]]] = None,
        evaluate: bool = False,
        use_compression: bool = False,
        use_multi_hop: bool = False,
        max_hops: int = 2,
        use_graph: bool = False,
        trace: bool = False,
    ) -> Dict[str, Any]:
        """Execute a full RAG query."""
        from app.services.rag.tracing import QueryTrace

        qt = QueryTrace()
        retrieval_start = time.time()

        # Query enhancement. These are optional accelerators: if the enhancer LLM call
        # fails, we log it and fall back to the plain query rather than failing the whole
        # request, recording what degraded so it's visible in the response (F12).
        search_query = query
        all_chunks: List[RetrievedChunk] = []
        degraded: List[str] = []
        hops: List[str] = []

        if use_hyde:
            try:
                search_query = await self.query_enhancer.hyde_generate(query)
            except Exception:
                logger.warning("HyDE enhancement failed; falling back to plain query", exc_info=True)
                degraded.append("hyde")

        if use_multi_query:
            try:
                queries = await self.query_enhancer.multi_query_expand(query)
            except Exception:
                logger.warning("multi-query expansion failed; using plain query", exc_info=True)
                degraded.append("multi_query")
                queries = []
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
        elif use_multi_hop:
            # Iterative retrieve -> reason -> retrieve (A1).
            from app.services.rag.multihop import MultiHopRetriever

            all_chunks, hops = await MultiHopRetriever(self.retriever, model=model).retrieve(
                query, collection_ids=collection_ids, top_k=top_k, max_hops=max_hops
            )
            if use_reranking and all_chunks:
                all_chunks = await self.retriever._rerank(query, all_chunks, top_k)
        else:
            all_chunks = await self.retriever.retrieve(
                search_query, collection_ids=collection_ids, top_k=top_k, use_reranking=use_reranking
            )

        # Contextual compression (A2): distill chunks to query-relevant content before
        # generation. Optional and fail-soft — on error we keep the raw chunks.
        if use_compression and all_chunks:
            try:
                from app.services.rag.compressor import ContextualCompressor

                all_chunks = await ContextualCompressor().compress(query, all_chunks)
            except Exception:
                logger.warning("contextual compression failed; using raw chunks", exc_info=True)
                degraded.append("compression")

        # Knowledge-graph augmentation (A3): pull facts about entities in the query.
        graph_facts: List[str] = []
        if use_graph:
            try:
                from app.services.rag.graph import GraphService

                graph_facts = await GraphService().query_facts(self.db, query, collection_ids)
            except Exception:
                logger.warning("knowledge-graph retrieval failed; answering without it", exc_info=True)
                degraded.append("graph")

        retrieval_time = (time.time() - retrieval_start) * 1000
        # Trace (item 3): record the retrieval stage + the chunks it produced.
        qt.add_span("retrieve", retrieval_time, n_chunks=len(all_chunks))
        qt.record_chunks(all_chunks)

        # Bound the conversation history (competitive-phase item 2): summarize older turns and
        # keep a token-budgeted recent window, so long chats don't blow the context window.
        # Fail-soft — on error we fall back to the raw history and record it as degraded.
        conversation_summary: Optional[str] = None
        if conversation_history:
            try:
                from app.services.rag.conversation_memory import ConversationMemory

                conversation_summary, conversation_history = await ConversationMemory(
                    model=model
                ).prepare(conversation_history)
            except Exception:
                logger.warning("conversation memory prep failed; using raw history", exc_info=True)
                degraded.append("conversation_memory")

        # Generate answer, conditioning on prior conversation turns (C1) and graph facts (A3).
        generator = GenerationService(model=model, temperature=temperature)
        with qt.span("generate"):
            result = await generator.generate(
                query,
                all_chunks,
                conversation_history=conversation_history,
                graph_facts=graph_facts,
                conversation_summary=conversation_summary,
            )

        # Build citations - only for sources actually cited in the answer
        citations = []
        if include_citations:
            cited_source_nums = self._extract_cited_sources(result["answer"])
            from sqlalchemy import select

            from app.models.document import Document

            for i, chunk in enumerate(all_chunks, 1):
                # Only include if this source was actually cited in the answer
                if i in cited_source_nums:
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
        result["degraded"] = degraded
        result["hops"] = hops
        result["graph_facts"] = graph_facts

        # Optional RAG self-evaluation (C3) — opt-in because it costs extra LLM calls.
        result["evaluation"] = None
        if evaluate and all_chunks:
            try:
                from app.services.rag.evaluator import RAGEvaluator
                evaluator = RAGEvaluator(model=model)
                contexts = [c.content for c in all_chunks]
                result["evaluation"] = await evaluator.evaluate(query, result["answer"], contexts)
            except Exception:
                logger.warning("RAG evaluation failed; returning answer without scores", exc_info=True)
                degraded.append("evaluation")

        # Finalize the trace (item 3): usage + cost, emit a structured summary for every query,
        # and attach the full trace to the response only when explicitly requested.
        qt.record_usage(
            result.get("model_used") or model or settings.default_llm_model,
            prompt_tokens=result.get("prompt_tokens"),
            completion_tokens=result.get("completion_tokens"),
            total_tokens=result.get("tokens_used"),
        )
        qt.log_summary(logger)
        result["trace"] = qt.to_dict() if trace else None

        # Track usage (tokens + estimated cost)
        await self._track_usage(
            result.get("tokens_used"),
            model,
            prompt_tokens=result.get("prompt_tokens"),
            completion_tokens=result.get("completion_tokens"),
        )

        return result

    async def query_stream(
        self,
        query: str,
        collection_ids: Optional[List[str]] = None,
        top_k: int = 5,
        model: Optional[str] = None,
        use_reranking: bool = True,
        temperature: float = 0.1,
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Stream a RAG query response."""
        chunks = await self.retriever.retrieve(
            query, collection_ids=collection_ids, top_k=top_k, use_reranking=use_reranking
        )

        # Bound the conversation history (competitive-phase item 2), fail-soft.
        conversation_summary: Optional[str] = None
        if conversation_history:
            try:
                from app.services.rag.conversation_memory import ConversationMemory

                conversation_summary, conversation_history = await ConversationMemory(
                    model=model
                ).prepare(conversation_history)
            except Exception:
                logger.warning("conversation memory prep failed; using raw history", exc_info=True)

        generator = GenerationService(model=model, temperature=temperature)

        # Accumulate the full answer to determine which sources were actually cited
        full_answer = ""

        async for token in generator.generate_stream(
            query,
            chunks,
            conversation_history=conversation_history,
            conversation_summary=conversation_summary,
        ):
            # Skip the initial citations - we'll send filtered ones at the end
            if token.get("type") == "citations":
                continue

            # Accumulate answer text
            if token.get("type") == "token":
                full_answer += token.get("content", "")

            yield token

        # Now send filtered citations based on what was actually cited
        cited_source_nums = self._extract_cited_sources(full_answer)
        if cited_source_nums:
            from sqlalchemy import select

            from app.models.document import Document

            enriched_citations = []
            for i, chunk in enumerate(chunks, 1):
                if i in cited_source_nums:
                    doc_result = await self.db.execute(
                        select(Document).where(Document.id == chunk.document_id)
                    )
                    doc = doc_result.scalar_one_or_none()
                    enriched_citations.append({
                        "chunk_id": chunk.chunk_id,
                        "document_id": chunk.document_id,
                        "document_name": doc.original_filename if doc else "Unknown",
                        "content": chunk.content[:200],
                        "score": chunk.score,
                        "page_number": chunk.page_number,
                    })

            if enriched_citations:
                yield {
                    "type": "citations",
                    "citations": enriched_citations,
                }

    async def _track_usage(
        self,
        tokens: Optional[int],
        model: Optional[str],
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
    ):
        """Record usage for billing and limits, including an estimated cost (item 3)."""
        from datetime import datetime, timezone

        from app.models.usage import UsageRecord
        from app.services.rag.tracing import estimate_cost

        model_used = model or settings.default_llm_model
        cost = (
            estimate_cost(model_used, prompt_tokens or 0, completion_tokens or 0)
            if (prompt_tokens or completion_tokens)
            else None
        )
        record = UsageRecord(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            resource_type="query",
            tokens_used=tokens,
            cost_usd=cost,
            model_used=model_used,
            period=datetime.now(timezone.utc).strftime("%Y-%m"),
        )
        self.db.add(record)
        await self.db.flush()
