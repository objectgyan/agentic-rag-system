"""Usage metering: one row per billable model call (queries, streamed chats, agent runs)."""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings


async def record_usage(
    db: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    resource_type: str,
    model: Optional[str],
    tokens: Optional[int],
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
) -> None:
    """Add a ``UsageRecord`` with an estimated cost (item 3). Flushed, not committed."""
    from app.models.usage import UsageRecord
    from app.services.rag.tracing import estimate_cost

    model_used = model or settings.default_llm_model
    cost = (
        estimate_cost(model_used, prompt_tokens or 0, completion_tokens or 0)
        if (prompt_tokens or completion_tokens)
        else None
    )
    db.add(UsageRecord(
        tenant_id=tenant_id,
        user_id=user_id,
        resource_type=resource_type,
        tokens_used=tokens,
        cost_usd=cost,
        model_used=model_used,
        period=datetime.now(timezone.utc).strftime("%Y-%m"),
    ))
    await db.flush()
