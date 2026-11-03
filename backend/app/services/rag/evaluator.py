"""RAG evaluation metrics (LLM-as-judge): faithfulness, relevance, context precision, completeness."""

import asyncio
import re
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.llm import get_llm

_NUMBER = re.compile(r"(?<![\d.])(?:0(?:\.\d+)?|1(?:\.0+)?)(?![\d.])")

_FAITHFULNESS = (
    "You are an evaluator. Given an answer and source contexts, determine what fraction "
    "of claims in the answer are supported by the contexts. Return ONLY a number between "
    "0.0 and 1.0 where 1.0 means every claim is fully supported."
)
_RELEVANCE = (
    "You are an evaluator. Given a query and retrieved contexts, rate how relevant "
    "the contexts are to answering the query. Return ONLY a number between 0.0 and 1.0."
)
_PRECISION = (
    "Given a query and numbered context passages, identify which passages are "
    "directly useful for answering the query. Return ONLY the fraction of useful "
    "passages as a number between 0.0 and 1.0."
)
_COMPLETENESS = (
    "Compare the given answer to the ground truth. Rate how completely the answer "
    "covers the information in the ground truth. Return ONLY a number between 0.0 and 1.0."
)


def parse_score(text: str, default: float = 0.5) -> float:
    """First number in [0, 1] in the judge's reply; ``default`` if there is none.

    Judges don't always obey "return ONLY a number" ("Score: 0.8", "0.8 — mostly supported"),
    so a strict ``float(text)`` would silently score those as the default.
    """
    m = _NUMBER.search(text or "")
    return float(m.group(0)) if m else default


class RAGEvaluator:
    """Built-in evaluation metrics for RAG quality assessment."""

    def __init__(self, model: Optional[str] = None):
        # The judge defaults to the cheap helper model; pass a stronger one for offline evals.
        self.model = model or settings.default_fast_model

    async def _score(self, system: str, user: str) -> float:
        resp = await get_llm(self.model).complete(
            [{"role": "user", "content": user}], system=system, temperature=0, max_tokens=16, effort="low"
        )
        return parse_score(resp.text)

    async def evaluate(
        self, query: str, answer: str, contexts: List[str], ground_truth: Optional[str] = None
    ) -> Dict[str, Any]:
        """Run all evaluation metrics concurrently."""
        names = ["faithfulness", "relevance", "precision"]
        jobs = [
            self.faithfulness(answer, contexts),
            self.context_relevance(query, contexts),
            self.context_precision(query, contexts),
        ]
        if ground_truth:
            names.append("completeness")
            jobs.append(self.answer_completeness(answer, ground_truth))
        results: Dict[str, Any] = dict(zip(names, await asyncio.gather(*jobs)))
        results["overall"] = sum(results.values()) / len(results)
        return results

    async def faithfulness(self, answer: str, contexts: List[str]) -> float:
        """Is the answer grounded in the retrieved context? Score 0-1."""
        context = "\n---\n".join(contexts)
        return await self._score(_FAITHFULNESS, f"Contexts:\n{context}\n\nAnswer:\n{answer}")

    async def context_relevance(self, query: str, contexts: List[str]) -> float:
        """Are the retrieved documents relevant to the query? Score 0-1."""
        context = "\n---\n".join(contexts)
        return await self._score(_RELEVANCE, f"Query:\n{query}\n\nContexts:\n{context}")

    async def context_precision(self, query: str, contexts: List[str]) -> float:
        """How precise is the context — is there minimal noise? Score 0-1."""
        numbered = "\n".join(f"[{i+1}] {c[:300]}" for i, c in enumerate(contexts))
        return await self._score(_PRECISION, f"Query:\n{query}\n\nPassages:\n{numbered}")

    async def answer_completeness(self, answer: str, ground_truth: str) -> float:
        """Does the answer cover all aspects of the ground truth? Score 0-1."""
        return await self._score(_COMPLETENESS, f"Ground Truth:\n{ground_truth}\n\nAnswer:\n{answer}")
