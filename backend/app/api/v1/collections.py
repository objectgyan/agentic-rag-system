"""Collection management endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.access import assert_collection_accessible
from app.api.deps.auth import get_current_user, require_member
from app.core.audit import create_audit_log
from app.core.config import TierLimits
from app.core.database import get_db
from app.models.collection import Collection
from app.models.document import Document
from app.models.user import User
from app.schemas.collection import CollectionCreate, CollectionResponse, CollectionUpdate

router = APIRouter()


@router.post("", response_model=CollectionResponse, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=CollectionResponse, status_code=status.HTTP_201_CREATED)
async def create_collection(
    req: CollectionCreate,
    user: User = Depends(require_member),
    db: AsyncSession = Depends(get_db),
):
    """Create a new document collection."""
    # Check tier limits
    from app.models.tenant import Tenant
    tenant_result = await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))
    tenant = tenant_result.scalar_one()
    limits = TierLimits.get(tenant.tier)

    if limits["max_collections"] > 0:
        count_result = await db.execute(
            select(func.count()).select_from(Collection).where(Collection.tenant_id == user.tenant_id)
        )
        current_count = count_result.scalar()
        if current_count >= limits["max_collections"]:
            raise HTTPException(status_code=403, detail=f"Collection limit reached for {tenant.tier} tier")

    collection = Collection(
        tenant_id=user.tenant_id,
        owner_id=user.id,
        name=req.name,
        description=req.description,
        visibility=req.visibility,
        embedding_model=req.embedding_model,
        chunk_strategy=req.chunk_strategy,
        chunk_size=str(req.chunk_size),
        chunk_overlap=str(req.chunk_overlap),
    )
    db.add(collection)
    await db.commit()
    await db.refresh(collection)

    # Create audit log
    await create_audit_log(
        db=db,
        user=user,
        action="collection.created",
        resource_type="collection",
        resource_id=str(collection.id),
        details={"name": collection.name},
    )

    resp = CollectionResponse.model_validate(collection)
    resp.document_count = 0
    return resp


@router.get("", response_model=list[CollectionResponse])
@router.get("/", response_model=list[CollectionResponse])
async def list_collections(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List collections accessible to the current user."""
    query = (
        select(Collection)
        .where(Collection.tenant_id == user.tenant_id)
        .where(
            (Collection.visibility != "private") |
            (Collection.owner_id == user.id)
        )
        .order_by(Collection.updated_at.desc())
    )
    result = await db.execute(query)
    collections = result.scalars().all()

    responses = []
    for c in collections:
        doc_count = await db.execute(
            select(func.count()).select_from(Document).where(Document.collection_id == c.id)
        )
        resp = CollectionResponse.model_validate(c)
        resp.document_count = doc_count.scalar()
        responses.append(resp)
    return responses


@router.get("/{collection_id}", response_model=CollectionResponse)
async def get_collection(
    collection_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific collection."""
    collection = await assert_collection_accessible(db, user, collection_id)

    doc_count = await db.execute(
        select(func.count()).select_from(Document).where(Document.collection_id == collection.id)
    )
    resp = CollectionResponse.model_validate(collection)
    resp.document_count = doc_count.scalar()
    return resp


@router.patch("/{collection_id}", response_model=CollectionResponse)
async def update_collection(
    collection_id: UUID,
    req: CollectionUpdate,
    user: User = Depends(require_member),
    db: AsyncSession = Depends(get_db),
):
    """Update a collection's metadata."""
    # Tenant scope + private-owner enforcement (previously any member could edit
    # another member's private collection).
    collection = await assert_collection_accessible(db, user, collection_id)

    for field, value in req.model_dump(exclude_unset=True).items():
        setattr(collection, field, value)

    await db.commit()
    await db.refresh(collection)
    return CollectionResponse.model_validate(collection)


@router.delete("/{collection_id}")
async def delete_collection(
    collection_id: UUID,
    user: User = Depends(require_member),
    db: AsyncSession = Depends(get_db),
):
    """Delete a collection and all its documents."""
    collection = await assert_collection_accessible(db, user, collection_id)

    await db.delete(collection)
    await db.commit()
    return {"status": "deleted"}
