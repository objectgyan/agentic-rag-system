"""Celery application for async document processing."""

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "agentrag",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_routes={
        "app.services.processing.*": {"queue": "processing"},
        "app.services.rag.*": {"queue": "rag"},
    },
)

celery_app.autodiscover_tasks(["app.services.processing"])

# Importing this connects the worker_init signal that starts the metrics exporter.
from app.core import worker_metrics  # noqa: E402,F401
