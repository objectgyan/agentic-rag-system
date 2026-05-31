"""Tests for contextual compression (A2)."""

import types

import pytest

from app.services.rag import compressor as compressor_mod
from app.services.rag.compressor import ContextualCompressor
from app.services.rag.retriever import RetrievedChunk


def _chunk(cid, content="original long text"):
    return RetrievedChunk(chunk_id=cid, document_id="d", content=content, score=0.5)


def _fake_client(content=None, raise_exc=None):
    class _Client:
        @property
        def chat(self):
            return self

        @property
        def completions(self):
            return self

        async def create(self, **kwargs):
            if raise_exc:
                raise raise_exc
            msg = types.SimpleNamespace(content=content)
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    return lambda: _Client()


@pytest.mark.asyncio
async def test_relevant_chunks_are_compressed(monkeypatch):
    monkeypatch.setattr(compressor_mod, "openai_client", _fake_client(content="distilled answer"))
    out = await ContextualCompressor().compress("q", [_chunk("a"), _chunk("b")])
    assert [c.content for c in out] == ["distilled answer", "distilled answer"]
    # chunk identity/metadata is preserved, only content changes
    assert [c.chunk_id for c in out] == ["a", "b"]


@pytest.mark.asyncio
async def test_irrelevant_chunks_are_dropped(monkeypatch):
    monkeypatch.setattr(compressor_mod, "openai_client", _fake_client(content="IRRELEVANT"))
    out = await ContextualCompressor().compress("q", [_chunk("a"), _chunk("b")])
    assert out == []


@pytest.mark.asyncio
async def test_failed_chunk_is_kept_uncompressed(monkeypatch):
    monkeypatch.setattr(
        compressor_mod, "openai_client", _fake_client(raise_exc=RuntimeError("LLM down"))
    )
    original = _chunk("a", content="keep me")
    out = await ContextualCompressor().compress("q", [original])
    assert len(out) == 1 and out[0].content == "keep me"


@pytest.mark.asyncio
async def test_empty_input():
    assert await ContextualCompressor().compress("q", []) == []
