"""Tests for multi-hop retrieval (A1)."""

import types

import pytest

from app.services.rag import multihop as multihop_mod
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


def _fake_llm(replies):
    """openai_client() factory whose create() returns successive canned replies."""
    state = {"i": 0}

    class _Client:
        @property
        def chat(self):
            return self

        @property
        def completions(self):
            return self

        async def create(self, **kwargs):
            reply = replies[state["i"]]
            state["i"] += 1
            msg = types.SimpleNamespace(content=reply)
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    return lambda: _Client()


@pytest.mark.asyncio
async def test_multi_hop_follows_then_stops(monkeypatch):
    # hop 1 asks a follow-up, hop 2 says SUFFICIENT.
    monkeypatch.setattr(multihop_mod, "openai_client", _fake_llm(["who manages her?", "SUFFICIENT"]))
    retriever = _FakeRetriever()

    chunks, hops = await MultiHopRetriever(retriever).retrieve("q", max_hops=3)

    assert hops == ["who manages her?"]
    # initial query + one follow-up = 2 retrievals, accumulated + de-duplicated.
    assert retriever.calls == ["q", "who manages her?"]
    assert {c.chunk_id for c in chunks} == {"q1", "q2"}


@pytest.mark.asyncio
async def test_multi_hop_respects_max_hops(monkeypatch):
    # Always asks for more, but max_hops caps it.
    monkeypatch.setattr(multihop_mod, "openai_client", _fake_llm(["more", "more", "more", "more"]))
    retriever = _FakeRetriever()

    _, hops = await MultiHopRetriever(retriever).retrieve("q", max_hops=2)

    assert len(hops) == 2  # initial + 2 follow-ups = 3 retrievals
    assert len(retriever.calls) == 3


@pytest.mark.asyncio
async def test_multi_hop_stops_on_llm_error(monkeypatch):
    def _boom():
        raise RuntimeError("llm down")

    monkeypatch.setattr(multihop_mod, "openai_client", _boom)
    retriever = _FakeRetriever()

    chunks, hops = await MultiHopRetriever(retriever).retrieve("q", max_hops=3)

    # Failure in the reasoning step just ends hopping; the initial retrieval still stands.
    assert hops == []
    assert {c.chunk_id for c in chunks} == {"q1"}
