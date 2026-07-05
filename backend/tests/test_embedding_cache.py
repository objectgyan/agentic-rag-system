"""Tests for the query embedding cache (item 5). No real Redis or embedding API."""

import pytest

from app.core import redis as redis_mod
from app.core.config import settings
from app.services.rag.embedder import EmbeddingService


class _FakeRedis:
    def __init__(self):
        self.store = {}
        self.gets = 0
        self.sets = 0

    async def get(self, k):
        self.gets += 1
        return self.store.get(k)

    async def set(self, k, v, ex=None):
        self.sets += 1
        self.store[k] = v


class _BoomRedis:
    async def get(self, k):
        raise RuntimeError("redis down")

    async def set(self, k, v, ex=None):
        raise RuntimeError("redis down")


def _service_with_counting_embed(monkeypatch):
    svc = EmbeddingService(model="text-embedding-3-small")
    calls = {"n": 0}

    async def fake_embed_texts(texts):
        calls["n"] += 1
        return [[0.1, 0.2, 0.3] for _ in texts]

    monkeypatch.setattr(svc, "embed_texts", fake_embed_texts)
    return svc, calls


def test_cache_key_stable_and_model_scoped():
    a = EmbeddingService(model="m1")
    b = EmbeddingService(model="m2")
    assert a._cache_key("hello") == a._cache_key("hello")
    assert a._cache_key("hello") != a._cache_key("world")
    assert a._cache_key("hello") != b._cache_key("hello")  # model-scoped


@pytest.mark.asyncio
async def test_second_query_served_from_cache(monkeypatch):
    monkeypatch.setattr(redis_mod, "redis_client", _FakeRedis())
    monkeypatch.setattr(settings, "embedding_cache_enabled", True)
    svc, calls = _service_with_counting_embed(monkeypatch)

    v1 = await svc.embed_query("what is the policy?")
    v2 = await svc.embed_query("what is the policy?")

    assert v1 == v2 == [0.1, 0.2, 0.3]
    assert calls["n"] == 1  # embedding API hit only once; second call cached


@pytest.mark.asyncio
async def test_distinct_queries_both_computed(monkeypatch):
    monkeypatch.setattr(redis_mod, "redis_client", _FakeRedis())
    monkeypatch.setattr(settings, "embedding_cache_enabled", True)
    svc, calls = _service_with_counting_embed(monkeypatch)

    await svc.embed_query("query one")
    await svc.embed_query("query two")
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_cache_failure_is_soft(monkeypatch):
    monkeypatch.setattr(redis_mod, "redis_client", _BoomRedis())
    monkeypatch.setattr(settings, "embedding_cache_enabled", True)
    svc, calls = _service_with_counting_embed(monkeypatch)

    v = await svc.embed_query("x")
    assert v == [0.1, 0.2, 0.3]  # still works despite Redis errors
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_cache_disabled_skips_redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(redis_mod, "redis_client", fake)
    monkeypatch.setattr(settings, "embedding_cache_enabled", False)
    svc, calls = _service_with_counting_embed(monkeypatch)

    await svc.embed_query("x")
    await svc.embed_query("x")
    assert calls["n"] == 2       # no caching
    assert fake.gets == 0        # Redis never touched
