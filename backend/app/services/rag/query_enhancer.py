"""Query enhancement: multi-query expansion, HyDE, query decomposition."""

from typing import List, Optional
from app.core.config import settings


class QueryEnhancer:
    """Enhance queries for better retrieval."""

    def __init__(self, model: Optional[str] = None):
        self.model = model or settings.default_llm_model

    async def multi_query_expand(self, query: str) -> List[str]:
        """Generate multiple query variations for broader retrieval."""
        import openai
        client = openai.AsyncOpenAI(api_key=settings.openai_api_key)

        response = await client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": (
                    "Generate 3 different versions of the given question to retrieve "
                    "relevant documents from a vector database. Provide diverse perspectives "
                    "of the same question. Return each version on a new line, nothing else."
                )},
                {"role": "user", "content": query},
            ],
            temperature=0.7,
            max_tokens=300,
        )

        variations = response.choices[0].message.content.strip().split("\n")
        return [v.strip().lstrip("0123456789.-) ") for v in variations if v.strip()]

    async def hyde_generate(self, query: str) -> str:
        """Hypothetical Document Embeddings — generate a hypothetical answer to embed."""
        import openai
        client = openai.AsyncOpenAI(api_key=settings.openai_api_key)

        response = await client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": (
                    "Write a short, factual passage that would answer the given question. "
                    "Write as if this is an excerpt from a relevant document. Be specific and detailed."
                )},
                {"role": "user", "content": query},
            ],
            temperature=0.3,
            max_tokens=300,
        )

        return response.choices[0].message.content.strip()

    async def decompose_query(self, query: str) -> List[str]:
        """Decompose a complex query into simpler sub-queries."""
        import openai
        client = openai.AsyncOpenAI(api_key=settings.openai_api_key)

        response = await client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": (
                    "Break down the following complex question into 2-4 simpler sub-questions "
                    "that together answer the original question. Return each sub-question on a new line."
                )},
                {"role": "user", "content": query},
            ],
            temperature=0.3,
            max_tokens=300,
        )

        subs = response.choices[0].message.content.strip().split("\n")
        return [s.strip().lstrip("0123456789.-) ") for s in subs if s.strip()]
