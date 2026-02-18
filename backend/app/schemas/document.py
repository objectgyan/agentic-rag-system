"""Document schemas."""

from typing import Optional
from pydantic import BaseModel, HttpUrl
from uuid import UUID
from datetime import datetime


class DocumentUploadResponse(BaseModel):
    id: UUID
    filename: str
    doc_type: str
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class DocumentResponse(BaseModel):
    id: UUID
    collection_id: UUID
    filename: str
    original_filename: str
    doc_type: str
    status: str
    file_size: Optional[int]
    page_count: Optional[int]
    chunk_count: int
    error_message: Optional[str]
    source_url: Optional[str]
    created_at: datetime
    processed_at: Optional[datetime]

    class Config:
        from_attributes = True


class DocumentURLIngest(BaseModel):
    url: str
    collection_id: UUID
    recursive: bool = False
    max_pages: int = 10
