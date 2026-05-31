"""Async database engine and session management."""

import uuid
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.core.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=settings.app_debug,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


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
    the f-string SQL-injection vector (F1) while keeping the same session-level semantics
    as the original ``SET app.current_tenant = ...``.

    ``is_local=false`` keeps the setting at session scope (matching prior behavior). Note
    for F9: with connection pooling a session-level GUC persists on the connection after
    it returns to the pool, so every authenticated request MUST re-set context (it does,
    via the ``get_current_user`` dependency). F9 hardens this further under FORCE RLS.

    Raises ValueError if ``tenant_id`` is not a valid UUID, so a malformed or hostile
    value can never reach the database.
    """
    try:
        tenant_uuid = uuid.UUID(str(tenant_id))
    except (ValueError, TypeError, AttributeError):
        raise ValueError(f"Invalid tenant_id for RLS context: {tenant_id!r}")

    await session.execute(
        sa_text("SELECT set_config('app.current_tenant', :tenant_id, false)"),
        {"tenant_id": str(tenant_uuid)},
    )
