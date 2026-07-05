"""Conversation memory tests.

Two layers:
- pipeline forwards history to the generator (C1), now through the bounding step (item 2);
- the ConversationMemory bounding logic itself (windowing + token budget + summary), no network.
"""

import pytest

from app.services.rag import pipeline as pipeline_mod
from app.services.rag.conversation_memory import ConversationMemory, count_tokens
from app.services.rag.pipeline import RAGPipeline


# --------------------------------------------------------------------------- #
# Pipeline forwarding (C1) — still holds with the item-2 bounding step in front
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_pipeline_forwards_history_to_generator(monkeypatch):
    p = RAGPipeline(db=object(), tenant_id="t", user_id="u")
    captured = {}

    class _Gen:
        def __init__(self, *args, **kwargs):
            pass

        async def generate(self, query, chunks, conversation_history=None, graph_facts=None,
                           conversation_summary=None):
            captured["history"] = conversation_history
            captured["summary"] = conversation_summary
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

    # A single short turn fits the recent window: forwarded verbatim, no summary.
    assert captured["history"] == history
    assert captured["summary"] is None


@pytest.mark.asyncio
async def test_pipeline_history_defaults_to_none(monkeypatch):
    p = RAGPipeline(db=object(), tenant_id="t", user_id="u")
    captured = {}

    class _Gen:
        def __init__(self, *args, **kwargs):
            pass

        async def generate(self, query, chunks, conversation_history=None, graph_facts=None,
                           conversation_summary=None):
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


# --------------------------------------------------------------------------- #
# ConversationMemory bounding logic (item 2)
# --------------------------------------------------------------------------- #

def _history(n: int):
    """n alternating user/assistant messages, chronological."""
    out = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        out.append({"role": role, "content": f"message number {i} with some words"})
    return out


def test_count_tokens():
    assert count_tokens("hello world") > 0
    assert count_tokens("") == 0


@pytest.mark.asyncio
async def test_empty_history():
    summary, recent = await ConversationMemory().prepare([])
    assert summary is None and recent == []


@pytest.mark.asyncio
async def test_short_history_kept_verbatim_no_summary():
    hist = _history(4)
    mem = ConversationMemory(recent_messages=6, summary_enabled=True)

    async def _boom(_older):
        raise AssertionError("should not summarize when everything fits in the recent window")
    mem._summarize = _boom

    summary, recent = await mem.prepare(hist)
    assert summary is None
    assert recent == hist


@pytest.mark.asyncio
async def test_long_history_summarizes_older_and_keeps_recent():
    hist = _history(10)
    mem = ConversationMemory(recent_messages=4, token_budget=100000, summary_enabled=True)

    captured = {}
    async def _fake_summarize(older):
        captured["older"] = older
        return "RUNNING SUMMARY"
    mem._summarize = _fake_summarize

    summary, recent = await mem.prepare(hist)
    assert summary == "RUNNING SUMMARY"
    assert recent == hist[-4:]
    assert captured["older"] == hist[:6]


@pytest.mark.asyncio
async def test_token_budget_trims_recent_window():
    hist = _history(6)
    mem = ConversationMemory(recent_messages=6, token_budget=15, summary_enabled=False)
    summary, recent = await mem.prepare(hist)
    assert summary is None
    assert 1 <= len(recent) < 6
    assert recent[-1] == hist[-1]  # newest message always kept


@pytest.mark.asyncio
async def test_summary_failure_is_soft():
    hist = _history(10)
    mem = ConversationMemory(recent_messages=4, summary_enabled=True)

    async def _boom(_older):
        raise RuntimeError("LLM down")
    mem._summarize = _boom

    summary, recent = await mem.prepare(hist)
    assert summary is None          # failed softly, not raised
    assert recent == hist[-4:]


@pytest.mark.asyncio
async def test_fifty_turn_conversation_stays_bounded():
    # Acceptance-criteria case: a 50-message chat must not pass 50 messages to the generator,
    # and early context is retained as a summary rather than silently dropped.
    hist = _history(50)
    mem = ConversationMemory(recent_messages=6, token_budget=100000, summary_enabled=True)

    async def _fake(_older):
        return "summary of the earlier messages"
    mem._summarize = _fake

    summary, recent = await mem.prepare(hist)
    assert len(recent) == 6
    assert summary is not None
