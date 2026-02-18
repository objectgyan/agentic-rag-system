"""Health check endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.core.database import get_db
from app.core.redis import redis_client

router = APIRouter()


@router.get("/health")
async def health_check():
    return {"status": "healthy", "service": "AgentRAG"}


@router.get("/health/detailed")
async def detailed_health(db: AsyncSession = Depends(get_db)):
    checks = {}

    # Database
    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = "healthy"
    except Exception as e:
        checks["database"] = f"unhealthy: {str(e)}"

    # Redis
    try:
        await redis_client.ping()
        checks["redis"] = "healthy"
    except Exception as e:
        checks["redis"] = f"unhealthy: {str(e)}"

    all_healthy = all(v == "healthy" for v in checks.values())
    return {"status": "healthy" if all_healthy else "degraded", "checks": checks}
