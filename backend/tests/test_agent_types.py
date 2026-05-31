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


def test_tool_catalog_is_the_expected_set():
    # The full catalog (handlers all exist); availability is then filtered by config (C4).
    catalog = {t["name"] for t in ToolRegistry.TOOLS}
    assert catalog == {"retrieval", "calculator", "web_search", "summarize", "compare"}


def test_static_agent_types_only_reference_real_tools():
    # Static lists reference the full catalog (web_search is real even if a key gates
    # its runtime availability); /agents/types filters to enabled tools at request time.
    catalog = {t["name"] for t in ToolRegistry.TOOLS}
    for agent in _AGENT_TYPES:
        for tool in agent["tools"]:
            assert tool in catalog, f"{agent['type']} references unknown tool {tool}"
