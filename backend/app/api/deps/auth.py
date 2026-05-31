"""Authentication dependencies for FastAPI routes."""

from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, set_tenant_context
from app.core.logging import bind_tenant
from app.core.security import decode_token, verify_password
from app.models.api_key import ApiKey
from app.models.user import User

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
    result = await db.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    # Set tenant context for RLS, and bind tenant/user to the logging context (O3).
    await set_tenant_context(db, str(user.tenant_id))
    bind_tenant(str(user.tenant_id), str(user.id))
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
    """Validate an API key and return tenant info.

    Looks up candidates by the indexed key_prefix (the first 10 chars), then bcrypt-
    verifies only those — at most a handful, effectively O(1). The previous version
    hashed the presented key against *every* active key in the system, an O(n) bcrypt
    loop that is both a DoS vector and a latency cliff as keys accumulate. Expired keys
    are skipped.
    """
    if not api_key:
        return None

    prefix = api_key[:10]
    result = await db.execute(
        select(ApiKey).where(
            ApiKey.key_prefix == prefix,
            ApiKey.is_active.is_(True),
        )
    )
    now = datetime.now(timezone.utc)
    for k in result.scalars().all():
        if k.expires_at and k.expires_at < now:
            continue
        if verify_password(api_key, k.key_hash):
            return {"tenant_id": k.tenant_id, "key_id": k.id}
    return None
