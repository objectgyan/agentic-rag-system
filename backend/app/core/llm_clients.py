"""Factories for external API clients with consistent timeouts + retries (F11).

Every LLM / embedding / rerank call should construct its client here, so timeouts
and bounded retries are applied uniformly instead of being forgotten per call site.

The OpenAI and Anthropic SDKs have built-in retry with exponential backoff + jitter
that honors Retry-After when given ``max_retries`` — we use that rather than wrapping
calls in our own tenacity layer, which would double-retry. The explicit ``timeout``
is the important part: the SDK default is 10 minutes, so without it one hung upstream
ties up a request/worker for that long.
"""

from app.core.config import settings


def openai_client():
    import openai

    return openai.AsyncOpenAI(
        api_key=settings.openai_api_key,
        timeout=settings.external_api_timeout_seconds,
        max_retries=settings.external_api_max_retries,
    )


def anthropic_client():
    import anthropic

    return anthropic.AsyncAnthropic(
        api_key=settings.anthropic_api_key,
        timeout=settings.external_api_timeout_seconds,
        max_retries=settings.external_api_max_retries,
    )


def cohere_client():
    import cohere

    # cohere v4's AsyncClient accepts timeout + max_retries; the rerank call site
    # also degrades gracefully if the client/call fails.
    return cohere.AsyncClient(
        api_key=settings.cohere_api_key,
        timeout=settings.external_api_timeout_seconds,
        max_retries=settings.external_api_max_retries,
    )
