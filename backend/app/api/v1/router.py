"""V1 API router aggregating all sub-routers."""

from fastapi import APIRouter

from app.api.v1 import admin, agents, auth, collections, documents, health, query

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(health.router, tags=["Health"])
api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(collections.router, prefix="/collections", tags=["Collections"])
api_router.include_router(documents.router, prefix="/documents", tags=["Documents"])
api_router.include_router(query.router, prefix="/query", tags=["Query"])
api_router.include_router(agents.router, prefix="/agents", tags=["Agents"])
api_router.include_router(admin.router, prefix="/admin", tags=["Admin"])
