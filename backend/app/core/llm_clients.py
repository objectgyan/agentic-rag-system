"""Factories for vendor clients used outside the LLM port, with consistent timeouts + retries (F11).

Chat/completion calls go through ``app/llm`` (``get_llm``), not these. What remains here are
non-chat APIs: OpenAI embeddings / Whisper / vision extraction, and Cohere embed + rerank.

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


def cohere_client():
    import cohere

    # Cohere v2 client (embed + rerank). Call sites degrade gracefully if it fails.
    return cohere.AsyncClientV2(
        api_key=settings.cohere_api_key,
        timeout=settings.external_api_timeout_seconds,
        max_retries=settings.external_api_max_retries,
    )
