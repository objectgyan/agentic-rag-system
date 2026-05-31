"""Custom Prometheus metrics for RAG operations (O2).

These register on the default prometheus_client REGISTRY, which is what the
``/metrics`` ASGI app mounted in main.py exposes — so defining them here is enough
to surface them. Before this, only default process/GC metrics were exported; there
was no visibility into query volume, error rate, or pipeline latency.
"""

from prometheus_client import Counter, Histogram

# Query volume and outcome.
rag_queries_total = Counter(
    "rag_queries_total",
    "RAG queries handled, by outcome",
    ["status"],  # "success" | "error"
)

# Pipeline latency, split into the two stages that dominate cost.
rag_retrieval_seconds = Histogram(
    "rag_retrieval_seconds",
    "Time spent in retrieval (dense + sparse + fusion + rerank)",
)
rag_generation_seconds = Histogram(
    "rag_generation_seconds",
    "Time spent in answer generation",
)

# NOTE: ingestion happens in the Celery worker, a separate process whose metrics are
# NOT scraped by the backend's /metrics. Exposing worker metrics needs its own exporter
# (e.g. prometheus_client.start_http_server in a worker signal) — tracked as a follow-up.
