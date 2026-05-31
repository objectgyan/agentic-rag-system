"""Test that advertised agent tools all actually exist (C2)."""

from app.api.v1.agents import _AGENT_TYPES, list_agent_types
from app.services.agents.tools import ToolRegistry
import asyncio


def test_advertised_tools_are_all_real():
    available = set(ToolRegistry.available_tool_names())
    result = asyncio.run(list_agent_types())
    advertised = {t for agent in result["agents"] for t in agent["tools"]}
    # Every advertised tool must have a handler in the registry.
    assert advertised <= available
    # The phantom that used to be advertised is gone.
    assert "code_execution" not in advertised


def test_every_registry_tool_has_a_handler():
    # Guards the single-source-of-truth invariant: TOOLS entries must be executable.
    reg = ToolRegistry(db=None, tenant_id="t", retriever=None)
    handler_names = {
        "retrieval", "calculator", "web_search", "summarize", "compare",
    }
    assert set(reg.available_tool_names()) == handler_names


def test_static_agent_types_only_reference_real_tools():
    available = set(ToolRegistry.available_tool_names())
    for agent in _AGENT_TYPES:
        for tool in agent["tools"]:
            assert tool in available, f"{agent['type']} references unknown tool {tool}"
