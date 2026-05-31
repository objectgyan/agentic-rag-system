"""Tests for API-key validation (F6).

Verifies the lookup queries by key_prefix (so it can't be an O(n) scan), verifies
a real bcrypt match, rejects expired/non-matching keys, and never touches the DB
for empty input. The session is mocked; bcrypt hashing is real (one hash).
"""

import types
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.api.deps.auth import get_user_from_api_key
from app.core.security import hash_password


def _key(full, *, tenant_id=None, key_id=None, expires_at=None):
    return types.SimpleNamespace(
        key_hash=hash_password(full),
        tenant_id=tenant_id or uuid.uuid4(),
        id=key_id or uuid.uuid4(),
        expires_at=expires_at,
    )


class _Result:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return types.SimpleNamespace(all=lambda: self._items)


class _Session:
    def __init__(self, items):
        self._items = items
        self.last_sql = None

    async def execute(self, statement):
        self.last_sql = str(statement)
        return _Result(self._items)


class _NoDBSession:
    async def execute(self, statement):  # pragma: no cover - must not be called
        raise AssertionError("DB should not be queried")


@pytest.mark.asyncio
async def test_valid_key_returns_tenant_and_filters_by_prefix():
    full = "ar_" + "a" * 40
    cand = _key(full, tenant_id="tenant-1", key_id="key-1")
    session = _Session([cand])

    result = await get_user_from_api_key(full, session)

    assert result == {"tenant_id": "tenant-1", "key_id": "key-1"}
    # Lookup must filter on the prefix column, not scan all keys.
    assert "key_prefix" in session.last_sql


@pytest.mark.asyncio
async def test_non_matching_hash_returns_none():
    real = "ar_" + "a" * 40
    cand = _key(real)
    result = await get_user_from_api_key("ar_" + "b" * 40, _Session([cand]))
    assert result is None


@pytest.mark.asyncio
async def test_expired_key_is_rejected():
    full = "ar_" + "c" * 40
    cand = _key(full, expires_at=datetime.now(timezone.utc) - timedelta(days=1))
    result = await get_user_from_api_key(full, _Session([cand]))
    assert result is None


@pytest.mark.asyncio
async def test_empty_key_never_hits_db():
    assert await get_user_from_api_key("", _NoDBSession()) is None
