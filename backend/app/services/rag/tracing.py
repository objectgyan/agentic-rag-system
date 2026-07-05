"""Per-query tracing + cost estimation (competitive-phase item 3).

Metrics tell you *that* a query was slow; a trace tells you *why*: the latency of each stage, which
chunks were retrieved at what scores, and how many tokens (and dollars) it cost. This is what you
reach for when a customer says "it answered wrong" — pull up the request and see the whole pipeline.

Kept dependency-free (no OpenTelemetry collector required): a ``QueryTrace`` is a plain structured
object attached to the response when ``trace=true`` and emitted to the structured logs on every
query. It can be exported to OTel/Langfuse later without changing call sites.

Vocabulary: *span/trace, observability vs monitoring, token accounting, cost-per-query, p50/p95.*
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Approximate USD per 1,000,000 tokens as (input, output). Prices drift — update as needed; unknown
# models fall through to 0.0 rather than guessing.
MODEL_PRICING: Dict[str, tuple] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "text-embedding-3-small": (0.02, 0.0),
    "text-embedding-3-large": (0.13, 0.0),
    "claude-3-5-haiku": (0.80, 4.00),
    "claude-3-5-sonnet": (3.00, 15.00),
    "claude-3-opus": (15.00, 75.00),
}


def _lookup_price(model: str) -> tuple:
    if not model:
        return (0.0, 0.0)
    if model in MODEL_PRICING:
        return MODEL_PRICING[model]
    # Prefix match so dated ids resolve (e.g. "claude-3-5-sonnet-20241022" -> "claude-3-5-sonnet").
    for key, price in MODEL_PRICING.items():
        if model.startswith(key):
            return price
    return (0.0, 0.0)


def estimate_cost(model: str, prompt_tokens: int = 0, completion_tokens: int = 0) -> float:
    """USD estimate for a call. Returns 0.0 for unknown models (fail-open — never guesses high)."""
    in_price, out_price = _lookup_price(model)
    return round(
        (prompt_tokens / 1_000_000) * in_price + (completion_tokens / 1_000_000) * out_price, 6
    )


@dataclass
class Span:
    name: str
    duration_ms: float
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class QueryTrace:
    """A structured record of one query's execution: stage timings, chunks, tokens, cost."""

    spans: List[Span] = field(default_factory=list)
    retrieved: List[Dict[str, Any]] = field(default_factory=list)
    model: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    cost_usd: float = 0.0

    @contextmanager
    def span(self, name: str, **metadata):
        """Time a block. Works around ``await``: ``__exit__`` runs after the awaited call returns."""
        start = time.time()
        try:
            yield
        finally:
            self.add_span(name, (time.time() - start) * 1000, **metadata)

    def add_span(self, name: str, duration_ms: float, **metadata) -> None:
        """Record an already-measured span (for durations timed elsewhere)."""
        self.spans.append(Span(name=name, duration_ms=duration_ms, metadata=metadata))

    def record_chunks(self, chunks) -> None:
        self.retrieved = [
            {"chunk_id": c.chunk_id, "document_id": c.document_id, "score": round(float(c.score), 4)}
            for c in chunks
        ]

    def record_usage(
        self,
        model: Optional[str],
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
    ) -> None:
        self.model = model
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = (
            total_tokens
            if total_tokens is not None
            else ((prompt_tokens or 0) + (completion_tokens or 0)) or None
        )
        if model and (prompt_tokens or completion_tokens):
            self.cost_usd = estimate_cost(model, prompt_tokens or 0, completion_tokens or 0)

    @property
    def total_ms(self) -> float:
        return round(sum(s.duration_ms for s in self.spans), 2)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_ms": self.total_ms,
            "spans": [
                {
                    "name": s.name,
                    "duration_ms": round(s.duration_ms, 2),
                    **({"metadata": s.metadata} if s.metadata else {}),
                }
                for s in self.spans
            ],
            "retrieved": self.retrieved,
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
        }

    def log_summary(self, logger) -> None:
        """Emit a one-line structured summary — observability for every query, flag or not."""
        logger.info(
            "rag_query_trace total_ms=%.1f n_chunks=%d model=%s tokens=%s cost_usd=%s spans=[%s]",
            self.total_ms,
            len(self.retrieved),
            self.model,
            self.total_tokens,
            self.cost_usd,
            " ".join(f"{s.name}={s.duration_ms:.0f}ms" for s in self.spans),
        )
