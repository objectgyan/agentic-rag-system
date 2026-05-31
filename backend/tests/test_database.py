"""Tests for the tenant RLS context helper (F1).

These are pure unit tests: they mock the SQLAlchemy session, so no database is
required. They prove (a) the tenant id is passed as a bound parameter and never
interpolated into the SQL text, and (b) non-UUID input is rejected before any SQL runs.
"""

import uuid

import pytest

from app.core.database import set_tenant_context


class _FakeSession:
    """Minimal async session double that records execute() calls."""

    def __init__(self):
        self.calls = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        return None


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

    # Nothing should have reached the database.
    assert session.calls == []


@pytest.mark.asyncio
async def test_set_tenant_context_normalizes_uuid():
    """A valid UUID in any case is normalized to canonical form before binding."""
    session = _FakeSession()
    raw = "550E8400-E29B-41D4-A716-446655440000"

    await set_tenant_context(session, raw)

    _, params = session.calls[0]
    assert params == {"tenant_id": "550e8400-e29b-41d4-a716-446655440000"}
