"""Tests for the external-API client factories (F11).

Confirms the SDK clients are built with the configured retry budget (and, for
cohere, that the v4 client actually accepts the timeout/max_retries kwargs).
Skips provider clients when no API key is configured.
"""

import pytest

from app.core.config import settings
from app.core.llm_clients import openai_client, anthropic_client, cohere_client


def test_openai_client_uses_configured_retries():
    if not settings.openai_api_key:
        pytest.skip("OPENAI_API_KEY not set")
    client = openai_client()
    assert client.max_retries == settings.external_api_max_retries
    assert client.timeout is not None


def test_anthropic_client_uses_configured_retries():
    if not settings.anthropic_api_key:
        pytest.skip("ANTHROPIC_API_KEY not set")
    client = anthropic_client()
    assert client.max_retries == settings.external_api_max_retries
    assert client.timeout is not None


def test_cohere_client_accepts_timeout_and_retries():
    # cohere may have no key configured, but construction must still succeed —
    # this is what proves the v4 AsyncClient accepts our timeout/max_retries kwargs.
    client = cohere_client()
    assert client is not None
