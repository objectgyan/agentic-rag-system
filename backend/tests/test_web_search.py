"""Tests for the web_search tool's config-aware availability (C4)."""

import pytest

from app.core.config import settings
from app.services.agents.tools import ToolRegistry


def test_web_search_excluded_without_key(monkeypatch):
    monkeypatch.setattr(settings, "web_search_api_key", None)
    assert "web_search" not in ToolRegistry.available_tool_names()


def test_web_search_offered_with_key(monkeypatch):
    monkeypatch.setattr(settings, "web_search_api_key", "tvly-test-key")
    assert "web_search" in ToolRegistry.available_tool_names()


@pytest.mark.asyncio
async def test_web_search_reports_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "web_search_api_key", None)
    reg = ToolRegistry(db=None, tenant_id="t", retriever=None)
    msg = await reg._tool_web_search({"query": "anything"})
    assert "not configured" in msg.lower()
