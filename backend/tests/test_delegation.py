"""Tests for multi-agent delegation (A4)."""

import pytest

from app.services.agents.tools import ToolRegistry


def _registry(depth):
    return ToolRegistry(db=None, tenant_id="t", retriever=None, user_id="u", depth=depth)


def test_delegate_offered_at_top_depth_only():
    assert "delegate" in [t["name"] for t in _registry(0).get_tools()]
    # at the recursion cap, the delegate tool disappears so a sub-agent can't recurse.
    assert "delegate" not in [t["name"] for t in _registry(1).get_tools()]


@pytest.mark.asyncio
async def test_delegate_refuses_past_depth_cap():
    out = await _registry(1)._tool_delegate({"task": "anything"})
    assert "depth limit" in out.lower()


@pytest.mark.asyncio
async def test_delegate_requires_a_task():
    out = await _registry(0)._tool_delegate({"agent_type": "research"})
    assert "task" in out.lower()


@pytest.mark.asyncio
async def test_delegate_runs_a_subagent_at_next_depth(monkeypatch):
    captured = {}

    class _FakeOrchestrator:
        def __init__(self, db, tenant_id, user_id, depth):
            captured["depth"] = depth

        async def execute(self, task, agent_type, max_steps):
            captured["task"] = task
            captured["agent_type"] = agent_type
            return {"result": "sub-agent answer"}

    import app.services.agents.orchestrator as orch_mod

    monkeypatch.setattr(orch_mod, "AgentOrchestrator", _FakeOrchestrator)

    out = await _registry(0)._tool_delegate({"agent_type": "analyst", "task": "analyze X"})

    assert out == "sub-agent answer"
    assert captured["depth"] == 1  # sub-agent runs one level deeper
    assert captured["task"] == "analyze X"
    assert captured["agent_type"] == "analyst"
