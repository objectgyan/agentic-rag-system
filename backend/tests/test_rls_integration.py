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


# --- Transaction-local tenant context through the app's session factory -------------
#
# The app sets the tenant with set_config(..., is_local=true), so Postgres clears it at
# every COMMIT. These tests drive the real TenantSession/after_begin path to prove that
# (a) the tenant survives a mid-request commit, (b) it does not survive the connection
# going back to the pool, and (c) writes after a commit still pass WITH CHECK.


async def _app_sessionmaker():
    """One-connection engine + the app's session factory, so pool reuse is guaranteed."""
    url = os.getenv("DATABASE_URL", "")
    if not url:
        pytest.skip("DATABASE_URL not set")
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.database import make_sessionmaker

    engine = create_async_engine(url, pool_size=1, max_overflow=0)
    try:
        async with engine.connect():
            pass
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f"database not reachable: {exc}")
    return engine, make_sessionmaker(engine)


async def _seed_two_tenants(conn, prefix):
    a, b = uuid.uuid4(), uuid.uuid4()
    for tid, name in ((a, "A"), (b, "B")):
        await conn.execute(
            "INSERT INTO tenants (id, name, slug) VALUES ($1,$2,$3)",
            tid, f"RLS-{name}", f"{prefix}-{name.lower()}-{tid.hex[:8]}",
        )
        await _set_ctx(conn, tid)
        await conn.execute(
            "INSERT INTO collections (tenant_id, name) VALUES ($1,$2)", tid, f"{name}_secret"
        )
    return a, b


async def _cleanup(conn, *tenants):
    for tid in tenants:
        await _set_ctx(conn, tid)
        await conn.execute("DELETE FROM collections WHERE tenant_id=$1", tid)
    await conn.execute("DELETE FROM tenants WHERE id = ANY($1::uuid[])", list(tenants))
    await conn.close()


async def test_tenant_context_survives_commit_in_same_session():
    from sqlalchemy import text

    from app.core.database import set_tenant_context

    conn = await _connect()
    a, b = await _seed_two_tenants(conn, "rls-tx")
    engine, Session = await _app_sessionmaker()
    try:
        async with Session() as db:
            await set_tenant_context(db, str(a))
            names = set((await db.execute(text("SELECT name FROM collections"))).scalars())
            assert names == {"A_secret"}

            await db.commit()  # clears the transaction-local GUC...

            # ...and after_begin re-applies it for the next transaction.
            names = set((await db.execute(text("SELECT name FROM collections"))).scalars())
            assert names == {"A_secret"}

            await db.rollback()
            names = set((await db.execute(text("SELECT name FROM collections"))).scalars())
            assert names == {"A_secret"}
    finally:
        await engine.dispose()
        await _cleanup(conn, a, b)


async def test_tenant_context_does_not_leak_through_the_pool():
    from sqlalchemy import text

    from app.core.database import set_tenant_context

    conn = await _connect()
    a, b = await _seed_two_tenants(conn, "rls-pool")
    engine, Session = await _app_sessionmaker()
    try:
        async with Session() as db:
            await set_tenant_context(db, str(a))
            assert (await db.execute(text("SELECT count(*) FROM collections"))).scalar() == 1
            pid_first = (await db.execute(text("SELECT pg_backend_pid()"))).scalar()
            # Commit like a write request does. (A session-level SET made inside a
            # transaction that later rolls back is undone; one that commits sticks to
            # the connection. That committed case is the leak this test guards.)
            await db.commit()

        # pool_size=1: the next session gets the very same Postgres connection back.
        async with Session() as db:
            pid_second = (await db.execute(text("SELECT pg_backend_pid()"))).scalar()
            assert pid_second == pid_first
            leftover = (
                await db.execute(text("SELECT current_setting('app.current_tenant', true)"))
            ).scalar()
            assert leftover in (None, "")
            assert (await db.execute(text("SELECT count(*) FROM collections"))).scalar() == 0
    finally:
        await engine.dispose()
        await _cleanup(conn, a, b)


async def test_insert_after_commit_passes_with_check():
    from sqlalchemy import text

    from app.core.database import set_tenant_context

    conn = await _connect()
    a, b = await _seed_two_tenants(conn, "rls-ins")
    engine, Session = await _app_sessionmaker()
    try:
        async with Session() as db:
            await set_tenant_context(db, str(a))
            await db.execute(text("SELECT 1 FROM collections"))
            await db.commit()

            # A write in the *next* transaction still sees tenant A, so WITH CHECK passes.
            await db.execute(
                text("INSERT INTO collections (tenant_id, name) VALUES (:t, 'A_after_commit')"),
                {"t": a},
            )
            await db.commit()

            names = set((await db.execute(text("SELECT name FROM collections"))).scalars())
            assert names == {"A_secret", "A_after_commit"}
    finally:
        await engine.dispose()
        await _cleanup(conn, a, b)
