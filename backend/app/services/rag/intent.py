"""Intent classification / query routing (competitive-phase item 4).

Classifies a query into a small set of intents so the pipeline can *branch* — most usefully, skip
retrieval for chit-chat so smalltalk doesn't hit the retriever (or cost an embedding + search).

This is the "intent classification is a re-ranking argmax" idea from docs/RERANKING.md: embed the
query and each intent's anchor text into the same space and take the nearest. Embedding-based, so
it needs no extra LLM call and reuses ``EmbeddingService``. Accuracy depends on the anchor
phrasings — and now that item 1 exists, it's measurable. For a large taxonomy or higher accuracy,
swap in an LLM zero-shot classifier behind the same ``classify()`` interface.

Vocabulary: *query intent / routing, zero-shot classification, argmax over label embeddings.*
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Dict, List, Optional

from app.services.rag.embedder import EmbeddingService

logger = logging.getLogger(__name__)

# intent -> anchor text (description + example phrases to sharpen the embedding separation).
DEFAULT_INTENTS: Dict[str, str] = {
    "question": (
        "An information-seeking question to be answered from documents. For example: what is the "
        "refund policy, how do I reset my password, when was the report published."
    ),
    "comparison": (
        "A request to compare, contrast, or choose between multiple options. For example: compare "
        "plan A and plan B, which is better, what are the differences between X and Y."
    ),
    "chitchat": (
        "A greeting, thanks, or casual small talk that needs no documents. For example: hello, hi "
        "there, how are you, good morning, thanks so much, that is great."
    ),
}


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


@dataclass
class IntentResult:
    intent: str
    scores: Dict[str, float]


class IntentClassifier:
    """Zero-shot intent classifier: nearest intent-anchor to the query in embedding space."""

    def __init__(
        self,
        intents: Optional[Dict[str, str]] = None,
        embedding_service: Optional[EmbeddingService] = None,
    ):
        self.intents = intents or DEFAULT_INTENTS
        self.embedder = embedding_service or EmbeddingService()
        self._labels = list(self.intents.keys())
        self._anchor_embeddings: Optional[List[List[float]]] = None

    async def _ensure_anchors(self) -> None:
        if self._anchor_embeddings is None:
            self._anchor_embeddings = await self.embedder.embed_texts(
                [self.intents[label] for label in self._labels]
            )

    async def classify(self, query: str) -> IntentResult:
        """Return the best-matching intent and the per-intent cosine scores."""
        await self._ensure_anchors()
        q = await self.embedder.embed_query(query)
        scores = {
            label: _cosine(q, emb) for label, emb in zip(self._labels, self._anchor_embeddings)
        }
        best = max(scores, key=scores.get) if scores else self._labels[0]
        return IntentResult(intent=best, scores=scores)
