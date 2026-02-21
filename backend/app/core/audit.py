"""Audit logging utility."""

from sqlalchemy.ext.asyncio import AsyncSession
from app.models.audit_log import AuditLog
from app.models.user import User
from typing import Optional, Dict, Any
from uuid import UUID


async def create_audit_log(
    db: AsyncSession,
    user: User,
    action: str,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
):
    """Create an audit log entry."""
    audit_entry = AuditLog(
        tenant_id=user.tenant_id,
        user_id=user.id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details or {},
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(audit_entry)
    await db.commit()
    return audit_entry
