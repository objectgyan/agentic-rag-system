"""Query and chat schemas."""

from typing import Optional, List
from pydantic import BaseModel, Field
from uuid import UUID
from datetime import datetime


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=10000)
    collection_ids: Optional[List[UUID]] = None
    # When set, the query is part of a conversation: prior turns are fed to the model as
    # history and both this turn and the answer are persisted (C1).
    conversation_id: Optional[UUID] = None
    model: Optional[str] = None
    top_k: int = Field(default=5, ge=1, le=50)
    use_reranking: bool = True
    use_hyde: bool = False
    use_multi_query: bool = False
    filters: Optional[dict] = None
    include_citations: bool = True
    temperature: float = Field(default=0.1, ge=0, le=2)


class Citation(BaseModel):
    document_id: UUID
    document_name: str
    chunk_id: UUID
    content: str
    page_number: Optional[int]
    score: float


class QueryResponse(BaseModel):
    answer: str
    citations: List[Citation] = []
    model_used: str
    tokens_used: Optional[int]
    retrieval_time_ms: float
    generation_time_ms: float
    # Names of optional features that silently degraded (e.g. "hyde", "multi_query")
    # so a client/operator can see when an answer was produced on a reduced path (F12).
    degraded: List[str] = []


class ConversationCreate(BaseModel):
    collection_id: Optional[UUID] = None
    title: Optional[str] = None
    model: Optional[str] = None


class ConversationResponse(BaseModel):
    id: UUID
    title: Optional[str]
    collection_id: Optional[UUID]
    model: Optional[str]
    created_at: datetime
    updated_at: datetime
    message_count: int = 0

    class Config:
        from_attributes = True


class ChatMessage(BaseModel):
    role: str
    content: str
    citations: Optional[List[Citation]] = None
    created_at: Optional[datetime] = None


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=10000)
    conversation_id: Optional[UUID] = None
    collection_ids: Optional[List[UUID]] = None
    model: Optional[str] = None
    use_agent: bool = False
