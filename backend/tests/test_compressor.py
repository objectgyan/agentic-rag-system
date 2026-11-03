"""Tests for contextual compression (A2)."""

import pytest

from app.llm import FakeLLM, use_llm
from app.services.rag.compressor import ContextualCompressor
from app.services.rag.retriever import RetrievedChunk


def _chunk(cid, content="original long text"):
    return RetrievedChunk(chunk_id=cid, document_id="d", content=content, score=0.5)


@pytest.mark.asyncio
async def test_relevant_chunks_are_compressed():
    with use_llm(FakeLLM("distilled answer")):
        out = await ContextualCompressor().compress("q", [_chunk("a"), _chunk("b")])
    assert [c.content for c in out] == ["distilled answer", "distilled answer"]
    # chunk identity/metadata is preserved, only content changes
    assert [c.chunk_id for c in out] == ["a", "b"]


@pytest.mark.asyncio
async def test_irrelevant_chunks_are_dropped():
    with use_llm(FakeLLM("IRRELEVANT")):
        out = await ContextualCompressor().compress("q", [_chunk("a"), _chunk("b")])
    assert out == []


@pytest.mark.asyncio
async def test_failed_chunk_is_kept_uncompressed():
    original = _chunk("a", content="keep me")
    with use_llm(FakeLLM(RuntimeError("LLM down"))):
        out = await ContextualCompressor().compress("q", [original])
    assert len(out) == 1 and out[0].content == "keep me"


@pytest.mark.asyncio
async def test_empty_input():
    assert await ContextualCompressor().compress("q", []) == []
