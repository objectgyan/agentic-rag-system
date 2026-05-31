"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import make_asgi_app

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import setup_logging
from app.middleware.rate_limiter import RateLimitMiddleware
from app.middleware.request_context import RequestContextMiddleware
from app.middleware.tenant_context import TenantContextMiddleware

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    # Ensure MinIO bucket exists
    try:
        from app.core.storage import ensure_bucket
        ensure_bucket()
    except Exception:
        # Don't crash the whole API if object storage is briefly unreachable at boot,
        # but make it loud — uploads will fail until this is resolved (F12).
        logger.error("failed to ensure MinIO bucket at startup", exc_info=True)
    yield
    # Cleanup
    from app.core.redis import redis_client
    await redis_client.close()


app = FastAPI(
    title="AgentRAG",
    description="Production-ready Agentic RAG-as-a-Service platform",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Custom middleware. add_middleware prepends, so the LAST added runs OUTERMOST:
# RequestContextMiddleware must wrap everything so a request_id is bound before any
# other middleware/handler logs.
app.add_middleware(RateLimitMiddleware)
app.add_middleware(TenantContextMiddleware)
app.add_middleware(RequestContextMiddleware)

# Prometheus metrics
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

# API routes
app.include_router(api_router)


@app.get("/")
async def root():
    return {
        "name": "AgentRAG",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/api/v1/health",
    }
