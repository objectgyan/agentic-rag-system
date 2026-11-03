"""Query enhancement: multi-query expansion, HyDE, query decomposition."""

from typing import List, Optional

from app.core.config import settings
from app.llm import get_llm


def _lines(text: str) -> List[str]:
    return [v.strip().lstrip("0123456789.-) ") for v in text.strip().split("\n") if v.strip()]


class QueryEnhancer:
    """Enhance queries for better retrieval. Uses the cheap helper model by default."""

    def __init__(self, model: Optional[str] = None):
        self.model = model or settings.default_fast_model

    async def _ask(self, system: str, query: str, temperature: float) -> str:
        resp = await get_llm(self.model).complete(
            [{"role": "user", "content": query}],
            system=system,
            max_tokens=300,
            temperature=temperature,
            effort="low",
        )
        return resp.text.strip()

    async def multi_query_expand(self, query: str) -> List[str]:
        """Generate multiple query variations for broader retrieval."""
        return _lines(await self._ask(
            "Generate 3 different versions of the given question to retrieve "
            "relevant documents from a vector database. Provide diverse perspectives "
            "of the same question. Return each version on a new line, nothing else.",
            query,
            temperature=0.7,
        ))

    async def hyde_generate(self, query: str) -> str:
        """Hypothetical Document Embeddings — generate a hypothetical answer to embed."""
        return await self._ask(
            "Write a short, factual passage that would answer the given question. "
            "Write as if this is an excerpt from a relevant document. Be specific and detailed.",
            query,
            temperature=0.3,
        )

    async def decompose_query(self, query: str) -> List[str]:
        """Decompose a complex query into simpler sub-queries."""
        return _lines(await self._ask(
            "Break down the following complex question into 2-4 simpler sub-questions "
            "that together answer the original question. Return each sub-question on a new line.",
            query,
            temperature=0.3,
        ))
