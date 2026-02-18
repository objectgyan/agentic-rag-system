"""Embedding service supporting multiple providers."""

from typing import List, Optional
import numpy as np
from app.core.config import settings


class EmbeddingService:
    """Generate embeddings using OpenAI, Cohere, or local models."""

    def __init__(self, model: Optional[str] = None):
        self.model = model or settings.default_embedding_model
        self._client = None

    @property
    def dimensions(self) -> int:
        dim_map = {
            "text-embedding-3-small": 1536,
            "text-embedding-3-large": 3072,
            "text-embedding-ada-002": 1536,
            "embed-english-v3.0": 1024,
            "embed-multilingual-v3.0": 1024,
        }
        return dim_map.get(self.model, settings.default_embedding_dimensions)

    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """Embed a batch of texts."""
        if not texts:
            return []

        if self.model.startswith("text-embedding"):
            return await self._embed_openai(texts)
        elif self.model.startswith("embed-"):
            return await self._embed_cohere(texts)
        else:
            return await self._embed_local(texts)

    async def embed_query(self, query: str) -> List[float]:
        """Embed a single query."""
        results = await self.embed_texts([query])
        return results[0] if results else []

    async def _embed_openai(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings using OpenAI API."""
        import openai
        client = openai.AsyncOpenAI(api_key=settings.openai_api_key)

        # Batch in chunks of 100
        all_embeddings = []
        for i in range(0, len(texts), 100):
            batch = texts[i:i + 100]
            response = await client.embeddings.create(
                model=self.model,
                input=batch,
            )
            all_embeddings.extend([e.embedding for e in response.data])
        return all_embeddings

    async def _embed_cohere(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings using Cohere API."""
        import cohere
        client = cohere.AsyncClient(api_key=settings.cohere_api_key)

        response = await client.embed(
            texts=texts,
            model=self.model,
            input_type="search_document",
        )
        return [list(e) for e in response.embeddings]

    async def _embed_local(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings using a local sentence-transformers model."""
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(self.model)
        embeddings = model.encode(texts, normalize_embeddings=True)
        return embeddings.tolist()
