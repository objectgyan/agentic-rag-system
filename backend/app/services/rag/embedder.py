"""Embedding service: one interface over OpenAI, Cohere, and local sentence-transformers models.

Documents and queries are embedded through separate entry points (``embed_texts`` vs
``embed_query``) because asymmetric models — Cohere v3+, Voyage, E5, BGE — embed them
differently (``input_type`` / instruction prefixes). Mixing them up quietly lowers recall.
"""

from typing import Dict, List, Optional

from app.core import embedding_models
from app.core.config import settings
from app.core.llm_clients import cohere_client, openai_client

# Local models are loaded once per process: loading pulls torch weights and takes seconds.
_local_models: Dict[str, object] = {}


def _get_local_model(name: str):
    if name not in _local_models:
        from sentence_transformers import SentenceTransformer

        _local_models[name] = SentenceTransformer(name)
    return _local_models[name]


class EmbeddingService:
    """Generate embeddings using OpenAI, Cohere, or local models."""

    def __init__(self, model: Optional[str] = None):
        self.model = model or settings.default_embedding_model
        self._client = None

    @property
    def dimensions(self) -> int:
        return embedding_models.known_dimensions(self.model) or settings.default_embedding_dimensions

    async def embed_texts(self, texts: List[str], input_type: str = "search_document") -> List[List[float]]:
        """Embed a batch of texts (documents by default; ``embed_query`` passes "search_query")."""
        if not texts:
            return []

        if self.model.startswith("text-embedding"):
            return await self._embed_openai(texts)
        elif self.model.startswith("embed-"):
            return await self._embed_cohere(texts, input_type)
        else:
            return await self._embed_local(texts, input_type)

    async def embed_query(self, query: str) -> List[float]:
        """Embed a single query, with a fail-soft Redis cache for repeated queries (item 5)."""
        if settings.embedding_cache_enabled:
            cached = await self._cache_get(query)
            if cached is not None:
                return cached

        results = await self.embed_texts([query], input_type="search_query")
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

    async def _embed_cohere(self, texts: List[str], input_type: str) -> List[List[float]]:
        """Generate embeddings using the Cohere v2 API (96 texts per request max)."""
        client = cohere_client()
        out: List[List[float]] = []
        for i in range(0, len(texts), 96):
            response = await client.embed(
                texts=texts[i:i + 96],
                model=self.model,
                input_type=input_type,
                embedding_types=["float"],
            )
            out.extend(list(e) for e in response.embeddings.float_)
        return out

    async def _embed_local(self, texts: List[str], input_type: str = "search_document") -> List[List[float]]:
        """Generate embeddings using a local sentence-transformers model."""
        import asyncio

        model = _get_local_model(self.model)
        query_prefix, doc_prefix = embedding_models.prefixes(self.model)
        prefix = query_prefix if input_type == "search_query" else doc_prefix
        if prefix:
            texts = [prefix + t for t in texts]
        # encode() is sync + CPU-bound; keep it off the event loop.
        embeddings = await asyncio.to_thread(model.encode, texts, normalize_embeddings=True)
        return embeddings.tolist()
