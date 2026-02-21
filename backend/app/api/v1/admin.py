"""Admin endpoints for tenant management."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.core.database import get_db
from app.models.user import User
from app.models.tenant import Tenant, TenantTier
from app.models.usage import UsageRecord
from app.models.audit_log import AuditLog
from app.schemas.admin import UsageStats, TierUpdateRequest, UserManageRequest, UserCreateRequest, AuditLogEntry
from app.api.deps.auth import require_admin
from uuid import UUID
from typing import List
from datetime import datetime, timezone
from app.core.security import hash_password
from app.models.user import UserRole
from app.core.audit import create_audit_log

router = APIRouter()


@router.get("/usage", response_model=UsageStats)
async def get_usage(
    period: str = None,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get usage stats for the current tenant."""
    if not period:
        period = datetime.now(timezone.utc).strftime("%Y-%m")

    result = await db.execute(
        select(
            func.sum(UsageRecord.quantity).label("total"),
            func.sum(UsageRecord.tokens_used).label("tokens"),
            func.sum(UsageRecord.cost_usd).label("cost"),
        ).where(
            UsageRecord.tenant_id == user.tenant_id,
            UsageRecord.period == period,
        )
    )
    row = result.one()

    # Count documents
    from app.models.document import Document
    doc_count = await db.execute(
        select(func.count()).select_from(Document).where(Document.tenant_id == user.tenant_id)
    )

    return UsageStats(
        period=period,
        total_queries=row.total or 0,
        total_documents=doc_count.scalar(),
        total_tokens=row.tokens or 0,
        storage_used_mb=0,  # TODO: compute from MinIO
        cost_usd=row.cost or 0,
    )


@router.get("/tenant")
async def get_tenant_info(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get tenant information."""
    result = await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))
    tenant = result.scalar_one()
    return {
        "id": str(tenant.id),
        "name": tenant.name,
        "slug": tenant.slug,
        "tier": tenant.tier if isinstance(tenant.tier, str) else tenant.tier.value,
        "is_active": tenant.is_active,
        "created_at": tenant.created_at.isoformat(),
    }


@router.patch("/tier")
async def update_tier(
    req: TierUpdateRequest,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update the tenant's tier."""
    result = await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))
    tenant = result.scalar_one()
    old_tier = tenant.tier if isinstance(tenant.tier, str) else tenant.tier.value
    
    try:
        from app.models.tenant import TenantTier
        tenant.tier = TenantTier(req.tier).value
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid tier: {req.tier}")
    
    await db.commit()
    
    # Create audit log
    await create_audit_log(
        db=db,
        user=user,
        action="tenant.tier_updated",
        resource_type="tenant",
        resource_id=str(tenant.id),
        details={"old_tier": old_tier, "new_tier": req.tier},
    )
    
    return {"status": "updated", "tier": tenant.tier}


@router.get("/users")
async def list_users(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all users in the tenant."""
    result = await db.execute(
        select(User).where(User.tenant_id == user.tenant_id).order_by(User.created_at)
    )
    users = result.scalars().all()
    return [
        {
            "id": str(u.id),
            "email": u.email,
            "full_name": u.full_name,
            "role": u.role if isinstance(u.role, str) else u.role.value,
            "is_active": u.is_active,
            "created_at": u.created_at.isoformat(),
            "last_login": u.last_login.isoformat() if u.last_login else None,
        }
        for u in users
    ]


@router.post("/users")
async def create_user(
    req: UserCreateRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create a new user in the tenant."""
    # Check if user already exists
    result = await db.execute(
        select(User).where(User.email == req.email, User.tenant_id == admin.tenant_id)
    )
    existing_user = result.scalar_one_or_none()
    if existing_user:
        raise HTTPException(status_code=400, detail="User with this email already exists")
    
    # Validate role
    try:
        user_role = UserRole(req.role)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid role: {req.role}")
    
    # Create new user
    new_user = User(
        email=req.email,
        hashed_password=hash_password(req.password),
        full_name=req.full_name,
        role=user_role.value,
        tenant_id=admin.tenant_id,
        is_active=True,
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    
    # Create audit log
    await create_audit_log(
        db=db,
        user=admin,
        action="user.created",
        resource_type="user",
        resource_id=str(new_user.id),
        details={"email": new_user.email, "role": new_user.role if isinstance(new_user.role, str) else new_user.role.value},
    )
    
    return {
        "id": str(new_user.id),
        "email": new_user.email,
        "full_name": new_user.full_name,
        "role": new_user.role if isinstance(new_user.role, str) else new_user.role.value,
        "is_active": new_user.is_active,
        "created_at": new_user.created_at.isoformat(),
    }


@router.patch("/users/{user_id}")
async def manage_user(
    user_id: UUID,
    req: UserManageRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update a user's role or status."""
    result = await db.execute(
        select(User).where(User.id == user_id, User.tenant_id == admin.tenant_id)
    )
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if str(target.id) == str(admin.id):
        raise HTTPException(status_code=400, detail="Cannot modify yourself")

    if req.role is not None:
        target.role = req.role
    if req.is_active is not None:
        target.is_active = req.is_active

    await db.commit()
    return {"status": "updated"}


@router.get("/audit-log", response_model=List[AuditLogEntry])
async def get_audit_log(
    limit: int = 100,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get the audit log for the tenant."""
    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.tenant_id == user.tenant_id)
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    )
    return result.scalars().all()
