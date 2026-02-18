"""Query and chat endpoints with streaming support."""

import time
import json
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.models.user import User
from app.models.conversation import Conversation, Message, MessageRole
from app.schemas.query import (
    QueryRequest, QueryResponse, ChatRequest, ConversationCreate,
    ConversationResponse, ChatMessage,
)
from app.api.deps.auth import get_current_user
from app.services.rag.pipeline import RAGPipeline
from app.services.rag.retriever import HybridRetriever
from uuid import UUID
from typing import List
from sqlalchemy import select, func

router = APIRouter()


@router.post("", response_model=QueryResponse)
@router.post("/", response_model=QueryResponse)
async def query(
    req: QueryRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Execute a single RAG query."""
    start = time.time()
    pipeline = RAGPipeline(db=db, tenant_id=str(user.tenant_id), user_id=str(user.id))

    result = await pipeline.query(
        query=req.query,
        collection_ids=[str(c) for c in req.collection_ids] if req.collection_ids else None,
        top_k=req.top_k,
        model=req.model,
        use_reranking=req.use_reranking,
        use_hyde=req.use_hyde,
        use_multi_query=req.use_multi_query,
        temperature=req.temperature,
        include_citations=req.include_citations,
    )
    total_time = (time.time() - start) * 1000
    result["generation_time_ms"] = total_time - result.get("retrieval_time_ms", 0)
    return QueryResponse(**result)


@router.post("/stream")
async def query_stream(
    req: QueryRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Execute a RAG query with streaming response (SSE)."""
    pipeline = RAGPipeline(db=db, tenant_id=str(user.tenant_id), user_id=str(user.id))

    async def event_generator():
        async for chunk in pipeline.query_stream(
            query=req.query,
            collection_ids=[str(c) for c in req.collection_ids] if req.collection_ids else None,
            top_k=req.top_k,
            model=req.model,
            use_reranking=req.use_reranking,
            temperature=req.temperature,
        ):
            yield f"data: {json.dumps(chunk)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/conversations", response_model=ConversationResponse)
async def create_conversation(
    req: ConversationCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new conversation."""
    conv = Conversation(
        tenant_id=user.tenant_id,
        user_id=user.id,
        collection_id=req.collection_id,
        title=req.title or "New Conversation",
        model=req.model,
    )
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    resp = ConversationResponse.model_validate(conv)
    resp.message_count = 0
    return resp


@router.get("/conversations", response_model=List[ConversationResponse])
async def list_conversations(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List user's conversations."""
    result = await db.execute(
        select(Conversation)
        .where(Conversation.tenant_id == user.tenant_id, Conversation.user_id == user.id)
        .order_by(Conversation.updated_at.desc())
    )
    convs = result.scalars().all()
    responses = []
    for c in convs:
        msg_count = await db.execute(
            select(func.count()).select_from(Message).where(Message.conversation_id == c.id)
        )
        resp = ConversationResponse.model_validate(c)
        resp.message_count = msg_count.scalar()
        responses.append(resp)
    return responses


@router.get("/conversations/{conversation_id}/messages", response_model=List[ChatMessage])
async def get_messages(
    conversation_id: UUID,
    limit: int = 50,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get messages in a conversation."""
    conv_result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user.id,
        )
    )
    if not conv_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Conversation not found")

    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    messages = result.scalars().all()
    return [
        ChatMessage(role=m.role.value, content=m.content, citations=m.citations, created_at=m.created_at)
        for m in reversed(messages)
    ]
