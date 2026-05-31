"""Contextual compression: distill retrieved chunks to query-relevant content (A2).

Retrieval returns whole chunks, which often contain mostly-irrelevant text that wastes
context window and dilutes the signal the generator sees. The compressor asks a cheap,
fast model to extract only the parts of each chunk relevant to the question, and to drop
chunks that are entirely irrelevant.

Each chunk is compressed in its own concurrent call (top_k is small), so one slow chunk
doesn't serialize the rest. Failures are fail-safe: a chunk that errors is kept as-is
rather than dropped, so compression can only ever help, never lose information silently.
"""

import asyncio
import logging
from dataclasses import replace
from typing import List, Optional

from app.core.config import settings
from app.core.llm_clients import openai_client
from app.services.rag.retriever import RetrievedChunk

logger = logging.getLogger(__name__)

_IRRELEVANT = "IRRELEVANT"
_SYSTEM_PROMPT = (
    "Extract verbatim only the sentences from the passage that help answer the question, "
    "preserving their original order. Do not add commentary or rephrase. If no part of the "
    f"passage is relevant, reply with exactly {_IRRELEVANT} and nothing else."
)


class ContextualCompressor:
    def __init__(self, model: Optional[str] = None):
        self.model = model or settings.default_compression_model

    async def compress(self, query: str, chunks: List[RetrievedChunk]) -> List[RetrievedChunk]:
        if not chunks:
            return chunks

        results = await asyncio.gather(
            *(self._compress_one(query, c) for c in chunks),
            return_exceptions=True,
        )

        compressed: List[RetrievedChunk] = []
        for original, res in zip(chunks, results):
            if isinstance(res, Exception):
                logger.warning(
                    "compression failed for chunk %s; keeping it uncompressed",
                    original.chunk_id,
                    exc_info=res,
                )
                compressed.append(original)
            elif res is not None:
                compressed.append(res)
            # res is None -> chunk judged irrelevant, drop it
        return compressed

    async def _compress_one(self, query: str, chunk: RetrievedChunk) -> Optional[RetrievedChunk]:
        client = openai_client()
        response = await client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"Question: {query}\n\nPassage:\n{chunk.content}"},
            ],
            temperature=0.0,
            max_tokens=500,
        )
        text = (response.choices[0].message.content or "").strip()
        if not text or text.upper().startswith(_IRRELEVANT):
            return None
        return replace(chunk, content=text)
