"""Pipeline stages — the RAG query path as a list of small, swappable building blocks.

Before this module, ``RAGPipeline.query`` was one long function driven by 18 boolean flags, and
the streaming path was a separate, thinner copy that silently skipped most features. Now every
step is a ``Stage``; the pipeline runs the same list for blocking and streaming queries.

A stage is three things:

- ``name`` — also the label recorded in ``ctx.degraded`` when an optional stage fails;
- ``applies(ctx)`` — whether it should run for this request (usually: is its option on?);
- ``run(ctx)`` — mutate the shared ``QueryContext`` (rewrite the query, set/replace chunks, ...).

``fail_soft = True`` marks a stage as an *optional accelerator*: if it raises, the pipeline logs
it, records the name in ``ctx.degraded`` and carries on with what it has (F12). Retrieval itself
is not fail-soft — without it there is nothing to answer from.

To customize for a client, build your own list (insert a PII filter, an ACL filter, a domain
re-ranker, ...) and pass it as ``RAGPipeline(..., stages=[...])``. See
docs/learn/blocks/02-pipeline-stages.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Protocol, Sequence

from app.services.rag.retriever import RetrievedChunk
from app.services.rag.tracing import QueryTrace

if TYPE_CHECKING:  # pragma: no cover
    from app.services.rag.pipeline import RAGPipeline

logger = logging.getLogger(__name__)


@dataclass
class QueryOptions:
    """Every per-request knob of the query API, in one place."""

    collection_ids: Optional[List[str]] = None
    top_k: int = 5
    model: Optional[str] = None
    use_reranking: bool = True
    use_hyde: bool = False
    use_multi_query: bool = False
    temperature: float = 0.1
    include_citations: bool = True
    evaluate: bool = False
    use_compression: bool = False
    use_multi_hop: bool = False
    max_hops: int = 2
    use_graph: bool = False
    trace: bool = False
    use_routing: bool = False
    boosts: Optional[List[dict]] = None


@dataclass
class QueryContext:
    """The state that flows through the stages for one query."""

    query: str
    opts: QueryOptions
    pipeline: "RAGPipeline"
    search_query: str = ""
    chunks: List[RetrievedChunk] = field(default_factory=list)
    intent: Optional[str] = None
    skip_retrieval: bool = False
    hops: List[str] = field(default_factory=list)
    graph_facts: List[str] = field(default_factory=list)
    history: Optional[List[Dict[str, str]]] = None
    summary: Optional[str] = None
    degraded: List[str] = field(default_factory=list)
    trace: QueryTrace = field(default_factory=QueryTrace)
    # Free-form scratch space for custom stages.
    extras: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.search_query = self.search_query or self.query


class Stage(Protocol):
    name: str
    fail_soft: bool

    def applies(self, ctx: QueryContext) -> bool: ...

    async def run(self, ctx: QueryContext) -> None: ...


async def run_stages(stages: Sequence[Stage], ctx: QueryContext) -> None:
    for stage in stages:
        if not stage.applies(ctx):
            continue
        try:
            # Every stage is traced automatically — custom stages included.
            with ctx.trace.span(f"stage.{stage.name}"):
                await stage.run(ctx)
        except Exception:
            if not stage.fail_soft:
                raise
            logger.warning("stage %r failed; continuing without it", stage.name, exc_info=True)
            ctx.degraded.append(stage.name)


# ---------------------------------------------------------------------------------------------
# Built-in stages, in their default order.
# ---------------------------------------------------------------------------------------------


class RouteStage:
    """Intent routing (item 4): chit-chat skips retrieval and the query rewriters."""

    name = "routing"
    fail_soft = True

    def applies(self, ctx: QueryContext) -> bool:
        return ctx.opts.use_routing

    async def run(self, ctx: QueryContext) -> None:
        from app.services.rag.intent import IntentClassifier

        ctx.intent = (await IntentClassifier().classify(ctx.query)).intent
        if ctx.intent == "chitchat":
            ctx.skip_retrieval = True


class HydeStage:
    """HyDE: search with a hypothetical answer instead of the question."""

    name = "hyde"
    fail_soft = True

    def applies(self, ctx: QueryContext) -> bool:
        return ctx.opts.use_hyde and not ctx.skip_retrieval

    async def run(self, ctx: QueryContext) -> None:
        ctx.search_query = await ctx.pipeline.query_enhancer.hyde_generate(ctx.query)


class RetrieveStage:
    """Fetch candidate chunks: multi-query fan-out, multi-hop, or a single hybrid search."""

    name = "retrieve"
    fail_soft = False

    def applies(self, ctx: QueryContext) -> bool:
        return not ctx.skip_retrieval

    async def run(self, ctx: QueryContext) -> None:
        o, retriever = ctx.opts, ctx.pipeline.retriever
        if o.use_multi_query:
            await self._multi_query(ctx)
        elif o.use_multi_hop:
            from app.services.rag.multihop import MultiHopRetriever

            ctx.chunks, ctx.hops = await MultiHopRetriever(retriever, model=o.model).retrieve(
                ctx.query, collection_ids=o.collection_ids, top_k=o.top_k, max_hops=o.max_hops
            )
            if o.use_reranking and ctx.chunks:
                ctx.chunks = await retriever._rerank(ctx.query, ctx.chunks, o.top_k)
        else:
            ctx.chunks = await retriever.retrieve(
                ctx.search_query, collection_ids=o.collection_ids, top_k=o.top_k, use_reranking=o.use_reranking
            )

    async def _multi_query(self, ctx: QueryContext) -> None:
        o, retriever = ctx.opts, ctx.pipeline.retriever
        try:
            queries = await ctx.pipeline.query_enhancer.multi_query_expand(ctx.query)
        except Exception:
            logger.warning("multi-query expansion failed; using plain query", exc_info=True)
            ctx.degraded.append("multi_query")
            queries = []
        queries.append(ctx.query)
        seen: set = set()
        merged: List[RetrievedChunk] = []
        for q in queries:
            for c in await retriever.retrieve(q, collection_ids=o.collection_ids, top_k=o.top_k, use_reranking=False):
                if c.chunk_id not in seen:
                    seen.add(c.chunk_id)
                    merged.append(c)
        # Re-rank the union against the *original* question.
        ctx.chunks = await retriever._rerank(ctx.query, merged, o.top_k) if o.use_reranking else merged[: o.top_k]


class BusinessRerankStage:
    """Blend relevance with weighted metadata signals (item 4b)."""

    name = "business_rerank"
    fail_soft = True

    def applies(self, ctx: QueryContext) -> bool:
        return bool(ctx.opts.boosts) and bool(ctx.chunks)

    async def run(self, ctx: QueryContext) -> None:
        from app.services.rag.ranking import MetadataBoostReranker

        ctx.chunks = await MetadataBoostReranker(ctx.opts.boosts).rerank(ctx.query, ctx.chunks, ctx.opts.top_k)


class CompressStage:
    """Contextual compression (A2): distill chunks to query-relevant content."""

    name = "compression"
    fail_soft = True

    def applies(self, ctx: QueryContext) -> bool:
        return ctx.opts.use_compression and bool(ctx.chunks)

    async def run(self, ctx: QueryContext) -> None:
        from app.services.rag.compressor import ContextualCompressor

        ctx.chunks = await ContextualCompressor().compress(ctx.query, ctx.chunks)


class GraphStage:
    """Knowledge-graph augmentation (A3): facts about entities in the query."""

    name = "graph"
    fail_soft = True

    def applies(self, ctx: QueryContext) -> bool:
        return ctx.opts.use_graph

    async def run(self, ctx: QueryContext) -> None:
        from app.services.rag.graph import GraphService

        ctx.graph_facts = await GraphService().query_facts(ctx.pipeline.db, ctx.query, ctx.opts.collection_ids)


class MemoryStage:
    """Bound the conversation history (item 2): running summary + token-budgeted recent window."""

    name = "conversation_memory"
    fail_soft = True

    def applies(self, ctx: QueryContext) -> bool:
        return bool(ctx.history)

    async def run(self, ctx: QueryContext) -> None:
        from app.services.rag.conversation_memory import ConversationMemory

        ctx.summary, ctx.history = await ConversationMemory(model=ctx.opts.model).prepare(ctx.history)


#: Stages timed as "retrieval" (everything that produces the context).
RETRIEVAL_STAGES: List[Stage] = [
    RouteStage(),
    HydeStage(),
    RetrieveStage(),
    BusinessRerankStage(),
    CompressStage(),
    GraphStage(),
]

#: The full default pre-generation pipeline.
DEFAULT_STAGES: List[Stage] = [*RETRIEVAL_STAGES, MemoryStage()]
