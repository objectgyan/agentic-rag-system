"""Rate limiting middleware (Redis fixed-window).

Two tiers of limiting:

1. **Authenticated traffic** — limited per tenant, by the tenant's tier
   (`TierLimits`). The tenant id is stamped on ``request.state`` by
   ``TenantContextMiddleware``.
2. **Unauthenticated auth endpoints** (``/api/v1/auth/*``) — limited per client IP
   (F5). Before login there is no tenant to key on, so without this, password
   brute force is unlimited. This limit is stricter and independent of tier.

Note on responses: this is a ``BaseHTTPMiddleware``, which sits *above* FastAPI's
exception handlers. Raising ``HTTPException`` here would escape to the server error
handler and become a 500, so we return a ``JSONResponse`` directly instead.
"""

import logging
import time

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import TierLimits, settings
from app.core.redis import redis_client

logger = logging.getLogger(__name__)

AUTH_PATH_PREFIX = "/api/v1/auth/"
HEALTH_PATH = "/api/v1/health"


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not path.startswith("/api/") or path == HEALTH_PATH:
            return await call_next(request)

        # 1. Unauthenticated auth endpoints: IP-keyed brute-force protection.
        if path.startswith(AUTH_PATH_PREFIX):
            ip = self._client_ip(request)
            limit = settings.auth_rate_limit_per_minute
            allowed, current = await self._hit(f"ratelimit:auth:{ip}", limit)
            if not allowed:
                logger.warning("auth rate limit exceeded for ip=%s (%s/%s)", ip, current, limit)
                return self._limit_response(
                    limit,
                    "Too many authentication attempts. Try again in a minute.",
                )
            return await call_next(request)

        # 2. Authenticated traffic: per-tenant, per-tier limit.
        tenant_id = getattr(request.state, "tenant_id", None)
        tenant_tier = getattr(request.state, "tenant_tier", "free")
        if not tenant_id:
            return await call_next(request)

        rpm = TierLimits.get(tenant_tier)["requests_per_minute"]
        allowed, current = await self._hit(f"ratelimit:{tenant_id}", rpm)
        if not allowed:
            return self._limit_response(
                rpm,
                f"Rate limit exceeded. Your {tenant_tier} tier allows {rpm} requests/minute.",
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(rpm)
        response.headers["X-RateLimit-Remaining"] = str(max(0, rpm - current))
        response.headers["X-RateLimit-Reset"] = str(60 - (int(time.time()) % 60))
        return response

    @staticmethod
    def _client_ip(request: Request) -> str:
        """Resolve the client IP, honoring proxy headers only when configured to.

        X-Forwarded-For is trivially spoofable by the client, so we only read it when
        ``trust_proxy_headers`` is set (i.e. a proxy we control overwrites it). Otherwise
        we use the direct socket peer, which a client cannot forge.
        """
        if settings.trust_proxy_headers:
            xff = request.headers.get("x-forwarded-for")
            if xff:
                return xff.split(",")[0].strip()
            real_ip = request.headers.get("x-real-ip")
            if real_ip:
                return real_ip.strip()
        return request.client.host if request.client else "unknown"

    async def _hit(self, key_base: str, limit: int):
        """Increment the fixed-window counter; return (allowed, current_count).

        Fails open: if Redis is unreachable the request is allowed (availability over
        strict enforcement), but the failure is logged so it is never silent.
        """
        key = f"{key_base}:{int(time.time()) // 60}"
        try:
            current = await redis_client.incr(key)
            if current == 1:
                await redis_client.expire(key, 120)  # 2-min TTL as a safety margin
            return current <= limit, current
        except Exception as exc:  # noqa: BLE001 - degrade gracefully, but loudly
            logger.warning("rate limiter Redis error on %s, allowing request: %s", key_base, exc)
            return True, 0

    @staticmethod
    def _limit_response(limit: int, message: str) -> JSONResponse:
        retry_after = 60 - (int(time.time()) % 60)
        return JSONResponse(
            status_code=429,
            content={
                "error": "rate_limit_exceeded",
                "message": message,
                "limit": limit,
                "retry_after": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )
