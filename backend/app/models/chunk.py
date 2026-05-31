"""Chunk model with vector embeddings for similarity search."""

import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.core.database import Base


class Chunk(Base):
    __tablename__ = "chunks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    collection_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    content = Column(Text, nullable=False)
    embedding = Column(Vector(1536), nullable=True)
    chunk_index = Column(Integer, nullable=False)
    parent_chunk_id = Column(UUID(as_uuid=True), ForeignKey("chunks.id"), nullable=True)
    token_count = Column(Integer, nullable=True)
    page_number = Column(Integer, nullable=True)
    section_title = Column(String(500), nullable=True)
    metadata_extra = Column(JSONB, default={})
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    document = relationship("Document", back_populates="chunks")
    parent = relationship("Chunk", remote_side=[id])
