"""Request-ID correlation middleware (O3).

A pure-ASGI middleware (not BaseHTTPMiddleware) so the contextvar it sets is visible
to the endpoint and all of its logging — BaseHTTPMiddleware runs the inner app in a
separate task where contextvars set in dispatch don't reliably propagate.

Honors an inbound X-Request-ID (e.g. from a load balancer) or generates one, binds it
to the logging context, and echoes it back on the response so clients/operators can
correlate a request across logs.
"""

import uuid

from app.core.logging import request_id_var


class RequestContextMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        inbound = headers.get(b"x-request-id")
        request_id = inbound.decode() if inbound else uuid.uuid4().hex[:16]
        token = request_id_var.set(request_id)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                message["headers"].append((b"x-request-id", request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            request_id_var.reset(token)
