"""Collection schemas."""

from typing import Optional
from pydantic import BaseModel, Field
from uuid import UUID
from datetime import datetime


class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    visibility: str = "shared"
    embedding_model: Optional[str] = None
    chunk_strategy: str = "semantic"
    chunk_size: int = 512
    chunk_overlap: int = 50


class CollectionUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    visibility: Optional[str] = None


class CollectionResponse(BaseModel):
    id: UUID
    name: str
    description: Optional[str]
    visibility: str
    embedding_model: Optional[str]
    chunk_strategy: str
    document_count: int = 0
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
