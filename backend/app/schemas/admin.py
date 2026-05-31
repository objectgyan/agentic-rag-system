"""Admin schemas."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class UsageStats(BaseModel):
    period: str
    total_queries: int
    total_documents: int
    total_tokens: int
    storage_used_mb: float
    cost_usd: float


class TierUpdateRequest(BaseModel):
    tier: str  # free, pro, enterprise


class UserManageRequest(BaseModel):
    role: Optional[str] = None
    is_active: Optional[bool] = None


class UserCreateRequest(BaseModel):
    email: str
    password: str
    full_name: Optional[str] = None
    role: str = "member"  # member, admin


class AuditLogEntry(BaseModel):
    id: UUID
    user_id: Optional[UUID]
    action: str
    resource_type: Optional[str]
    resource_id: Optional[str]
    details: dict
    ip_address: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True
