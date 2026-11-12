"""Async database engine and session management."""

import uuid

from sqlalchemy import event
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session

from app.core.config import settings

_SET_TENANT_SQL = sa_text("SELECT set_config('app.current_tenant', :tenant_id, true)")

engine = create_async_engine(
    settings.database_url,
    echo=settings.app_debug,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
)


class TenantSession(Session):
    """Sync session class behind every AsyncSession; re-applies the RLS tenant per transaction.

    The tenant GUC is transaction-local (``set_config(..., true)``), so it is cleared at
    every COMMIT/ROLLBACK. Many request paths commit and then keep using the same session,
    so the tenant is remembered in ``session.info`` and set again by the ``after_begin``
    listener below at the start of each new transaction.
    """


@event.listens_for(TenantSession, "after_begin")
def _apply_tenant_on_begin(session, transaction, connection):
    tenant_id = session.info.get("tenant_id")
    if tenant_id:
        connection.execute(_SET_TENANT_SQL, {"tenant_id": tenant_id})


def make_sessionmaker(bind) -> async_sessionmaker[AsyncSession]:
    """Session factory for ``bind`` whose sessions carry the tenant across transactions."""
    return async_sessionmaker(
        bind, class_=AsyncSession, sync_session_class=TenantSession, expire_on_commit=False
    )


AsyncSessionLocal = make_sessionmaker(engine)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def set_tenant_context(session: AsyncSession, tenant_id: str):
    """Set the current tenant GUC that Postgres Row-Level Security policies read.

    The value is passed as a *bound parameter* to ``set_config()`` rather than
    interpolated into the SQL string, and is validated as a UUID first. ``SET`` cannot
    take bind parameters, but ``set_config(name, value, is_local)`` can — so this closes
    the f-string SQL-injection vector (F1).

    The setting is *transaction-local* (``is_local=true``): Postgres clears it at the end
    of every transaction, so a pooled connection can never carry one tenant's context into
    the next request. Because request paths commit and keep querying on the same session,
    the tenant is stored in ``session.info`` and the ``TenantSession`` ``after_begin``
    listener applies it again at the start of each new transaction. If the session is
    already inside a transaction (``after_begin`` has fired for it), it is applied now too.

    The session must come from ``make_sessionmaker`` / ``AsyncSessionLocal``. A plain
    ``AsyncSession`` has no listener, so after a commit (or before its first query) it has
    no tenant set and RLS denies by default: reads return nothing, writes fail.

    Raises ValueError if ``tenant_id`` is not a valid UUID, so a malformed or hostile
    value can never reach the database.
    """
    try:
        tenant_uuid = uuid.UUID(str(tenant_id))
    except (ValueError, TypeError, AttributeError):
        raise ValueError(f"Invalid tenant_id for RLS context: {tenant_id!r}")

    canonical = str(tenant_uuid)
    session.info["tenant_id"] = canonical
    if session.in_transaction():
        await session.execute(_SET_TENANT_SQL, {"tenant_id": canonical})
