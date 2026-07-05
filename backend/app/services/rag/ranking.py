"""Business / metadata re-ranking (competitive-phase item 4).

Semantic re-ranking (a cross-encoder) answers *"is it relevant?"*. This layer answers *"of the
relevant ones, which do we surface?"* — it blends the relevance score with weighted **business
signals** read from each chunk's metadata (manufacturer preference, popularity, recency, ...). See
[docs/RERANKING.md](../../../docs/RERANKING.md) for the semantic-vs-business distinction.

Pluggable: anything implementing ``Reranker.rerank`` drops in (e.g. a learned LambdaMART model).
``MetadataBoostReranker`` is the config-driven default:

    final = base_weight · norm(relevance) + Σ (rule contributions)

Every signal is min-max **normalized across the candidate set** before blending, so a big-magnitude
signal can't dominate just because of its units (RERANKING.md §1.4).

Vocabulary: *signal fusion, boosting vs filtering, metadata filtering, learning-to-rank.*
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol

from app.services.rag.retriever import RetrievedChunk

logger = logging.getLogger(__name__)


class Reranker(Protocol):
    async def rerank(
        self, query: str, chunks: List[RetrievedChunk], top_k: int
    ) -> List[RetrievedChunk]: ...


@dataclass
class BoostRule:
    """One business signal.

    - categorical (``equals`` set): +``weight`` when ``metadata[field]`` equals it (or is in it if a list).
    - numeric (``numeric=True``): +``weight`` · min-max-normalized ``metadata[field]`` across candidates.
    """

    field: str
    weight: float
    equals: Optional[Any] = None
    numeric: bool = False

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BoostRule":
        return cls(
            field=d["field"],
            weight=float(d.get("weight", 1.0)),
            equals=d.get("equals"),
            numeric=bool(d.get("numeric", False)),
        )


def _normalize(values: List[float]) -> List[float]:
    """Min-max normalize to [0,1]; a flat list maps to all-zeros (no signal)."""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi == lo:
        return [0.0 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


class MetadataBoostReranker:
    """Config-driven business reranker: blend normalized relevance with weighted metadata boosts."""

    def __init__(self, rules, base_weight: float = 1.0):
        self.rules: List[BoostRule] = [
            r if isinstance(r, BoostRule) else BoostRule.from_dict(r) for r in (rules or [])
        ]
        self.base_weight = base_weight

    def _meta(self, chunk: RetrievedChunk) -> Dict[str, Any]:
        return chunk.metadata or {}

    async def rerank(
        self, query: str, chunks: List[RetrievedChunk], top_k: int
    ) -> List[RetrievedChunk]:
        if not chunks:
            return chunks

        n = len(chunks)
        base = _normalize([float(c.score) for c in chunks])
        finals = [self.base_weight * base[i] for i in range(n)]

        for rule in self.rules:
            if rule.numeric:
                raw: List[float] = []
                for c in chunks:
                    try:
                        raw.append(float(self._meta(c).get(rule.field)))
                    except (TypeError, ValueError):
                        raw.append(0.0)
                norm = _normalize(raw)
                for i in range(n):
                    finals[i] += rule.weight * norm[i]
            else:
                for i, c in enumerate(chunks):
                    v = self._meta(c).get(rule.field)
                    match = v in rule.equals if isinstance(rule.equals, (list, tuple, set)) else v == rule.equals
                    if match:
                        finals[i] += rule.weight

        order = sorted(range(n), key=lambda i: finals[i], reverse=True)
        out: List[RetrievedChunk] = []
        for i in order[:top_k]:
            chunks[i].score = finals[i]
            out.append(chunks[i])
        return out
