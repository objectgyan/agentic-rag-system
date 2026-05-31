"""Tests for log correlation context (O3)."""

import logging

from app.core.logging import (
    CorrelationFilter,
    bind_tenant,
    request_id_var,
)


def _record():
    return logging.LogRecord("test", logging.INFO, __file__, 1, "msg", None, None)


def test_filter_injects_bound_context():
    request_id_var.set("req-xyz")
    bind_tenant("tenant-1", "user-1")

    rec = _record()
    assert CorrelationFilter().filter(rec) is True
    assert rec.request_id == "req-xyz"
    assert rec.tenant_id == "tenant-1"
    assert rec.user_id == "user-1"


def test_filter_uses_defaults_in_isolated_context():
    import contextvars

    def check():
        rec = _record()
        CorrelationFilter().filter(rec)
        return rec.request_id, rec.tenant_id, rec.user_id

    # Run in a fresh context so previously-set values don't leak in.
    result = contextvars.Context().run(check)
    assert result == ("-", "-", "-")
