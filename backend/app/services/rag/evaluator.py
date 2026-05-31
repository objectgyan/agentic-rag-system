"""RAG evaluation metrics: faithfulness, relevance, context precision, completeness."""

from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.core.llm_clients import openai_client


class RAGEvaluator:
    """Built-in evaluation metrics for RAG quality assessment."""

    def __init__(self, model: Optional[str] = None):
        self.model = model or settings.default_llm_model

    async def evaluate(
        self, query: str, answer: str, contexts: List[str], ground_truth: Optional[str] = None
    ) -> Dict[str, Any]:
        """Run all evaluation metrics."""
        results = {}
        results["faithfulness"] = await self.faithfulness(answer, contexts)
        results["relevance"] = await self.context_relevance(query, contexts)
        results["precision"] = await self.context_precision(query, contexts)
        if ground_truth:
            results["completeness"] = await self.answer_completeness(answer, ground_truth)
        results["overall"] = sum(v for v in results.values() if isinstance(v, float)) / len(results)
        return results

    async def faithfulness(self, answer: str, contexts: List[str]) -> float:
        """Is the answer grounded in the retrieved context? Score 0-1."""
        client = openai_client()

        context = "\n---\n".join(contexts)
        response = await client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": (
                    "You are an evaluator. Given an answer and source contexts, determine what fraction "
                    "of claims in the answer are supported by the contexts. Return ONLY a number between "
                    "0.0 and 1.0 where 1.0 means every claim is fully supported."
                )},
                {"role": "user", "content": f"Contexts:\n{context}\n\nAnswer:\n{answer}"},
            ],
            temperature=0,
            max_tokens=10,
        )
        try:
            return float(response.choices[0].message.content.strip())
        except ValueError:
            return 0.5

    async def context_relevance(self, query: str, contexts: List[str]) -> float:
        """Are the retrieved documents relevant to the query? Score 0-1."""
        client = openai_client()

        context = "\n---\n".join(contexts)
        response = await client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": (
                    "You are an evaluator. Given a query and retrieved contexts, rate how relevant "
                    "the contexts are to answering the query. Return ONLY a number between 0.0 and 1.0."
                )},
                {"role": "user", "content": f"Query:\n{query}\n\nContexts:\n{context}"},
            ],
            temperature=0,
            max_tokens=10,
        )
        try:
            return float(response.choices[0].message.content.strip())
        except ValueError:
            return 0.5

    async def context_precision(self, query: str, contexts: List[str]) -> float:
        """How precise is the context — is there minimal noise? Score 0-1."""
        client = openai_client()

        numbered = "\n".join(f"[{i+1}] {c[:300]}" for i, c in enumerate(contexts))
        response = await client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": (
                    "Given a query and numbered context passages, identify which passages are "
                    "directly useful for answering the query. Return ONLY the fraction of useful "
                    "passages as a number between 0.0 and 1.0."
                )},
                {"role": "user", "content": f"Query:\n{query}\n\nPassages:\n{numbered}"},
            ],
            temperature=0,
            max_tokens=10,
        )
        try:
            return float(response.choices[0].message.content.strip())
        except ValueError:
            return 0.5

    async def answer_completeness(self, answer: str, ground_truth: str) -> float:
        """Does the answer cover all aspects of the ground truth? Score 0-1."""
        client = openai_client()

        response = await client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": (
                    "Compare the given answer to the ground truth. Rate how completely the answer "
                    "covers the information in the ground truth. Return ONLY a number between 0.0 and 1.0."
                )},
                {"role": "user", "content": f"Ground Truth:\n{ground_truth}\n\nAnswer:\n{answer}"},
            ],
            temperature=0,
            max_tokens=10,
        )
        try:
            return float(response.choices[0].message.content.strip())
        except ValueError:
            return 0.5
