"""Tests for the rate limiter (F5).

Covers the security-relevant client-IP resolution (X-Forwarded-For must NOT be
trusted unless configured) and the fixed-window counter's fail-open behavior.
"""

import types

import pytest

from app.core.config import settings
from app.middleware.rate_limiter import RateLimitMiddleware


def _req(headers=None, client_host="1.2.3.4"):
    return types.SimpleNamespace(
        headers={k.lower(): v for k, v in (headers or {}).items()},
        client=types.SimpleNamespace(host=client_host) if client_host else None,
    )


def test_client_ip_ignores_forwarded_header_when_untrusted(monkeypatch):
    monkeypatch.setattr(settings, "trust_proxy_headers", False)
    req = _req({"X-Forwarded-For": "9.9.9.9"}, client_host="1.2.3.4")
    # A client cannot spoof its way past IP limits via XFF.
    assert RateLimitMiddleware._client_ip(req) == "1.2.3.4"


def test_client_ip_uses_forwarded_header_when_trusted(monkeypatch):
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    req = _req({"X-Forwarded-For": "9.9.9.9, 10.0.0.1"}, client_host="1.2.3.4")
    assert RateLimitMiddleware._client_ip(req) == "9.9.9.9"


def test_client_ip_falls_back_to_real_ip(monkeypatch):
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    req = _req({"X-Real-IP": "8.8.8.8"}, client_host="1.2.3.4")
    assert RateLimitMiddleware._client_ip(req) == "8.8.8.8"


def test_client_ip_unknown_without_socket(monkeypatch):
    monkeypatch.setattr(settings, "trust_proxy_headers", False)
    assert RateLimitMiddleware._client_ip(_req({}, client_host=None)) == "unknown"


@pytest.mark.asyncio
async def test_hit_blocks_once_over_limit(monkeypatch):
    class _CountRedis:
        def __init__(self):
            self.n = 0

        async def incr(self, key):
            self.n += 1
            return self.n

        async def expire(self, key, ttl):
            pass

    monkeypatch.setattr("app.middleware.rate_limiter.redis_client", _CountRedis())
    mw = RateLimitMiddleware(app=None)
    verdicts = [(await mw._hit("k", 2))[0] for _ in range(3)]
    assert verdicts == [True, True, False]


@pytest.mark.asyncio
async def test_hit_fails_open_when_redis_down(monkeypatch):
    class _BoomRedis:
        async def incr(self, key):
            raise RuntimeError("redis down")

        async def expire(self, key, ttl):
            pass

    monkeypatch.setattr("app.middleware.rate_limiter.redis_client", _BoomRedis())
    mw = RateLimitMiddleware(app=None)
    allowed, current = await mw._hit("k", 5)
    assert allowed is True and current == 0
