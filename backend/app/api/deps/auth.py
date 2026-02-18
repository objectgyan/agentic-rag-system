"""Authentication dependencies for FastAPI routes."""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import get_db, set_tenant_context
from app.core.security import decode_token
from app.models.user import User, UserRole
from app.models.api_key import ApiKey
from app.core.security import verify_password
from typing import Optional

security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Extract and validate the current user from JWT token."""
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    payload = decode_token(credentials.credentials)
    if not payload or payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == user_id, User.is_active == True))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    # Set tenant context for RLS
    await set_tenant_context(db, str(user.tenant_id))
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """Require the current user to be an admin."""
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


async def require_member(user: User = Depends(get_current_user)) -> User:
    """Require at least member role."""
    if user.role == "viewer":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Member access required")
    return user


async def get_user_from_api_key(
    api_key: str,
    db: AsyncSession,
) -> Optional[dict]:
    """Validate an API key and return tenant info."""
    result = await db.execute(select(ApiKey).where(ApiKey.is_active == True))
    keys = result.scalars().all()
    for k in keys:
        if verify_password(api_key, k.key_hash):
            return {"tenant_id": k.tenant_id, "key_id": k.id}
    return None
