"""Headers for Server-Sent Events responses.

Reverse proxies buffer responses by default, so an SSE stream would reach the browser in one
lump when the answer finishes. ``X-Accel-Buffering: no`` turns buffering off for this response
in nginx (the frontend's proxy) and in many ingress controllers built on it.
"""

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
