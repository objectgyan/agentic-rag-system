"""Tests for per-query tracing + cost estimation (item 3). Pure, no infra."""

import time

import pytest

from app.services.rag.tracing import QueryTrace, estimate_cost


def test_estimate_cost_known_model():
    # gpt-4o-mini: (0.15 in, 0.60 out) per 1M tokens
    cost = estimate_cost("gpt-4o-mini", prompt_tokens=1_000_000, completion_tokens=1_000_000)
    assert cost == pytest.approx(0.75)


def test_estimate_cost_prefix_match():
    # dated claude id resolves to the base price
    base = estimate_cost("claude-3-5-sonnet", 1_000_000, 0)
    dated = estimate_cost("claude-3-5-sonnet-20241022", 1_000_000, 0)
    assert dated == base == pytest.approx(3.00)


def test_estimate_cost_unknown_model_is_zero():
    assert estimate_cost("some-unknown-model", 1000, 1000) == 0.0
    assert estimate_cost("", 1000, 1000) == 0.0


def test_span_times_a_block():
    qt = QueryTrace()
    with qt.span("work", foo="bar"):
        time.sleep(0.01)
    assert len(qt.spans) == 1
    s = qt.spans[0]
    assert s.name == "work"
    assert s.duration_ms >= 5  # at least ~10ms slept
    assert s.metadata == {"foo": "bar"}


def test_add_span_records_premeasured():
    qt = QueryTrace()
    qt.add_span("retrieve", 42.0, n_chunks=5)
    assert qt.spans[0].duration_ms == 42.0
    assert qt.total_ms == 42.0


def test_record_chunks():
    class _C:
        def __init__(self, cid, doc, score):
            self.chunk_id, self.document_id, self.score = cid, doc, score

    qt = QueryTrace()
    qt.record_chunks([_C("a", "d1", 0.912345), _C("b", "d2", 0.5)])
    assert qt.retrieved == [
        {"chunk_id": "a", "document_id": "d1", "score": 0.9123},
        {"chunk_id": "b", "document_id": "d2", "score": 0.5},
    ]


def test_record_usage_computes_cost():
    qt = QueryTrace()
    qt.record_usage("gpt-4o-mini", prompt_tokens=1000, completion_tokens=500)
    assert qt.total_tokens == 1500
    # 1000/1e6*0.15 + 500/1e6*0.60 = 0.00015 + 0.0003 = 0.00045
    assert qt.cost_usd == pytest.approx(0.00045)


def test_record_usage_falls_back_to_total():
    qt = QueryTrace()
    qt.record_usage("gpt-4o", total_tokens=1234)
    assert qt.total_tokens == 1234


def test_to_dict_shape():
    qt = QueryTrace()
    qt.add_span("retrieve", 10.0, n_chunks=2)
    qt.add_span("generate", 20.0)
    qt.record_usage("gpt-4o-mini", 100, 50)
    d = qt.to_dict()
    assert d["total_ms"] == 30.0
    assert [s["name"] for s in d["spans"]] == ["retrieve", "generate"]
    assert d["spans"][0]["metadata"] == {"n_chunks": 2}
    assert "metadata" not in d["spans"][1]  # empty metadata omitted
    assert d["model"] == "gpt-4o-mini"
    assert d["cost_usd"] > 0


def test_log_summary_does_not_raise():
    import logging

    qt = QueryTrace()
    qt.add_span("retrieve", 5.0)
    qt.record_usage("gpt-4o-mini", 10, 10)
    qt.log_summary(logging.getLogger("test"))  # must not raise
