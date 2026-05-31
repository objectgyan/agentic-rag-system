"""Test that conversation history reaches the generator (C1)."""

import pytest

from app.services.rag import pipeline as pipeline_mod
from app.services.rag.pipeline import RAGPipeline


@pytest.mark.asyncio
async def test_pipeline_forwards_history_to_generator(monkeypatch):
    p = RAGPipeline(db=object(), tenant_id="t", user_id="u")
    captured = {}

    class _Gen:
        def __init__(self, *args, **kwargs):
            pass

        async def generate(self, query, chunks, conversation_history=None):
            captured["history"] = conversation_history
            return {"answer": "ok", "model_used": "t", "tokens_used": 1}

    async def empty_retrieve(*args, **kwargs):
        return []

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(p.retriever, "retrieve", empty_retrieve)
    monkeypatch.setattr(pipeline_mod, "GenerationService", _Gen)
    monkeypatch.setattr(p, "_track_usage", noop)

    history = [{"role": "user", "content": "My name is Ada"}]
    await p.query("what is my name?", include_citations=False, conversation_history=history)

    assert captured["history"] == history


@pytest.mark.asyncio
async def test_pipeline_history_defaults_to_none(monkeypatch):
    p = RAGPipeline(db=object(), tenant_id="t", user_id="u")
    captured = {}

    class _Gen:
        def __init__(self, *args, **kwargs):
            pass

        async def generate(self, query, chunks, conversation_history=None):
            captured["history"] = conversation_history
            return {"answer": "ok", "model_used": "t", "tokens_used": 1}

    async def empty_retrieve(*args, **kwargs):
        return []

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(p.retriever, "retrieve", empty_retrieve)
    monkeypatch.setattr(pipeline_mod, "GenerationService", _Gen)
    monkeypatch.setattr(p, "_track_usage", noop)

    await p.query("hello", include_citations=False)

    assert captured["history"] is None
