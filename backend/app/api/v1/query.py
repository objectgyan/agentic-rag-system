"""Query and chat endpoints with streaming support."""

import json
import time
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.access import assert_collection_accessible, assert_collections_accessible
from app.api.deps.auth import get_current_user
from app.core.database import get_db, set_tenant_context
from app.core.metrics import (
    rag_generation_seconds,
    rag_queries_total,
    rag_retrieval_seconds,
)
from app.core.security import decode_token
from app.models.conversation import Conversation, Message, MessageRole
from app.models.user import User
from app.schemas.query import (
    ChatMessage,
    ConversationCreate,
    ConversationResponse,
    QueryRequest,
    QueryResponse,
)
from app.services.rag.pipeline import RAGPipeline

router = APIRouter()


async def _load_conversation_history(db: AsyncSession, user: User, conversation_id):
    """Authorize a conversation and return (conversation, history) for C1.

    history is a list of {role, content} dicts in chronological order, suitable to feed
    the generator as prior turns. 404s if the conversation isn't the caller's.
    """
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == user.tenant_id,
            Conversation.user_id == user.id,
        )
    )
    conversation = result.scalar_one_or_none()
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    msgs = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
    )
    history = [{"role": m.role.value, "content": m.content} for m in msgs.scalars().all()]
    return conversation, history


async def _persist_turn(db: AsyncSession, user: User, conversation, query_text, answer, citations):
    """Persist the user turn and the assistant answer, and bump updated_at (C1).

    tenant_id is set explicitly so the rows satisfy the messages RLS policy (F3).
    """
    from datetime import datetime, timezone

    db.add(Message(
        tenant_id=user.tenant_id,
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content=query_text,
    ))
    db.add(Message(
        tenant_id=user.tenant_id,
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content=answer,
        citations=citations or [],
    ))
    conversation.updated_at = datetime.now(timezone.utc)
    await db.commit()


@router.post("", response_model=QueryResponse)
@router.post("/", response_model=QueryResponse)
async def query(
    req: QueryRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Execute a single RAG query (optionally within a conversation)."""
    start = time.time()
    # Authorize the requested collections before retrieval touches them (F2).
    await assert_collections_accessible(db, user, req.collection_ids)

    # If this query belongs to a conversation, load prior turns as history (C1).
    conversation = None
    history = None
    if req.conversation_id:
        conversation, history = await _load_conversation_history(db, user, req.conversation_id)

    pipeline = RAGPipeline(db=db, tenant_id=str(user.tenant_id), user_id=str(user.id))

    try:
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
            conversation_history=history,
            evaluate=req.evaluate,
            use_compression=req.use_compression,
            use_multi_hop=req.use_multi_hop,
            max_hops=req.max_hops,
            use_graph=req.use_graph,
            trace=req.trace,
        )
    except Exception:
        rag_queries_total.labels(status="error").inc()
        raise

    total_time = (time.time() - start) * 1000
    retrieval_ms = result.get("retrieval_time_ms", 0)
    result["generation_time_ms"] = total_time - retrieval_ms

    # Record domain metrics (O2).
    rag_queries_total.labels(status="success").inc()
    rag_retrieval_seconds.observe(retrieval_ms / 1000)
    rag_generation_seconds.observe(result["generation_time_ms"] / 1000)

    # Persist both turns so the next message in this conversation has memory (C1).
    if conversation is not None:
        await _persist_turn(db, user, conversation, req.query, result["answer"], result.get("citations"))

    return QueryResponse(**result)


@router.post("/stream")
async def query_stream(
    req: QueryRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Execute a RAG query with streaming response (SSE)."""
    # Authorize before the stream starts — a 403 mid-SSE-stream is hard for clients to handle.
    await assert_collections_accessible(db, user, req.collection_ids)

    # Conversation memory for the streaming path too (C1): load history up front, and
    # persist both turns once the stream finishes.
    conversation = None
    history = None
    if req.conversation_id:
        conversation, history = await _load_conversation_history(db, user, req.conversation_id)

    pipeline = RAGPipeline(db=db, tenant_id=str(user.tenant_id), user_id=str(user.id))

    async def event_generator():
        full_answer = ""
        final_citations = None
        async for chunk in pipeline.query_stream(
            query=req.query,
            collection_ids=[str(c) for c in req.collection_ids] if req.collection_ids else None,
            top_k=req.top_k,
            model=req.model,
            use_reranking=req.use_reranking,
            temperature=req.temperature,
            conversation_history=history,
        ):
            if chunk.get("type") == "token":
                full_answer += chunk.get("content", "")
            elif chunk.get("type") == "citations":
                final_citations = chunk.get("citations")
            yield f"data: {json.dumps(chunk)}\n\n"

        if conversation is not None and full_answer:
            await _persist_turn(db, user, conversation, req.query, full_answer, final_citations)
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.websocket("/ws/chat")
async def ws_chat(
    websocket: WebSocket,
    token: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Bidirectional chat over WebSocket. Auth via the `token` query param (browsers
    can't set headers on a WS handshake). Streams the same token/citations frames the
    SSE endpoint sends, with conversation memory, over a long-lived connection."""
    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    result = await db.execute(select(User).where(User.id == payload.get("sub"), User.is_active.is_(True)))
    user = result.scalar_one_or_none()
    if not user:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await set_tenant_context(db, str(user.tenant_id))
    await websocket.accept()
    pipeline = RAGPipeline(db=db, tenant_id=str(user.tenant_id), user_id=str(user.id))

    try:
        while True:
            req = await websocket.receive_json()
            query_text = (req.get("query") or "").strip()
            if not query_text:
                continue

            collection_ids = req.get("collection_ids")
            try:
                await assert_collections_accessible(db, user, collection_ids)
            except HTTPException as exc:
                await websocket.send_json({"type": "error", "detail": exc.detail})
                continue

            conversation = None
            history = None
            if req.get("conversation_id"):
                try:
                    conversation, history = await _load_conversation_history(db, user, req["conversation_id"])
                except HTTPException:
                    conversation, history = None, None

            full_answer = ""
            final_citations = None
            async for chunk in pipeline.query_stream(
                query=query_text,
                collection_ids=collection_ids,
                top_k=req.get("top_k", 5),
                model=req.get("model"),
                use_reranking=req.get("use_reranking", True),
                conversation_history=history,
            ):
                if chunk.get("type") == "token":
                    full_answer += chunk.get("content", "")
                elif chunk.get("type") == "citations":
                    final_citations = chunk.get("citations")
                await websocket.send_json(chunk)

            if conversation is not None and full_answer:
                await _persist_turn(db, user, conversation, query_text, full_answer, final_citations)
            await websocket.send_json({"type": "done"})
    except WebSocketDisconnect:
        return


@router.post("/conversations", response_model=ConversationResponse)
async def create_conversation(
    req: ConversationCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new conversation."""
    # A conversation may be scoped to a collection — verify access to it (F2).
    if req.collection_id is not None:
        await assert_collection_accessible(db, user, req.collection_id)
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
