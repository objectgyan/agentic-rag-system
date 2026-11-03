"""OpenAI Chat Completions adapter — also the adapter for every *OpenAI-compatible* server.

Most self-hosted and third-party inference servers expose the same ``/v1/chat/completions``
API: Azure OpenAI, Ollama, vLLM, LM Studio, Groq, Together, Fireworks, OpenRouter, LiteLLM.
Pointing this adapter at their ``base_url`` is how a client deployment swaps in a local or
regional model without code changes (see ``registry.py`` / the ``LLM_ENDPOINTS`` setting).

Translation rules (neutral types -> Chat Completions):

- ``system=`` becomes the first ``{"role": "system"}`` message.
- Tool calls round-trip as ``tool_calls`` on the assistant message + ``{"role": "tool"}`` results.
- Reasoning models (o-series, gpt-5) reject non-default ``temperature``; it is dropped for them
  and ``effort`` is sent as ``reasoning_effort`` instead.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence

from app.llm.types import LLMResponse, Message, StreamEvent, ToolCall, ToolSpec, Usage

_REASONING_PREFIXES = ("o1", "o3", "o4", "gpt-5")


def _map_finish(reason: Optional[str]) -> str:
    return {
        "stop": "end_turn",
        "tool_calls": "tool_use",
        "function_call": "tool_use",
        "length": "max_tokens",
        "content_filter": "refusal",
    }.get(reason or "", "other")


class OpenAIProvider:
    name = "openai"

    def __init__(
        self,
        model: str,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        default_max_tokens: int = 4096,
        provider_name: str = "openai",
        # Official OpenAI takes `max_completion_tokens`; many compatible servers only know the
        # older `max_tokens`, so custom endpoints default to that.
        max_tokens_param: Optional[str] = None,
        client: Any = None,
    ):
        self.model = model
        self.name = provider_name
        self.default_max_tokens = default_max_tokens
        self.max_tokens_param = max_tokens_param or ("max_tokens" if base_url else "max_completion_tokens")
        self.is_reasoning = model.startswith(_REASONING_PREFIXES)
        if client is None:
            import openai

            client = openai.AsyncOpenAI(
                # Local servers (Ollama, vLLM) accept any non-empty key.
                api_key=api_key or ("not-needed" if base_url else None),
                base_url=base_url,
                timeout=timeout,
                max_retries=max_retries,
            )
        self._client = client

    # ---- request building -------------------------------------------------------------

    @staticmethod
    def _to_api_messages(messages: List[Message], system: Optional[str]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if system:
            out.append({"role": "system", "content": system})
        for m in messages:
            role = m["role"]
            if role == "tool":
                out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
            elif role == "assistant" and m.get("tool_calls"):
                out.append({
                    "role": "assistant",
                    "content": m.get("content") or None,
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                        }
                        for c in m["tool_calls"]
                    ],
                })
            else:
                out.append({"role": role, "content": m.get("content") or ""})
        return out

    def _params(
        self,
        messages: List[Message],
        system: Optional[str],
        tools: Optional[Sequence[ToolSpec]],
        max_tokens: Optional[int],
        temperature: Optional[float],
        effort: Optional[str],
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "model": self.model,
            "messages": self._to_api_messages(messages, system),
            self.max_tokens_param: max_tokens or self.default_max_tokens,
        }
        if self.is_reasoning:
            if effort:
                params["reasoning_effort"] = effort
        elif temperature is not None:
            params["temperature"] = temperature
        if tools:
            params["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.input_schema},
                }
                for t in tools
            ]
        return params

    # ---- response parsing -------------------------------------------------------------

    @staticmethod
    def _usage(u: Any) -> Usage:
        if not u:
            return Usage()
        details = getattr(u, "prompt_tokens_details", None)
        return Usage(
            input_tokens=u.prompt_tokens or 0,
            output_tokens=u.completion_tokens or 0,
            cached_input_tokens=(getattr(details, "cached_tokens", 0) or 0) if details else 0,
        )

    def _to_response(self, resp: Any) -> LLMResponse:
        choice = resp.choices[0]
        calls: List[ToolCall] = []
        for tc in choice.message.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_raw": tc.function.arguments}
            calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))
        return LLMResponse(
            text=choice.message.content or "",
            model=getattr(resp, "model", None) or self.model,
            usage=self._usage(resp.usage),
            tool_calls=calls,
            stop_reason=_map_finish(choice.finish_reason),  # type: ignore[arg-type]
            provider=self.name,
        )

    # ---- port methods -----------------------------------------------------------------

    async def complete(
        self,
        messages: List[Message],
        *,
        system: Optional[str] = None,
        tools: Optional[Sequence[ToolSpec]] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        effort: Optional[str] = None,
    ) -> LLMResponse:
        params = self._params(messages, system, tools, max_tokens, temperature, effort)
        resp = await self._client.chat.completions.create(**params)
        return self._to_response(resp)

    async def stream(
        self,
        messages: List[Message],
        *,
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        effort: Optional[str] = None,
    ) -> AsyncIterator[StreamEvent]:
        params = self._params(messages, system, None, max_tokens, temperature, effort)
        # include_usage adds a final chunk with token counts, so streamed answers can be billed.
        params.update(stream=True, stream_options={"include_usage": True})
        stream = await self._client.chat.completions.create(**params)
        parts: List[str] = []
        usage = Usage()
        finish = None
        model = self.model
        async for chunk in stream:
            if chunk.usage:
                usage = self._usage(chunk.usage)
            if not chunk.choices:
                continue
            model = getattr(chunk, "model", None) or model
            delta = chunk.choices[0].delta
            if delta and delta.content:
                parts.append(delta.content)
                yield StreamEvent(type="text", text=delta.content)
            finish = chunk.choices[0].finish_reason or finish
        yield StreamEvent(
            type="done",
            response=LLMResponse(
                text="".join(parts),
                model=model,
                usage=usage,
                stop_reason=_map_finish(finish),  # type: ignore[arg-type]
                provider=self.name,
            ),
        )
