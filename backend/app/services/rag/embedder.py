"""Embedding service supporting multiple providers."""

from typing import List, Optional

from app.core.config import settings
from app.core.llm_clients import cohere_client, openai_client


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
        """Embed a single query, with a fail-soft Redis cache for repeated queries (item 5)."""
        if settings.embedding_cache_enabled:
            cached = await self._cache_get(query)
            if cached is not None:
                return cached

        results = await self.embed_texts([query])
        vec = results[0] if results else []

        if settings.embedding_cache_enabled and vec:
            await self._cache_set(query, vec)
        return vec

    def _cache_key(self, text: str) -> str:
        import hashlib

        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return f"emb:{self.model}:{digest}"

    async def _cache_get(self, text: str) -> Optional[List[float]]:
        """Look up a cached embedding. Never raises — a cache miss/outage just means recompute."""
        try:
            import json

            from app.core.redis import redis_client

            raw = await redis_client.get(self._cache_key(text))
            return json.loads(raw) if raw else None
        except Exception:
            return None

    async def _cache_set(self, text: str, vec: List[float]) -> None:
        try:
            import json

            from app.core.redis import redis_client

            await redis_client.set(
                self._cache_key(text), json.dumps(vec), ex=settings.embedding_cache_ttl_seconds
            )
        except Exception:
            pass

    async def _embed_openai(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings using OpenAI API."""
        import tiktoken

        client = openai_client()

        # Tokenizer for the embedding model
        encoding = tiktoken.get_encoding("cl100k_base")
        max_tokens = 8191  # OpenAI embedding models have 8191 token limit

        # Truncate texts that are too long
        processed_texts = []
        for text in texts:
            tokens = encoding.encode(text)
            if len(tokens) > max_tokens:
                # Truncate to max tokens
                truncated_tokens = tokens[:max_tokens]
                text = encoding.decode(truncated_tokens)
            processed_texts.append(text)

        # Batch in chunks of 100
        all_embeddings = []
        for i in range(0, len(processed_texts), 100):
            batch = processed_texts[i:i + 100]
            response = await client.embeddings.create(
                model=self.model,
                input=batch,
            )
            all_embeddings.extend([e.embedding for e in response.data])
        return all_embeddings

    async def _embed_cohere(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings using Cohere API."""
        client = cohere_client()

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
