"""RAG evaluation harness.

The quality loop that lets us answer "did this change help?" with a number instead of a vibe.
Three layers:

- ``metrics``  — pure retrieval-quality functions (recall@k, precision@k, MRR, nDCG). No I/O,
  fully unit-testable in CI.
- ``dataset``  — the golden set: human-labeled (question -> relevant ids, ideal answer) cases.
- ``runner``   — runs a dataset through the live retriever + pipeline and aggregates a report.

Run it:  ``python -m app.services.rag.evaluation <dataset.json> --tenant <id> --user <id>``

Note: ``metrics`` and ``dataset`` are pure and import cheaply. ``runner`` (and the ``EvaluationRunner``
/ ``CaseResult`` / ``EvalReport`` re-exports) pull in the DB + pipeline stack, so they are imported
**lazily** via ``__getattr__`` — importing this package for its metrics alone stays dependency-free.
"""

from app.services.rag.evaluation.dataset import GoldenCase, load_golden_dataset
from app.services.rag.evaluation.metrics import (
    hit_at_k,
    mean_scores,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    retrieval_scores,
)

__all__ = [
    "GoldenCase",
    "load_golden_dataset",
    "hit_at_k",
    "mean_scores",
    "ndcg_at_k",
    "precision_at_k",
    "recall_at_k",
    "reciprocal_rank",
    "retrieval_scores",
    # lazy (see __getattr__):
    "CaseResult",
    "EvalReport",
    "EvaluationRunner",
]

_LAZY = {"CaseResult", "EvalReport", "EvaluationRunner"}


def __getattr__(name: str):
    """Lazily expose the runner symbols so metric-only use doesn't import the pipeline stack."""
    if name in _LAZY:
        from app.services.rag.evaluation import runner

        return getattr(runner, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
