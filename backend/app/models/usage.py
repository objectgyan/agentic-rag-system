"""Usage tracking for rate limiting and billing."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, Integer, Float
from sqlalchemy.dialects.postgresql import UUID
from app.core.database import Base


class UsageRecord(Base):
    __tablename__ = "usage_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), nullable=True)
    resource_type = Column(String(50), nullable=False)  # query, document, storage, embedding
    quantity = Column(Integer, default=1)
    tokens_used = Column(Integer, nullable=True)
    cost_usd = Column(Float, nullable=True)
    model_used = Column(String(100), nullable=True)
    period = Column(String(7), nullable=False)  # YYYY-MM format
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
