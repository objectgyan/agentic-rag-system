"""Tenant (organization) model."""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database import Base


class TenantTier(str, enum.Enum):
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    slug = Column(String(255), unique=True, nullable=False, index=True)
    tier = Column(SAEnum('free', 'pro', 'enterprise', name='tenanttier', create_type=False), default="free", nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    is_active = Column(String(1), default="1")

    # Relationships
    users = relationship("User", back_populates="tenant", cascade="all, delete-orphan")
    collections = relationship("Collection", back_populates="tenant", cascade="all, delete-orphan")
    api_keys = relationship("ApiKey", back_populates="tenant", cascade="all, delete-orphan")
