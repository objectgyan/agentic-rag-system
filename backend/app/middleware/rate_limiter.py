"""Tier-based rate limiting middleware using Redis sliding window."""

import time
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from app.core.redis import redis_client
from app.core.config import TierLimits


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limits requests based on tenant tier using Redis sliding window."""

    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for non-API routes and health checks
        if not request.url.path.startswith("/api/") or request.url.path == "/api/v1/health":
            return await call_next(request)

        # Get tenant info from request state (set by auth dependency)
        tenant_id = getattr(request.state, "tenant_id", None)
        tenant_tier = getattr(request.state, "tenant_tier", "free")

        if not tenant_id:
            return await call_next(request)

        limits = TierLimits.get(tenant_tier)
        rpm = limits["requests_per_minute"]

        # Sliding window rate limit
        key = f"ratelimit:{tenant_id}:{int(time.time()) // 60}"
        try:
            current = await redis_client.incr(key)
            if current == 1:
                await redis_client.expire(key, 120)  # 2 min TTL for safety

            if current > rpm:
                raise HTTPException(
                    status_code=429,
                    detail={
                        "error": "rate_limit_exceeded",
                        "message": f"Rate limit exceeded. Your {tenant_tier} tier allows {rpm} requests/minute.",
                        "limit": rpm,
                        "retry_after": 60 - (int(time.time()) % 60),
                    },
                )

            response = await call_next(request)
            response.headers["X-RateLimit-Limit"] = str(rpm)
            response.headers["X-RateLimit-Remaining"] = str(max(0, rpm - current))
            response.headers["X-RateLimit-Reset"] = str(60 - (int(time.time()) % 60))
            return response

        except HTTPException:
            raise
        except Exception:
            # If Redis is down, allow the request
            return await call_next(request)
