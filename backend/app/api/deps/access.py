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
