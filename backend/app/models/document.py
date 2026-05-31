"""Document model for ingested files."""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.core.database import Base


class DocumentStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class DocumentType(str, enum.Enum):
    PDF = "pdf"
    DOCX = "docx"
    TXT = "txt"
    MARKDOWN = "markdown"
    CSV = "csv"
    XLSX = "xlsx"
    HTML = "html"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    URL = "url"


class Document(Base):
    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    collection_id = Column(UUID(as_uuid=True), ForeignKey("collections.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = Column(String(500), nullable=False)
    original_filename = Column(String(500), nullable=False)
    doc_type = Column(SAEnum('pdf', 'docx', 'txt', 'markdown', 'csv', 'xlsx', 'html', 'image', 'audio', 'video', 'url', name='documenttype', create_type=False), nullable=False)
    status = Column(SAEnum('pending', 'processing', 'completed', 'failed', name='documentstatus', create_type=False), default='pending', nullable=False, index=True)
    storage_path = Column(String(1000), nullable=True)
    file_size = Column(Integer, nullable=True)
    mime_type = Column(String(100), nullable=True)
    page_count = Column(Integer, nullable=True)
    chunk_count = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    source_url = Column(String(2000), nullable=True)
    metadata_extra = Column(JSONB, default={})
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    processed_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    collection = relationship("Collection", back_populates="documents")
    chunks = relationship("Chunk", back_populates="document", cascade="all, delete-orphan")
