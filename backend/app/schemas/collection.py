"""Collection schemas."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    visibility: str = "shared"
    embedding_model: Optional[str] = None
    chunk_strategy: str = "semantic"
    chunk_size: int = 512
    chunk_overlap: int = 50
    # Extract knowledge-graph triples during ingestion for this collection (A3).
    enable_graph: bool = False


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
    enable_graph: bool = False
    document_count: int = 0
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
