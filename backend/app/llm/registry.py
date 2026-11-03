"""Resolve a model string to a ready-to-use ``LLMProvider``.

Model strings
-------------
- ``"provider:model"`` is explicit: ``anthropic:claude-opus-5-5``, ``openai:gpt-4o-mini``,
  ``ollama:llama3.1:8b`` (only the first ``:`` splits, and only when the prefix is a known
  provider — so Ollama-style tags like ``llama3.1:8b`` stay intact).
- A bare name is inferred: ``claude-*`` -> anthropic, anything else -> openai. This keeps every
  existing model string in the database and API working unchanged.

Custom endpoints (the client-deployment lever)
----------------------------------------------
``settings.llm_endpoints`` declares extra OpenAI-compatible providers by name, e.g. in ``.env``::

    LLM_ENDPOINTS={"ollama": {"base_url": "http://ollama:11434/v1"},
                   "azure": {"base_url": "https://acme.openai.azure.com/openai/v1/", "api_key": "..."}}

after which ``DEFAULT_LLM_MODEL=ollama:llama3.1:8b`` routes every call to that server.

Extending in code
-----------------
``register_provider("bedrock", factory)`` adds a new adapter kind; ``factory(model) -> LLMProvider``.

Testing
-------
``with use_llm(FakeLLM([...])):`` makes every ``get_llm()`` return the fake.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import OrderedDict
from typing import Callable, Dict, Iterator, List, Optional, Tuple

from app.core.config import settings
from app.llm.base import LLMProvider

ProviderFactory = Callable[[str], LLMProvider]

_factories: Dict[str, ProviderFactory] = {}
_override: Optional[LLMProvider] = None

# Provider instances hold an HTTP connection pool, so we reuse them — but a pool is bound to the
# event loop that created it, and Celery runs each task in a fresh loop. Keying the cache by the
# running loop avoids "Event loop is closed" errors; the LRU bound stops dead loops piling up.
_cache: "OrderedDict[Tuple[str, str, int], LLMProvider]" = OrderedDict()
_CACHE_SIZE = 32


def _anthropic_factory(model: str) -> LLMProvider:
    from app.llm.anthropic_provider import AnthropicProvider

    return AnthropicProvider(
        model,
        api_key=settings.anthropic_api_key,
        timeout=settings.external_api_timeout_seconds,
        max_retries=settings.external_api_max_retries,
        default_max_tokens=settings.llm_max_output_tokens,
        refusal_fallback=settings.anthropic_refusal_fallback,
    )


def _openai_factory(model: str) -> LLMProvider:
    from app.llm.openai_provider import OpenAIProvider

    return OpenAIProvider(
        model,
        api_key=settings.openai_api_key,
        timeout=settings.external_api_timeout_seconds,
        max_retries=settings.external_api_max_retries,
        default_max_tokens=min(settings.llm_max_output_tokens, 16000),
    )


def _endpoint_factory(name: str) -> ProviderFactory:
    def factory(model: str) -> LLMProvider:
        cfg = settings.llm_endpoints[name]
        kind = cfg.get("kind", "openai")
        if kind == "anthropic":
            from app.llm.anthropic_provider import AnthropicProvider

            return AnthropicProvider(
                model,
                api_key=cfg.get("api_key"),
                base_url=cfg.get("base_url"),
                timeout=settings.external_api_timeout_seconds,
                max_retries=settings.external_api_max_retries,
                default_max_tokens=cfg.get("max_output_tokens", settings.llm_max_output_tokens),
                refusal_fallback=False,
            )
        from app.llm.openai_provider import OpenAIProvider

        return OpenAIProvider(
            model,
            api_key=cfg.get("api_key"),
            base_url=cfg.get("base_url"),
            timeout=settings.external_api_timeout_seconds,
            max_retries=settings.external_api_max_retries,
            default_max_tokens=cfg.get("max_output_tokens", 4096),
            provider_name=name,
            max_tokens_param=cfg.get("max_tokens_param"),
        )

    return factory


register_provider = _factories.__setitem__
register_provider("anthropic", _anthropic_factory)
register_provider("openai", _openai_factory)


def known_providers() -> List[str]:
    return sorted(set(_factories) | set(settings.llm_endpoints))


def resolve(model: Optional[str] = None) -> Tuple[str, str]:
    """Split a model string into ``(provider, model_name)``."""
    model = model or settings.default_llm_model
    prefix, sep, rest = model.partition(":")
    if sep and prefix in known_providers():
        return prefix, rest
    return ("anthropic" if model.startswith("claude") else "openai"), model


def get_llm(model: Optional[str] = None) -> LLMProvider:
    """The provider for ``model`` (default: ``settings.default_llm_model``)."""
    if _override is not None:
        return _override
    provider, name = resolve(model)
    try:
        loop_id = id(asyncio.get_running_loop())
    except RuntimeError:
        loop_id = 0
    key = (provider, name, loop_id)
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    if provider in settings.llm_endpoints:
        factory = _endpoint_factory(provider)
    elif provider in _factories:
        factory = _factories[provider]
    else:
        raise ValueError(f"Unknown LLM provider {provider!r}; known: {known_providers()}")
    llm = factory(name)
    _cache[key] = llm
    while len(_cache) > _CACHE_SIZE:
        _cache.popitem(last=False)
    return llm


def get_fast_llm() -> LLMProvider:
    """The cheap model for helper calls (query rewriting, compression, judging, summaries)."""
    return get_llm(settings.default_fast_model)


@contextlib.contextmanager
def use_llm(llm: LLMProvider) -> Iterator[LLMProvider]:
    """Route every ``get_llm()`` call to ``llm`` for the duration of the block (tests, demos)."""
    global _override
    previous, _override = _override, llm
    try:
        yield llm
    finally:
        _override = previous


def model_allowed(allowed: List[str], model: Optional[str]) -> bool:
    """Whether a tier's model allowlist permits ``model``. ``"*"`` allows everything.

    Entries may be exact names (``claude-haiku-4-5``) or provider wildcards (``ollama:*``).
    """
    if model is None or "*" in allowed:
        return True
    provider, name = resolve(model)
    return name in allowed or model in allowed or f"{provider}:*" in allowed
