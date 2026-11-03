"""Tests for multi-hop retrieval (A1)."""

import pytest

from app.llm import FakeLLM, use_llm
from app.services.rag.multihop import MultiHopRetriever
from app.services.rag.retriever import RetrievedChunk


def _chunk(cid):
    return RetrievedChunk(chunk_id=cid, document_id="d", content=f"content {cid}", score=0.5)


class _FakeRetriever:
    """Returns a distinct chunk per query so we can see accumulation across hops."""

    def __init__(self):
        self.calls = []

    async def retrieve(self, query, collection_ids=None, top_k=5, use_reranking=True):
        self.calls.append(query)
        return [_chunk(f"q{len(self.calls)}")]


@pytest.mark.asyncio
async def test_multi_hop_follows_then_stops():
    # hop 1 asks a follow-up, hop 2 says SUFFICIENT.
    retriever = _FakeRetriever()

    with use_llm(FakeLLM(["who manages her?", "SUFFICIENT"])):
        chunks, hops = await MultiHopRetriever(retriever).retrieve("q", max_hops=3)

    assert hops == ["who manages her?"]
    # initial query + one follow-up = 2 retrievals, accumulated + de-duplicated.
    assert retriever.calls == ["q", "who manages her?"]
    assert {c.chunk_id for c in chunks} == {"q1", "q2"}


@pytest.mark.asyncio
async def test_multi_hop_respects_max_hops():
    # Always asks for more, but max_hops caps it.
    retriever = _FakeRetriever()

    with use_llm(FakeLLM("more")):
        _, hops = await MultiHopRetriever(retriever).retrieve("q", max_hops=2)

    assert len(hops) == 2  # initial + 2 follow-ups = 3 retrievals
    assert len(retriever.calls) == 3


@pytest.mark.asyncio
async def test_multi_hop_stops_on_llm_error():
    retriever = _FakeRetriever()

    with use_llm(FakeLLM(RuntimeError("llm down"))):
        chunks, hops = await MultiHopRetriever(retriever).retrieve("q", max_hops=3)

    # Failure in the reasoning step just ends hopping; the initial retrieval still stands.
    assert hops == []
    assert {c.chunk_id for c in chunks} == {"q1"}
