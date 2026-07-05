"""Tests for intent classification + routing (item 4). No network — a fake embedder is injected."""

import pytest

from app.services.rag import intent as intent_mod
from app.services.rag import pipeline as pipeline_mod
from app.services.rag.intent import IntentClassifier, IntentResult
from app.services.rag.pipeline import RAGPipeline

# Orthonormal basis per intent anchor; the query maps to a basis by keyword.
_INTENTS = {"question": "question", "comparison": "comparison", "chitchat": "chitchat"}
_BASIS = {"question": [1.0, 0.0, 0.0], "comparison": [0.0, 1.0, 0.0], "chitchat": [0.0, 0.0, 1.0]}


class _FakeEmbedder:
    def __init__(self):
        self.embed_texts_calls = 0

    async def embed_texts(self, texts):
        self.embed_texts_calls += 1
        return [_BASIS[t] for t in texts]

    async def embed_query(self, q):
        ql = q.lower()
        if "hello" in ql or "hi" in ql or "thanks" in ql:
            return [0.1, 0.1, 1.0]
        if "compare" in ql or "versus" in ql or " vs " in ql:
            return [0.1, 1.0, 0.1]
        return [1.0, 0.1, 0.1]


def _classifier():
    return IntentClassifier(intents=_INTENTS, embedding_service=_FakeEmbedder())


@pytest.mark.asyncio
async def test_classify_question():
    res = await _classifier().classify("what is the refund policy?")
    assert res.intent == "question"
    assert set(res.scores) == {"question", "comparison", "chitchat"}


@pytest.mark.asyncio
async def test_classify_chitchat():
    res = await _classifier().classify("hello there!")
    assert res.intent == "chitchat"


@pytest.mark.asyncio
async def test_classify_comparison():
    res = await _classifier().classify("compare plan A and plan B")
    assert res.intent == "comparison"


@pytest.mark.asyncio
async def test_anchors_embedded_once():
    clf = _classifier()
    await clf.classify("hello")
    await clf.classify("what is x")
    assert clf.embedder.embed_texts_calls == 1  # cached across calls


# --------------------------------------------------------------------------- #
# Pipeline routing behavior
# --------------------------------------------------------------------------- #

class _Gen:
    def __init__(self, *a, **k):
        pass

    async def generate(self, query, chunks, conversation_history=None, graph_facts=None,
                       conversation_summary=None):
        return {"answer": "hi!", "model_used": "t", "tokens_used": 1}


def _patch_intent(monkeypatch, label):
    class _FakeClf:
        def __init__(self, *a, **k):
            pass

        async def classify(self, q):
            return IntentResult(intent=label, scores={label: 0.9})

    monkeypatch.setattr(intent_mod, "IntentClassifier", _FakeClf)


@pytest.mark.asyncio
async def test_routing_chitchat_skips_retrieval(monkeypatch):
    _patch_intent(monkeypatch, "chitchat")
    p = RAGPipeline(db=object(), tenant_id="t", user_id="u")

    called = {"retrieve": False}
    async def _retrieve(*a, **k):
        called["retrieve"] = True
        return []
    monkeypatch.setattr(p.retriever, "retrieve", _retrieve)
    monkeypatch.setattr(pipeline_mod, "GenerationService", _Gen)
    async def noop(*a, **k):
        return None
    monkeypatch.setattr(p, "_track_usage", noop)

    result = await p.query("hello there", use_routing=True, include_citations=False)
    assert result["intent"] == "chitchat"
    assert called["retrieve"] is False   # smalltalk did NOT hit the retriever
    assert result["answer"] == "hi!"


@pytest.mark.asyncio
async def test_routing_question_still_retrieves(monkeypatch):
    _patch_intent(monkeypatch, "question")
    p = RAGPipeline(db=object(), tenant_id="t", user_id="u")

    called = {"retrieve": False}
    async def _retrieve(*a, **k):
        called["retrieve"] = True
        return []
    monkeypatch.setattr(p.retriever, "retrieve", _retrieve)
    monkeypatch.setattr(pipeline_mod, "GenerationService", _Gen)
    async def noop(*a, **k):
        return None
    monkeypatch.setattr(p, "_track_usage", noop)

    result = await p.query("what is the policy?", use_routing=True, include_citations=False)
    assert result["intent"] == "question"
    assert called["retrieve"] is True    # a real question DID hit the retriever
