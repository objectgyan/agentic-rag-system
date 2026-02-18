"""Middleware to extract and set tenant context on each request."""

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from app.core.security import decode_token


class TenantContextMiddleware(BaseHTTPMiddleware):
    """Extracts tenant info from JWT and sets it on request.state for RLS."""

    async def dispatch(self, request: Request, call_next):
        request.state.tenant_id = None
        request.state.tenant_tier = "free"
        request.state.user_id = None

        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            payload = decode_token(token)
            if payload:
                request.state.tenant_id = payload.get("tenant_id")
                request.state.tenant_tier = payload.get("tier", "free")
                request.state.user_id = payload.get("sub")

        # Also check X-API-Key header
        api_key = request.headers.get("X-API-Key")
        if api_key and not request.state.tenant_id:
            request.state.api_key = api_key

        return await call_next(request)
