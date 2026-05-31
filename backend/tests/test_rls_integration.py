"""Integration test: Row-Level Security actually isolates tenants (F9/F10).

Unlike the other tests, this one needs a live database and connects as the
restricted ``agentrag_app`` role (via DATABASE_URL). It proves the keystone
guarantee: a non-owner, non-superuser role cannot see another tenant's rows even
with an explicit cross-tenant WHERE clause, and sees nothing with no context set.

Skips cleanly when no database is reachable (e.g. unit-only CI without Postgres).
"""

import os
import uuid

import pytest

asyncpg = pytest.importorskip("asyncpg")

pytestmark = pytest.mark.asyncio


def _dsn():
    url = os.getenv("DATABASE_URL", "")
    # SQLAlchemy async URL -> plain asyncpg DSN
    return url.replace("+asyncpg", "") if url else None


async def _connect():
    dsn = _dsn()
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    try:
        return await asyncpg.connect(dsn)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"database not reachable: {exc}")


async def _set_ctx(conn, tenant_id):
    await conn.execute("SELECT set_config('app.current_tenant', $1, false)", str(tenant_id))


async def test_rls_isolates_tenants_for_the_app_role():
    conn = await _connect()
    a, b = uuid.uuid4(), uuid.uuid4()
    try:
        # tenants has no RLS, so seed two of them directly.
        await conn.execute(
            "INSERT INTO tenants (id, name, slug) VALUES ($1,$2,$3)", a, "RLS-A", f"rls-a-{a.hex[:8]}"
        )
        await conn.execute(
            "INSERT INTO tenants (id, name, slug) VALUES ($1,$2,$3)", b, "RLS-B", f"rls-b-{b.hex[:8]}"
        )

        # Each collection can only be inserted under its own tenant context — the
        # policy's WITH CHECK enforces tenant_id == current_setting.
        await _set_ctx(conn, a)
        await conn.execute("INSERT INTO collections (tenant_id, name) VALUES ($1,$2)", a, "A_secret")
        await _set_ctx(conn, b)
        await conn.execute("INSERT INTO collections (tenant_id, name) VALUES ($1,$2)", b, "B_secret")

        # Context A sees only A's row.
        await _set_ctx(conn, a)
        names = {r["name"] for r in await conn.fetch("SELECT name FROM collections")}
        assert "A_secret" in names
        assert "B_secret" not in names

        # An explicit cross-tenant filter still returns nothing — RLS overrides it.
        assert await conn.fetchval(
            "SELECT count(*) FROM collections WHERE tenant_id=$1", b
        ) == 0

        # With no context, deny by default.
        await conn.execute("SELECT set_config('app.current_tenant', '', false)")
        assert await conn.fetchval("SELECT count(*) FROM collections") == 0
    finally:
        await _set_ctx(conn, a)
        await conn.execute("DELETE FROM collections WHERE tenant_id=$1", a)
        await _set_ctx(conn, b)
        await conn.execute("DELETE FROM collections WHERE tenant_id=$1", b)
        await conn.execute("DELETE FROM tenants WHERE id = ANY($1::uuid[])", [a, b])
        await conn.close()


async def test_writes_are_blocked_without_matching_context():
    """Inserting a row for a tenant other than the current context must fail."""
    conn = await _connect()
    a, b = uuid.uuid4(), uuid.uuid4()
    try:
        await conn.execute(
            "INSERT INTO tenants (id, name, slug) VALUES ($1,$2,$3)", a, "RLS-A", f"rls-wa-{a.hex[:8]}"
        )
        await _set_ctx(conn, a)
        # Context is A, but we try to insert a row tagged as tenant B -> WITH CHECK fails.
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await conn.execute("INSERT INTO collections (tenant_id, name) VALUES ($1,$2)", b, "evil")
    finally:
        await _set_ctx(conn, a)
        await conn.execute("DELETE FROM collections WHERE tenant_id=$1", a)
        await conn.execute("DELETE FROM tenants WHERE id=$1", a)
        await conn.close()
