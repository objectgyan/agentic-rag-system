"""Authorization helpers for resource access (F2).

Postgres RLS is the *safety net*; these checks are the *gate*. They exist for two
reasons:

1. **Clear failures.** Without them, asking for a collection in another tenant (or a
   private one you don't own) silently returns an empty answer — indistinguishable from
   "no results". With them, you get an explicit 403/404.
2. **Defense in depth.** Isolation stays correct even if RLS is ever misconfigured
   (exactly the inert-RLS situation we are fixing in F9). Never rely on a single layer.
"""

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.collection import Collection
from app.models.user import User


async def assert_collection_accessible(
    db: AsyncSession, user: User, collection_id
) -> Collection:
    """Return the collection if the user may access it, else raise.

    - 404 if it does not exist within the user's tenant (don't leak existence
      across tenants — a foreign id looks identical to a missing one).
    - 403 if it is private and the user is not the owner.
    """
    result = await db.execute(
        select(Collection).where(
            Collection.id == collection_id,
            Collection.tenant_id == user.tenant_id,
        )
    )
    collection = result.scalar_one_or_none()
    if collection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Collection {collection_id} not found",
        )
    if collection.visibility == "private" and collection.owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied to collection {collection_id}",
        )
    return collection


async def assert_collections_accessible(db: AsyncSession, user: User, collection_ids) -> None:
    """Validate every collection id supplied by the caller. No-op if None/empty.

    Each id is checked individually so the first inaccessible one produces a precise
    403/404 rather than a vague aggregate error.
    """
    if not collection_ids:
        return
    for cid in dict.fromkeys(collection_ids):  # de-dupe, preserve order
        await assert_collection_accessible(db, user, cid)


async def assert_model_allowed(db: AsyncSession, user: User, model) -> None:
    """403 unless the tenant's tier permits the requested model (``TierLimits[...]["models"]``).

    ``None`` (use the server default) is always allowed — the operator chose that model. The tier
    is read from the DB rather than the JWT claim so an upgrade/downgrade applies immediately.
    """
    if model is None:
        return
    from app.core.config import TierLimits
    from app.llm import model_allowed
    from app.models.tenant import Tenant

    tier = await db.scalar(select(Tenant.tier).where(Tenant.id == user.tenant_id))
    if not model_allowed(TierLimits.get(tier or "free")["models"], model):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Model {model!r} is not available on the {tier or 'free'} tier",
        )
