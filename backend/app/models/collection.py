"""Collection model — groups of documents with privacy controls."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.core.database import Base
import enum


class CollectionVisibility(str, enum.Enum):
    PRIVATE = "private"    # Owner only
    SHARED = "shared"      # All tenant members
    PUBLIC = "public"      # Accessible via API key


class Collection(Base):
    __tablename__ = "collections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    visibility = Column(SAEnum('private', 'shared', 'public', name='collectionvisibility', create_type=False), default="shared", nullable=False)
    embedding_model = Column(String(100), nullable=True)
    chunk_strategy = Column(String(50), default="semantic")
    chunk_size = Column(String(10), default="512")
    chunk_overlap = Column(String(10), default="50")
    metadata_schema = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    tenant = relationship("Tenant", back_populates="collections")
    owner = relationship("User", back_populates="collections")
    documents = relationship("Document", back_populates="collection", cascade="all, delete-orphan")
