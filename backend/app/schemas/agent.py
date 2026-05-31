"""Agent execution schemas."""

from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class AgentStep(BaseModel):
    step_number: int
    thought: str
    action: Optional[str] = None
    action_input: Optional[dict] = None
    observation: Optional[str] = None


class AgentExecuteRequest(BaseModel):
    task: str = Field(min_length=1, max_length=10000)
    agent_type: str = "research"  # research, analyst, summarizer, code, custom
    collection_ids: Optional[List[UUID]] = None
    model: Optional[str] = None
    max_steps: int = Field(default=10, ge=1, le=30)
    tools: Optional[List[str]] = None


class AgentExecuteResponse(BaseModel):
    result: str
    steps: List[AgentStep]
    model_used: str
    total_tokens: int
    execution_time_ms: float
