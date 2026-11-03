"""Tests for the external-API client factories (F11).

Confirms the SDK clients are built with the configured retry budget (and, for
cohere, that the client actually accepts the timeout/max_retries kwargs).
Skips provider clients when no API key is configured.
"""

import pytest

from app.core.config import settings
from app.core.llm_clients import cohere_client, openai_client
from app.llm import get_llm


def test_openai_client_uses_configured_retries():
    if not settings.openai_api_key:
        pytest.skip("OPENAI_API_KEY not set")
    client = openai_client()
    assert client.max_retries == settings.external_api_max_retries
    assert client.timeout is not None


def test_anthropic_provider_uses_configured_retries(monkeypatch):
    # Construction needs a credential, not a network call — a dummy key is enough.
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-test")
    llm = get_llm("anthropic:claude-haiku-4-5")
    assert llm._client.max_retries == settings.external_api_max_retries
    assert llm._client.timeout == settings.external_api_timeout_seconds


def test_cohere_client_accepts_timeout_and_retries(monkeypatch):
    # The v2 client refuses to construct without a key (call sites only build it when one is
    # configured); a dummy key proves it accepts our timeout/max_retries kwargs.
    monkeypatch.setattr(settings, "cohere_api_key", "co-test")
    client = cohere_client()
    assert client is not None
