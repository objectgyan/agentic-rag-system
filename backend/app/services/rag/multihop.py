"""Multi-hop / iterative retrieval (A1).

A single retrieval pass can't answer questions whose pieces live in different places
("Who manages the author of the 2019 safety report?"). This does retrieve → reason →
retrieve: after the first pass, an LLM looks at what's been gathered and either declares
the context sufficient or proposes ONE follow-up query for the most important missing
piece. We retrieve that, accumulate (de-duplicated), and repeat up to ``max_hops``.

The reasoning step is fail-soft: if the LLM call errors we just stop hopping and return
what we have, so multi-hop can only add coverage, never break the query.
"""

import logging
from typing import List, Optional, Tuple

from app.core.config import settings
from app.core.llm_clients import openai_client
from app.services.rag.retriever import HybridRetriever, RetrievedChunk

logger = logging.getLogger(__name__)

_SUFFICIENT = "SUFFICIENT"
_SYSTEM = (
    "You orchestrate multi-hop document retrieval. Given a question and the context "
    "gathered so far, decide whether that context is enough to fully answer the question. "
    f"If it is, reply with exactly {_SUFFICIENT}. If not, reply with ONE short search "
    "query (no preamble, no quotes) targeting the single most important missing fact."
)


class MultiHopRetriever:
    def __init__(self, retriever: HybridRetriever, model: Optional[str] = None):
        self.retriever = retriever
        self.model = model or settings.default_llm_model

    async def retrieve(
        self,
        query: str,
        collection_ids: Optional[List[str]] = None,
        top_k: int = 5,
        max_hops: int = 2,
    ) -> Tuple[List[RetrievedChunk], List[str]]:
        seen: set = set()
        accumulated: List[RetrievedChunk] = []

        def _add(chunks: List[RetrievedChunk]) -> None:
            for c in chunks:
                if c.chunk_id not in seen:
                    seen.add(c.chunk_id)
                    accumulated.append(c)

        _add(await self.retriever.retrieve(query, collection_ids=collection_ids, top_k=top_k))

        hops: List[str] = []
        for _ in range(max_hops):
            followup = await self._next_query(query, accumulated)
            if not followup:
                break
            hops.append(followup)
            _add(await self.retriever.retrieve(followup, collection_ids=collection_ids, top_k=top_k))
        return accumulated, hops

    async def _next_query(self, query: str, chunks: List[RetrievedChunk]) -> Optional[str]:
        context = "\n\n".join(c.content[:500] for c in chunks) or "(nothing retrieved yet)"
        try:
            client = openai_client()
            response = await client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": f"Question: {query}\n\nContext so far:\n{context}"},
                ],
                temperature=0.0,
                max_tokens=80,
            )
        except Exception:
            logger.warning("multi-hop follow-up generation failed; stopping hops", exc_info=True)
            return None

        text = (response.choices[0].message.content or "").strip()
        if not text or text.upper().startswith(_SUFFICIENT):
            return None
        return text
