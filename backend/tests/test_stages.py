"""Pipeline stages: custom stages plug in, failures degrade, and streaming == blocking."""

import pytest

from app.llm import FakeLLM, use_llm
from app.services.rag.pipeline import RAGPipeline
from app.services.rag.retriever import RetrievedChunk
from app.services.rag.stages import DEFAULT_STAGES, QueryContext


def _chunk(cid):
    return RetrievedChunk(chunk_id=cid, document_id="d", content=f"text {cid}", score=0.5)


def _pipeline(monkeypatch, stages=None):
    p = RAGPipeline(db=object(), tenant_id="t", user_id="u", stages=stages)
    seen = {"queries": [], "usage": []}

    async def retrieve(query, collection_ids=None, top_k=5, use_reranking=True):
        seen["queries"].append(query)
        return [_chunk("a"), _chunk("b")]

    async def track(tokens, model, prompt_tokens=None, completion_tokens=None):
        seen["usage"].append(tokens)

    async def hyde(q):
        return f"hypothetical answer to {q}"

    async def citations(chunks, answer):
        return [{"chunk_id": c.chunk_id} for i, c in enumerate(chunks, 1) if f"[Source {i}]" in answer]

    monkeypatch.setattr(p.retriever, "retrieve", retrieve)
    monkeypatch.setattr(p, "_track_usage", track)
    monkeypatch.setattr(p.query_enhancer, "hyde_generate", hyde)
    monkeypatch.setattr(p, "_citations", citations)
    return p, seen


class _DropChunk:
    """A client-specific stage: e.g. an ACL filter removing chunks the user may not see."""

    name = "acl_filter"
    fail_soft = False

    def applies(self, ctx: QueryContext) -> bool:
        return True

    async def run(self, ctx: QueryContext) -> None:
        ctx.chunks = [c for c in ctx.chunks if c.chunk_id != "b"]


class _Broken:
    name = "flaky_enricher"
    fail_soft = True

    def applies(self, ctx):
        return True

    async def run(self, ctx):
        raise RuntimeError("down")


@pytest.mark.asyncio
async def test_custom_stage_runs_in_order(monkeypatch):
    p, _ = _pipeline(monkeypatch, stages=[*DEFAULT_STAGES, _DropChunk()])
    fake = FakeLLM("answer [Source 1]")
    with use_llm(fake):
        out = await p.query("q", trace=True)
    assert [c["chunk_id"] for c in out["citations"]] == ["a"]
    # only chunk "a" reached the model
    assert "text b" not in fake.calls[0]["messages"][-1]["content"]
    # every stage that ran is traced automatically
    assert "stage.acl_filter" in [s["name"] for s in out["trace"]["spans"]]


@pytest.mark.asyncio
async def test_fail_soft_stage_is_recorded_as_degraded(monkeypatch):
    p, _ = _pipeline(monkeypatch, stages=[*DEFAULT_STAGES, _Broken()])
    with use_llm(FakeLLM("fine")):
        out = await p.query("q")
    assert out["answer"] == "fine" and out["degraded"] == ["flaky_enricher"]


@pytest.mark.asyncio
async def test_unknown_option_is_rejected(monkeypatch):
    p, _ = _pipeline(monkeypatch)
    with pytest.raises(TypeError, match="use_hdye"):
        await p.query("q", use_hdye=True)


@pytest.mark.asyncio
async def test_streaming_runs_the_same_stages_and_is_billed(monkeypatch):
    # Before the stage refactor, query_stream ignored HyDE (and every other option) and never
    # recorded usage — streamed chats were free.
    p, seen = _pipeline(monkeypatch)
    with use_llm(FakeLLM("streamed answer [Source 2]")):
        events = [e async for e in p.query_stream("q", use_hyde=True)]

    assert seen["queries"] == ["hypothetical answer to q"]
    assert seen["usage"] == [15]  # FakeLLM reports 10 in + 5 out
    types = [e["type"] for e in events]
    assert types[-2:] == ["citations", "done"] and set(types[:-2]) == {"token"}
    assert "".join(e["content"] for e in events if e["type"] == "token").strip() == "streamed answer [Source 2]"
    assert events[-2]["citations"] == [{"chunk_id": "b"}]


@pytest.mark.asyncio
async def test_refusal_becomes_a_polite_answer(monkeypatch):
    from app.llm import LLMResponse
    from app.services.rag.generator import REFUSAL_MESSAGE

    p, _ = _pipeline(monkeypatch)
    with use_llm(FakeLLM(LLMResponse(text="", model="m", stop_reason="refusal"))):
        out = await p.query("q")
    assert out["answer"] == REFUSAL_MESSAGE and out["citations"] == []
