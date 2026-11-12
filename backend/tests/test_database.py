"""Tests for the tenant RLS context helper (F1).

These are pure unit tests: they mock the SQLAlchemy session, so no database is
required. They prove (a) the tenant id is passed as a bound parameter and never
interpolated into the SQL text, (b) non-UUID input is rejected before any SQL runs, and
(c) the setting is transaction-local and remembered on the session so the ``after_begin``
listener can re-apply it after a commit.
"""

import uuid

import pytest

from app.core.database import _apply_tenant_on_begin, set_tenant_context


class _FakeSession:
    """Minimal async session double that records execute() calls."""

    def __init__(self, in_transaction=True):
        self.calls = []
        self.info = {}
        self._in_transaction = in_transaction

    def in_transaction(self):
        return self._in_transaction

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        return None


class _FakeConnection:
    """Sync connection double for the after_begin listener."""

    def __init__(self):
        self.calls = []

    def execute(self, statement, params=None):
        self.calls.append((str(statement), params))


@pytest.mark.asyncio
async def test_set_tenant_context_uses_bound_parameter():
    session = _FakeSession()
    tid = str(uuid.uuid4())

    await set_tenant_context(session, tid)

    assert len(session.calls) == 1
    sql, params = session.calls[0]
    # The id travels as a bound parameter...
    assert params == {"tenant_id": tid}
    assert "set_config" in sql.lower()
    # ...and is NOT baked into the SQL string (the old f-string vulnerability).
    assert tid not in sql
    # Transaction-local (is_local=true): cleared at COMMIT, never left on a pooled connection.
    assert "set_config('app.current_tenant', :tenant_id, true)" in sql
    # Remembered so the after_begin listener re-applies it to later transactions.
    assert session.info["tenant_id"] == tid


@pytest.mark.asyncio
async def test_set_tenant_context_outside_transaction_defers_to_after_begin():
    """With no open transaction there is nothing to set yet; the next BEGIN applies it."""
    session = _FakeSession(in_transaction=False)
    tid = str(uuid.uuid4())

    await set_tenant_context(session, tid)

    assert session.calls == []
    assert session.info["tenant_id"] == tid


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        "'; DROP TABLE users;--",
        "not-a-uuid",
        "",
        None,
        "12345",
    ],
)
async def test_set_tenant_context_rejects_non_uuid(bad):
    session = _FakeSession()

    with pytest.raises(ValueError):
        await set_tenant_context(session, bad)

    # Nothing should have reached the database, or been stored on the session.
    assert session.calls == []
    assert session.info == {}


@pytest.mark.asyncio
async def test_set_tenant_context_normalizes_uuid():
    """A valid UUID in any case is normalized to canonical form before binding."""
    session = _FakeSession()
    raw = "550E8400-E29B-41D4-A716-446655440000"

    await set_tenant_context(session, raw)

    _, params = session.calls[0]
    assert params == {"tenant_id": "550e8400-e29b-41d4-a716-446655440000"}


def test_after_begin_reapplies_stored_tenant():
    session = _FakeSession()
    tid = str(uuid.uuid4())
    session.info["tenant_id"] = tid
    conn = _FakeConnection()

    _apply_tenant_on_begin(session, None, conn)

    assert len(conn.calls) == 1
    sql, params = conn.calls[0]
    assert params == {"tenant_id": tid}
    assert "set_config('app.current_tenant', :tenant_id, true)" in sql
    assert tid not in sql


def test_after_begin_without_tenant_sets_nothing():
    """Sessions that never set a tenant (login lookups, health checks) stay deny-by-default."""
    conn = _FakeConnection()

    _apply_tenant_on_begin(_FakeSession(), None, conn)

    assert conn.calls == []
