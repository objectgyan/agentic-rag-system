"""Prometheus metrics for the Celery worker (prefork-safe).

The backend exposes metrics via its ASGI /metrics app, but the worker is a separate
process group — and a *prefork* one, so a counter incremented in a child process isn't
visible to a metrics server in the parent. The standard fix is prometheus_client's
multiprocess mode: child processes write counter samples to a shared directory
(PROMETHEUS_MULTIPROC_DIR), and a tiny HTTP server in the worker's main process serves
the aggregated registry.

Wiring (see docker-compose): the worker sets PROMETHEUS_MULTIPROC_DIR and publishes
WORKER_METRICS_PORT; on worker startup we clear stale sample files and start the server.
"""

import glob
import logging
import os

from celery.signals import worker_init
from prometheus_client import CollectorRegistry, Counter, multiprocess, start_http_server

logger = logging.getLogger(__name__)

# In multiprocess mode this counter writes samples to PROMETHEUS_MULTIPROC_DIR; the
# exporter below aggregates them across all prefork children.
documents_processed_total = Counter(
    "documents_processed_total",
    "Documents processed by the ingestion worker, by outcome",
    ["status"],  # "completed" | "failed"
)


@worker_init.connect
def start_worker_metrics_server(**_kwargs) -> None:
    multiproc_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if not multiproc_dir:
        return  # metrics export not configured (e.g. local dev without the env)

    os.makedirs(multiproc_dir, exist_ok=True)
    # Clear stale samples from a previous run, per prometheus_client guidance.
    for f in glob.glob(os.path.join(multiproc_dir, "*.db")):
        try:
            os.remove(f)
        except OSError:
            pass

    port = int(os.environ.get("WORKER_METRICS_PORT", "9100"))
    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)
    start_http_server(port, registry=registry)
    logger.info("worker metrics exporter listening on :%d", port)
