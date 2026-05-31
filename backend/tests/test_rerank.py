"""Tests for re-ranking provider routing + local cross-encoder (no model download)."""

import pytest

from app.core.config import settings
from app.services.rag import retriever as retriever_mod
from app.services.rag.retriever import HybridRetriever, RetrievedChunk


def _chunk(cid):
    return RetrievedChunk(chunk_id=cid, document_id="d", content=f"text-{cid}", score=0.0)


def _retriever():
    return HybridRetriever(db=None, tenant_id="t", embedding_service=object())


@pytest.mark.asyncio
async def test_rerank_is_noop_without_any_provider(monkeypatch):
    monkeypatch.setattr(settings, "cohere_api_key", None)
    monkeypatch.setattr(settings, "local_reranker_model", None)
    chunks = [_chunk("a"), _chunk("b"), _chunk("c")]
    out = await _retriever()._rerank("q", chunks, top_k=2)
    assert [c.chunk_id for c in out] == ["a", "b"]  # original order, truncated


@pytest.mark.asyncio
async def test_local_reranker_reorders_by_score(monkeypatch):
    monkeypatch.setattr(settings, "cohere_api_key", None)
    monkeypatch.setattr(settings, "local_reranker_model", "fake/model")

    class _FakeModel:
        def predict(self, pairs):
            # Score "b" highest regardless of input order.
            return [0.9 if doc.endswith("b") else 0.1 for _query, doc in pairs]

    monkeypatch.setattr(retriever_mod, "_get_local_reranker", lambda name: _FakeModel())

    out = await _retriever()._rerank("q", [_chunk("a"), _chunk("b"), _chunk("c")], top_k=2)
    assert out[0].chunk_id == "b"
    assert len(out) == 2


@pytest.mark.asyncio
async def test_local_reranker_failure_falls_back(monkeypatch):
    monkeypatch.setattr(settings, "cohere_api_key", None)
    monkeypatch.setattr(settings, "local_reranker_model", "fake/model")

    def _boom(_name):
        raise RuntimeError("model load failed")

    monkeypatch.setattr(retriever_mod, "_get_local_reranker", _boom)

    chunks = [_chunk("a"), _chunk("b")]
    out = await _retriever()._rerank("q", chunks, top_k=2)
    assert [c.chunk_id for c in out] == ["a", "b"]  # fused order preserved
