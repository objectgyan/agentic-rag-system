"""The evaluation runner: run a golden dataset through the live system and aggregate a report.

It measures two things independently, because they fail in different ways:

- **Retrieval quality** — from ``HybridRetriever.retrieve()``, scored against the case's relevant
  ids with the pure metrics in ``metrics``. "Did we fetch the right chunks?"
- **Generation quality** — from ``RAGPipeline.query()``'s answer, scored by the ``RAGEvaluator``
  LLM judge (faithfulness/relevance/precision) plus cheap ``answer_must_contain`` substring checks.
  "Given context, did we write a grounded, correct answer?"

The runner needs a live, tenant-scoped DB session (RLS context set) and a tenant that already has
ingested documents. See ``__main__`` for the CLI that wires that up.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.rag.evaluation.dataset import GoldenCase
from app.services.rag.evaluation.metrics import mean_scores, retrieval_scores
from app.services.rag.evaluator import RAGEvaluator
from app.services.rag.pipeline import RAGPipeline
from app.services.rag.retriever import HybridRetriever

logger = logging.getLogger(__name__)


def _dedup_preserve_order(ids: List[str]) -> List[str]:
    """Collapse repeats but keep first-seen order — used to rank at document granularity."""
    seen: set = set()
    out: List[str] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


@dataclass
class CaseResult:
    """Scores for a single golden case."""

    id: str
    question: str
    retrieval: Optional[Dict[str, float]] = None       # None if the case has no relevance labels
    generation: Optional[Dict[str, float]] = None      # None if answer judging is off / failed
    answer_contains_pass: Optional[bool] = None        # None if no substring checks defined
    answer_preview: str = ""
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EvalReport:
    """Aggregate report across all cases — the thing you diff between runs to catch regressions."""

    cases: List[CaseResult] = field(default_factory=list)
    retrieval_means: Dict[str, float] = field(default_factory=dict)
    generation_means: Dict[str, float] = field(default_factory=dict)
    contains_pass_rate: Optional[float] = None
    n_cases: int = 0
    n_retrieval_scored: int = 0
    n_generation_scored: int = 0
    n_errors: int = 0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["cases"] = [c.to_dict() for c in self.cases]
        return d

    def summary(self) -> str:
        lines = [
            f"Evaluated {self.n_cases} case(s) — {self.n_errors} error(s)",
            f"Retrieval (n={self.n_retrieval_scored}): "
            + (", ".join(f"{k}={v:.3f}" for k, v in self.retrieval_means.items()) or "—"),
            f"Generation (n={self.n_generation_scored}): "
            + (", ".join(f"{k}={v:.3f}" for k, v in self.generation_means.items()) or "—"),
        ]
        if self.contains_pass_rate is not None:
            lines.append(f"answer_must_contain pass rate: {self.contains_pass_rate:.3f}")
        return "\n".join(lines)


class EvaluationRunner:
    """Runs golden cases against the live retriever + pipeline and aggregates an ``EvalReport``."""

    def __init__(
        self,
        db: AsyncSession,
        tenant_id: str,
        user_id: str,
        *,
        top_k: int = 5,
        model: Optional[str] = None,
        use_reranking: bool = True,
        judge_answers: bool = True,
    ):
        self.db = db
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.top_k = top_k
        self.model = model
        self.use_reranking = use_reranking
        self.retriever = HybridRetriever(db=db, tenant_id=tenant_id)
        self.pipeline = RAGPipeline(db=db, tenant_id=tenant_id, user_id=user_id)
        self.evaluator = RAGEvaluator(model=model) if judge_answers else None

    async def run_case(self, case: GoldenCase) -> CaseResult:
        try:
            # --- Retrieval quality: score the retriever's own output against the gold ids.
            chunks = await self.retriever.retrieve(
                case.question,
                collection_ids=case.collection_ids,
                top_k=self.top_k,
                use_reranking=self.use_reranking,
            )

            retrieval: Optional[Dict[str, float]] = None
            if case.relevant_chunk_ids:
                retrieved_ids = [c.chunk_id for c in chunks]
                retrieval = retrieval_scores(retrieved_ids, case.relevant_chunk_ids, self.top_k)
            elif case.relevant_document_ids:
                retrieved_ids = _dedup_preserve_order([c.document_id for c in chunks])
                retrieval = retrieval_scores(retrieved_ids, case.relevant_document_ids, self.top_k)

            # --- Generation quality: run the real pipeline, judge the answer.
            result = await self.pipeline.query(
                case.question,
                collection_ids=case.collection_ids,
                top_k=self.top_k,
                model=self.model,
                use_reranking=self.use_reranking,
            )
            answer = result.get("answer", "") or ""

            contains_pass: Optional[bool] = None
            if case.answer_must_contain:
                low = answer.lower()
                contains_pass = all(s.lower() in low for s in case.answer_must_contain)

            generation: Optional[Dict[str, float]] = None
            if self.evaluator is not None:
                contexts = [c.content for c in chunks]
                if contexts:
                    generation = await self.evaluator.evaluate(
                        case.question, answer, contexts, ground_truth=case.ground_truth
                    )

            return CaseResult(
                id=case.id or case.question[:40],
                question=case.question,
                retrieval=retrieval,
                generation=generation,
                answer_contains_pass=contains_pass,
                answer_preview=answer[:200],
            )
        except Exception as exc:  # a bad case must not abort the whole run
            logger.warning("eval case %s failed", case.id, exc_info=True)
            return CaseResult(
                id=case.id or case.question[:40], question=case.question, error=str(exc)
            )

    async def run(self, cases: List[GoldenCase]) -> EvalReport:
        results = [await self.run_case(c) for c in cases]
        return self._aggregate(results)

    def _aggregate(self, results: List[CaseResult]) -> EvalReport:
        retrieval_dicts = [r.retrieval for r in results if r.retrieval is not None]
        generation_dicts = [r.generation for r in results if r.generation is not None]
        contains = [r.answer_contains_pass for r in results if r.answer_contains_pass is not None]

        return EvalReport(
            cases=results,
            retrieval_means=mean_scores(retrieval_dicts),
            generation_means=mean_scores(generation_dicts),
            contains_pass_rate=(sum(1 for c in contains if c) / len(contains)) if contains else None,
            n_cases=len(results),
            n_retrieval_scored=len(retrieval_dicts),
            n_generation_scored=len(generation_dicts),
            n_errors=sum(1 for r in results if r.error),
        )
