"""Pure retrieval-quality metrics.

No DB, no LLM, no I/O — each function takes a *ranked list of retrieved ids* (best-first,
rank 1 = index 0) and a *set of relevant ids* (the human "gold" judgments) and returns a score
in [0, 1]. That makes them fast, deterministic, and unit-testable in CI without any infrastructure.

The vocabulary here is the language you'll use with technical folks:

- **retrieved** — the ids the retriever returned, best-first.
- **relevant**  — the ids a human judged correct (ground truth / gold).
- **@k**        — evaluate only the top-k retrieved (the cutoff a user actually sees).
- **recall@k**    — of all the relevant items, how many made it into the top-k? (coverage)
- **precision@k** — of the top-k shown, how many were relevant? (noise)
- **MRR**         — mean of 1/(rank of first relevant); rewards putting a good hit *first*.
- **hit@k**       — did we get *anything* relevant in the top-k? (binary)
- **nDCG@k**      — like recall but *rank-weighted*: a relevant item higher up scores more.
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Sequence


def _top_k(retrieved: Sequence[str], k: int) -> List[str]:
    return list(retrieved[:k])


def hit_at_k(retrieved: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """1.0 if at least one relevant id appears in the top-k, else 0.0."""
    relevant = set(relevant)
    if not relevant:
        return 0.0
    return 1.0 if any(r in relevant for r in _top_k(retrieved, k)) else 0.0


def recall_at_k(retrieved: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """Fraction of all relevant ids that appear in the top-k:  |relevant ∩ top_k| / |relevant|."""
    relevant = set(relevant)
    if not relevant:
        return 0.0
    hits = sum(1 for r in _top_k(retrieved, k) if r in relevant)
    return hits / len(relevant)


def precision_at_k(retrieved: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """Fraction of the top-k that are relevant:  |relevant ∩ top_k| / min(k, #retrieved).

    Dividing by the number actually shown (not a hard ``k``) avoids penalizing a query that
    legitimately returned fewer than ``k`` candidates.
    """
    relevant = set(relevant)
    top = _top_k(retrieved, k)
    if not top:
        return 0.0
    hits = sum(1 for r in top if r in relevant)
    return hits / len(top)


def reciprocal_rank(retrieved: Sequence[str], relevant: Iterable[str], k: int | None = None) -> float:
    """1 / (rank of the first relevant id), 0.0 if none. Averaged across queries this is **MRR**."""
    relevant = set(relevant)
    items = retrieved if k is None else _top_k(retrieved, k)
    for idx, r in enumerate(items, start=1):
        if r in relevant:
            return 1.0 / idx
    return 0.0


def ndcg_at_k(retrieved: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """Normalized Discounted Cumulative Gain (binary relevance).

    DCG discounts a hit by log2 of its position, so a relevant item ranked 1st is worth more
    than the same item ranked 5th. Normalized by the ideal ordering (IDCG) so the score is in
    [0, 1] and comparable across queries with different numbers of relevant items.
    """
    relevant = set(relevant)
    if not relevant:
        return 0.0
    top = _top_k(retrieved, k)
    dcg = sum(1.0 / math.log2(idx + 1) for idx, r in enumerate(top, start=1) if r in relevant)
    ideal_hits = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(idx + 1) for idx in range(1, ideal_hits + 1))
    return dcg / idcg if idcg > 0 else 0.0


def retrieval_scores(retrieved: Sequence[str], relevant: Iterable[str], k: int) -> Dict[str, float]:
    """All retrieval metrics for one query, keyed by name (``mrr`` is this query's reciprocal rank)."""
    relevant = list(relevant)
    return {
        f"hit@{k}": hit_at_k(retrieved, relevant, k),
        f"recall@{k}": recall_at_k(retrieved, relevant, k),
        f"precision@{k}": precision_at_k(retrieved, relevant, k),
        "mrr": reciprocal_rank(retrieved, relevant, k),
        f"ndcg@{k}": ndcg_at_k(retrieved, relevant, k),
    }


def mean_scores(per_case: Sequence[Dict[str, float]]) -> Dict[str, float]:
    """Average a list of per-case metric dicts into aggregate means (skips missing keys)."""
    if not per_case:
        return {}
    keys: List[str] = []
    for d in per_case:
        for key in d:
            if key not in keys:
                keys.append(key)
    out: Dict[str, float] = {}
    for key in keys:
        vals = [d[key] for d in per_case if key in d]
        out[key] = sum(vals) / len(vals) if vals else 0.0
    return out
