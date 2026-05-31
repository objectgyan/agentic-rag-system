"""Tests for custom Prometheus metrics (O2)."""

from prometheus_client import REGISTRY, generate_latest

from app.core.metrics import (
    rag_generation_seconds,
    rag_queries_total,
    rag_retrieval_seconds,
)


def test_metrics_register_and_render():
    rag_queries_total.labels(status="success").inc()
    rag_retrieval_seconds.observe(0.123)
    rag_generation_seconds.observe(0.456)

    rendered = generate_latest(REGISTRY).decode()
    assert "rag_queries_total" in rendered
    assert "rag_retrieval_seconds" in rendered
    assert "rag_generation_seconds" in rendered


def test_query_counter_tracks_status():
    before = rag_queries_total.labels(status="error")._value.get()
    rag_queries_total.labels(status="error").inc()
    after = rag_queries_total.labels(status="error")._value.get()
    assert after == before + 1
