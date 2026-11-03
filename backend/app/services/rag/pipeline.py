"""The RAG pipeline: stages -> generate -> finalize, shared by blocking and streaming queries.

    ctx = QueryContext(...)                 # one request's state
    run_stages(self.stages, ctx)            # route, HyDE, retrieve, re-rank, compress, graph, memory
    answer = generate(ctx)                  # or generate_stream(ctx), token by token
    finalize(ctx, answer)                   # citations, optional self-eval, trace, usage/billing

The stage list is the customization point (see ``stages.py``); everything else is the same for
every request, so the streaming path gets every feature — and is billed — exactly like
``query()``.
"""

import logging
import re
import time
from dataclasses import fields
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence, Set

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.rag.embedder import EmbeddingService
from app.services.rag.generator import GenerationService
from app.services.rag.query_enhancer import QueryEnhancer
from app.services.rag.retriever import HybridRetriever, RetrievedChunk
from app.services.rag.stages import DEFAULT_STAGES, QueryContext, QueryOptions, Stage, run_stages

logger = logging.getLogger(__name__)

_OPTION_NAMES = {f.name for f in fields(QueryOptions)}


class RAGPipeline:
    """End-to-end RAG pipeline with configurable stages."""

    def __init__(
        self,
        db: AsyncSession,
        tenant_id: str,
        user_id: str,
        stages: Optional[Sequence[Stage]] = None,
    ):
        self.db = db
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.embedder = EmbeddingService()
        self.retriever = HybridRetriever(db=db, tenant_id=tenant_id, embedding_service=self.embedder)
        self.query_enhancer = QueryEnhancer()
        self.stages: List[Stage] = list(stages if stages is not None else DEFAULT_STAGES)

    def _extract_cited_sources(self, answer: str) -> Set[int]:
        """Extract which [Source N] numbers were actually cited in the answer."""
        return {int(m) for m in re.findall(r"\[Source\s+(\d+)\]", answer, re.IGNORECASE)}

    # ---- shared steps -----------------------------------------------------------------

    async def _prepare(
        self, query: str, conversation_history: Optional[List[Dict[str, str]]], options: Dict[str, Any]
    ) -> QueryContext:
        unknown = set(options) - _OPTION_NAMES
        if unknown:
            raise TypeError(f"unknown query option(s): {sorted(unknown)}")
        ctx = QueryContext(query=query, opts=QueryOptions(**options), pipeline=self, history=conversation_history)
        start = time.time()
        await run_stages(self.stages, ctx)
        ctx.extras["retrieval_time_ms"] = (time.time() - start) * 1000
        # Trace (item 3): the context-building phase + the chunks it produced.
        ctx.trace.add_span("retrieve", ctx.extras["retrieval_time_ms"], n_chunks=len(ctx.chunks))
        ctx.trace.record_chunks(ctx.chunks)
        return ctx

    def _generator(self, ctx: QueryContext) -> GenerationService:
        return GenerationService(model=ctx.opts.model, temperature=ctx.opts.temperature)

    async def _citations(self, chunks: List[RetrievedChunk], answer: str) -> List[Dict[str, Any]]:
        """Citations for the sources the answer actually referenced as [Source N] — one DB query."""
        cited = self._extract_cited_sources(answer)
        picked = [(i, c) for i, c in enumerate(chunks, 1) if i in cited]
        if not picked:
            return []
        from sqlalchemy import select

        from app.models.document import Document

        ids = list({c.document_id for _, c in picked})
        rows = await self.db.execute(select(Document.id, Document.original_filename).where(Document.id.in_(ids)))
        names = {str(doc_id): name for doc_id, name in rows.all()}
        return [
            {
                "document_id": c.document_id,
                "document_name": names.get(str(c.document_id), "Unknown"),
                "chunk_id": c.chunk_id,
                "content": c.content[:300],
                "page_number": c.page_number,
                "score": c.score,
            }
            for _, c in picked
        ]

    async def _finalize(self, ctx: QueryContext, result: Dict[str, Any]) -> Dict[str, Any]:
        o = ctx.opts
        result["citations"] = await self._citations(ctx.chunks, result["answer"]) if o.include_citations else []
        result["retrieval_time_ms"] = ctx.extras["retrieval_time_ms"]
        result["hops"] = ctx.hops
        result["graph_facts"] = ctx.graph_facts
        result["intent"] = ctx.intent

        # Optional RAG self-evaluation (C3) — opt-in because it costs extra LLM calls.
        result["evaluation"] = None
        if o.evaluate and ctx.chunks:
            try:
                from app.services.rag.evaluator import RAGEvaluator

                result["evaluation"] = await RAGEvaluator(model=o.model).evaluate(
                    ctx.query, result["answer"], [c.content for c in ctx.chunks]
                )
            except Exception:
                logger.warning("RAG evaluation failed; returning answer without scores", exc_info=True)
                ctx.degraded.append("evaluation")
        result["degraded"] = ctx.degraded

        # Finalize the trace (item 3): usage + cost, a structured summary for every query, and the
        # full trace on the response only when requested.
        model_used = result.get("model_used") or o.model or settings.default_llm_model
        ctx.trace.record_usage(
            model_used,
            prompt_tokens=result.get("prompt_tokens"),
            completion_tokens=result.get("completion_tokens"),
            total_tokens=result.get("tokens_used"),
        )
        ctx.trace.log_summary(logger)
        result["trace"] = ctx.trace.to_dict() if o.trace else None

        await self._track_usage(
            result.get("tokens_used"),
            o.model,
            prompt_tokens=result.get("prompt_tokens"),
            completion_tokens=result.get("completion_tokens"),
        )
        return result

    # ---- public API ---------------------------------------------------------------------

    async def query(
        self,
        query: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        **options: Any,
    ) -> Dict[str, Any]:
        """Execute a full RAG query. ``options`` are the fields of ``QueryOptions``."""
        ctx = await self._prepare(query, conversation_history, options)
        with ctx.trace.span("generate"):
            result = await self._generator(ctx).generate(
                query,
                ctx.chunks,
                conversation_history=ctx.history,
                graph_facts=ctx.graph_facts,
                conversation_summary=ctx.summary,
            )
        return await self._finalize(ctx, result)

    async def query_stream(
        self,
        query: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        **options: Any,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Stream a RAG query: ``token`` events, then ``citations`` (if any), then one final ``done``.

        Runs the same stages and finalization as ``query()`` — including usage tracking.
        """
        ctx = await self._prepare(query, conversation_history, options)
        result: Optional[Dict[str, Any]] = None
        start = time.time()
        async for event in self._generator(ctx).generate_stream(
            query,
            ctx.chunks,
            conversation_history=ctx.history,
            conversation_summary=ctx.summary,
            graph_facts=ctx.graph_facts,
        ):
            if event["type"] == "done":
                result = {k: v for k, v in event.items() if k != "type"}
            else:
                yield event
        ctx.trace.add_span("generate", (time.time() - start) * 1000)
        if result is None:  # the provider stream ended without a final message
            return

        result = await self._finalize(ctx, result)
        if result["citations"]:
            yield {"type": "citations", "citations": result["citations"]}
        yield {
            "type": "done",
            "model_used": result.get("model_used"),
            "tokens_used": result.get("tokens_used"),
            "degraded": result["degraded"],
            "intent": result["intent"],
            "evaluation": result["evaluation"],
            "trace": result["trace"],
        }

    async def _track_usage(
        self,
        tokens: Optional[int],
        model: Optional[str],
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
    ):
        """Record usage for billing and limits, including an estimated cost (item 3)."""
        from app.services.usage import record_usage

        await record_usage(
            self.db,
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            resource_type="query",
            model=model,
            tokens=tokens,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
