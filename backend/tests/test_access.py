"""Tests for collection access-control helpers (F2).

Pure unit tests with a mocked session — they assert the 404/403/allow decisions and
that no-op cases never touch the database.
"""

import types
import uuid

import pytest
from fastapi import HTTPException

from app.api.deps.access import (
    assert_collection_accessible,
    assert_collections_accessible,
)


def _user(tenant_id=None, user_id=None):
    return types.SimpleNamespace(
        tenant_id=tenant_id or uuid.uuid4(),
        id=user_id or uuid.uuid4(),
    )


def _collection(visibility="shared", owner_id=None):
    return types.SimpleNamespace(visibility=visibility, owner_id=owner_id or uuid.uuid4())


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _Session:
    """Returns queued values from execute(); records how many queries ran."""

    def __init__(self, values):
        self._values = list(values)
        self.executed = 0

    async def execute(self, *args, **kwargs):
        self.executed += 1
        return _Result(self._values.pop(0))


@pytest.mark.asyncio
async def test_shared_collection_is_accessible():
    user = _user()
    coll = _collection(visibility="shared")
    session = _Session([coll])
    assert await assert_collection_accessible(session, user, uuid.uuid4()) is coll


@pytest.mark.asyncio
async def test_private_collection_accessible_to_owner():
    user = _user()
    coll = _collection(visibility="private", owner_id=user.id)
    session = _Session([coll])
    assert await assert_collection_accessible(session, user, uuid.uuid4()) is coll


@pytest.mark.asyncio
async def test_private_collection_denied_to_non_owner():
    user = _user()
    coll = _collection(visibility="private", owner_id=uuid.uuid4())  # someone else
    session = _Session([coll])
    with pytest.raises(HTTPException) as exc:
        await assert_collection_accessible(session, user, uuid.uuid4())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_missing_collection_is_404():
    session = _Session([None])  # tenant-scoped query found nothing
    with pytest.raises(HTTPException) as exc:
        await assert_collection_accessible(session, _user(), uuid.uuid4())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("empty", [None, [], ()])
async def test_no_collections_is_noop_and_hits_no_db(empty):
    session = _Session([])  # would IndexError if execute were called
    await assert_collections_accessible(session, _user(), empty)
    assert session.executed == 0


@pytest.mark.asyncio
async def test_collections_list_rejects_first_inaccessible():
    user = _user()
    ok = _collection(visibility="shared")
    private_foreign = _collection(visibility="private", owner_id=uuid.uuid4())
    session = _Session([ok, private_foreign])
    with pytest.raises(HTTPException) as exc:
        await assert_collections_accessible(session, user, [uuid.uuid4(), uuid.uuid4()])
    assert exc.value.status_code == 403
