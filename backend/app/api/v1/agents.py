"""Agent execution endpoints."""

import time
import json
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.models.user import User
from app.schemas.agent import AgentExecuteRequest, AgentExecuteResponse
from app.api.deps.auth import get_current_user
from app.api.deps.access import assert_collections_accessible
from app.services.agents.orchestrator import AgentOrchestrator

router = APIRouter()


@router.post("/execute", response_model=AgentExecuteResponse)
async def execute_agent(
    req: AgentExecuteRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Execute an agent task with full trace."""
    start = time.time()
    await assert_collections_accessible(db, user, req.collection_ids)
    orchestrator = AgentOrchestrator(
        db=db,
        tenant_id=str(user.tenant_id),
        user_id=str(user.id),
    )
    result = await orchestrator.execute(
        task=req.task,
        agent_type=req.agent_type,
        collection_ids=[str(c) for c in req.collection_ids] if req.collection_ids else None,
        model=req.model,
        max_steps=req.max_steps,
        tools=req.tools,
    )
    result["execution_time_ms"] = (time.time() - start) * 1000
    return AgentExecuteResponse(**result)


@router.post("/stream")
async def stream_agent(
    req: AgentExecuteRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Stream agent execution with step-by-step updates."""
    await assert_collections_accessible(db, user, req.collection_ids)
    orchestrator = AgentOrchestrator(
        db=db,
        tenant_id=str(user.tenant_id),
        user_id=str(user.id),
    )

    async def event_generator():
        async for step in orchestrator.execute_stream(
            task=req.task,
            agent_type=req.agent_type,
            collection_ids=[str(c) for c in req.collection_ids] if req.collection_ids else None,
            model=req.model,
            max_steps=req.max_steps,
        ):
            yield f"data: {json.dumps(step)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/types")
async def list_agent_types():
    """List available agent types and their capabilities."""
    return {
        "agents": [
            {
                "type": "research",
                "name": "Research Agent",
                "description": "Multi-step information gathering with source triangulation",
                "tools": ["retrieval", "web_search", "calculator"],
            },
            {
                "type": "analyst",
                "name": "Analyst Agent",
                "description": "Data analysis, comparison, and trend identification",
                "tools": ["retrieval", "calculator", "code_execution"],
            },
            {
                "type": "summarizer",
                "name": "Summarizer Agent",
                "description": "Condensing large document sets into actionable summaries",
                "tools": ["retrieval"],
            },
            {
                "type": "code",
                "name": "Code Agent",
                "description": "Code understanding, generation, and debugging",
                "tools": ["retrieval", "code_execution"],
            },
        ]
    }
