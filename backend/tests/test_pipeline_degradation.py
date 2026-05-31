"""Test graceful degradation in the RAG pipeline (F12).

If an optional query-enhancement step fails, the query should still return an
answer and record what degraded — not blow up the whole request.
"""

import pytest

from app.services.rag import pipeline as pipeline_mod
from app.services.rag.pipeline import RAGPipeline


class _FakeGenerator:
    def __init__(self, *args, **kwargs):
        pass

    async def generate(self, query, chunks, conversation_history=None, graph_facts=None):
        return {"answer": "fallback answer", "model_used": "test", "tokens_used": 1}


@pytest.mark.asyncio
async def test_query_degrades_when_hyde_fails(monkeypatch):
    p = RAGPipeline(db=object(), tenant_id="t", user_id="u")

    async def boom(_query):
        raise RuntimeError("enhancer LLM unavailable")

    async def empty_retrieve(*args, **kwargs):
        return []

    async def noop_usage(*args, **kwargs):
        return None

    monkeypatch.setattr(p.query_enhancer, "hyde_generate", boom)
    monkeypatch.setattr(p.retriever, "retrieve", empty_retrieve)
    monkeypatch.setattr(pipeline_mod, "GenerationService", _FakeGenerator)
    monkeypatch.setattr(p, "_track_usage", noop_usage)

    result = await p.query("hello", use_hyde=True, include_citations=False)

    # The request succeeded on the fallback path, and the degradation is recorded.
    assert result["answer"] == "fallback answer"
    assert "hyde" in result["degraded"]
