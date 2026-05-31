"""Tests for the RBAC dependencies (O5)."""

import types

import pytest
from fastapi import HTTPException

from app.api.deps.auth import require_admin, require_member


def _user(role):
    return types.SimpleNamespace(role=role)


@pytest.mark.asyncio
async def test_require_admin_allows_admin():
    u = _user("admin")
    assert await require_admin(u) is u


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["member", "viewer"])
async def test_require_admin_blocks_non_admin(role):
    with pytest.raises(HTTPException) as exc:
        await require_admin(_user(role))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "member"])
async def test_require_member_allows_member_and_admin(role):
    assert (await require_member(_user(role))).role == role


@pytest.mark.asyncio
async def test_require_member_blocks_viewer():
    with pytest.raises(HTTPException) as exc:
        await require_member(_user("viewer"))
    assert exc.value.status_code == 403
