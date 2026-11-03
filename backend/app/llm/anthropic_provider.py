"""Anthropic (Claude) adapter for the LLM port.

Translation rules (neutral types -> Messages API):

- ``system=`` becomes the top-level ``system`` field, marked ``cache_control`` so a long, stable
  system prompt is billed at the cache-read rate on repeat calls (prompt caching).
- Assistant turns that came from Claude are replayed from their native content blocks, unchanged
  — current models bind thinking blocks to the conversation, and editing them is a 400.
- Consecutive ``tool`` messages are merged into ONE user message of ``tool_result`` blocks;
  splitting parallel tool results across messages teaches the model to stop calling in parallel.
- ``temperature`` is not sent: the Claude API removed sampling parameters (API 2026-10-21).
- ``effort`` maps to ``output_config.effort`` on models that support it.
- Refusals: on models that support it, the request opts into the server-side fallback
  (``fallbacks="default"``), which re-runs a declined request on Anthropic's recommended
  fallback model inside the same call. A refusal that survives comes back as
  ``stop_reason="refusal"`` with empty text — callers must handle that, never crash on it.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence

from app.llm.types import LLMResponse, Message, StreamEvent, ToolCall, ToolSpec, Usage

logger = logging.getLogger(__name__)

_FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Models whose thinking is always on: thinking tokens count against max_tokens, so tiny
# caps (e.g. 10 tokens for a judge score) would be consumed before any text is written.
_ALWAYS_THINKING_PREFIXES = ("claude-opus-5", "claude-sonnet-5-5", "claude-fable-5", "claude-mythos-5")
_MIN_MAX_TOKENS_WHEN_THINKING = 4096

# Models that accept the server-side refusal fallback (`fallbacks="default"`).
_FALLBACK_MODELS = ("claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5")


def _supports_effort(model: str) -> bool:
    # `effort` errors on Haiku 4.5 / Sonnet 4.5 and older models.
    return not ("haiku" in model or "-4-5" in model or "claude-3" in model)


def _map_stop_reason(reason: Optional[str]) -> str:
    return {
        "end_turn": "end_turn",
        "stop_sequence": "end_turn",
        "tool_use": "tool_use",
        "max_tokens": "max_tokens",
        "refusal": "refusal",
    }.get(reason or "", "other")


def _echoable(blocks: List[Any]) -> List[Any]:
    """Apply the fallback echo rule before replaying an assistant turn.

    After a mid-output fallback, thinking / redacted_thinking / tool_use blocks that appear
    *before* the final ``fallback`` block must be omitted. Everything else replays as-is.
    """
    last_fallback = max((i for i, b in enumerate(blocks) if _btype(b) == "fallback"), default=-1)
    if last_fallback < 0:
        return blocks
    drop = {"thinking", "redacted_thinking", "tool_use"}
    return [b for i, b in enumerate(blocks) if i > last_fallback or _btype(b) not in drop]


def _btype(block: Any) -> Optional[str]:
    return block.get("type") if isinstance(block, dict) else getattr(block, "type", None)


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        model: str,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        default_max_tokens: int = 16000,
        refusal_fallback: bool = True,
        client: Any = None,
    ):
        self.model = model
        self.default_max_tokens = default_max_tokens
        # Server-side fallback is a Claude API feature; skip it behind a custom gateway.
        self.refusal_fallback = refusal_fallback and base_url is None and model.startswith(_FALLBACK_MODELS)
        if client is None:
            import anthropic

            kwargs: Dict[str, Any] = {"timeout": timeout, "max_retries": max_retries}
            if api_key:
                kwargs["api_key"] = api_key
            if base_url:
                kwargs["base_url"] = base_url
            client = anthropic.AsyncAnthropic(**kwargs)
        self._client = client

    # ---- request building -------------------------------------------------------------

    def _to_api_messages(self, messages: List[Message]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for m in messages:
            role = m["role"]
            if role == "system":
                # A system turn inside history (rare) is folded in as user context.
                out.append({"role": "user", "content": f"[context]\n{m['content']}"})
            elif role == "tool":
                block = {
                    "type": "tool_result",
                    "tool_use_id": m["tool_call_id"],
                    "content": m["content"],
                }
                if m.get("is_error"):
                    block["is_error"] = True
                prev = out[-1] if out else None
                if prev and prev["role"] == "user" and isinstance(prev["content"], list) and all(
                    b.get("type") == "tool_result" for b in prev["content"]
                ):
                    prev["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
            elif role == "assistant":
                native = m.get("_native")
                if native and native[0] == self.name:
                    out.append({"role": "assistant", "content": _echoable(list(native[1]))})
                elif m.get("tool_calls"):
                    blocks: List[Dict[str, Any]] = []
                    if m.get("content"):
                        blocks.append({"type": "text", "text": m["content"]})
                    for c in m["tool_calls"]:
                        blocks.append({"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments})
                    out.append({"role": "assistant", "content": blocks})
                else:
                    out.append({"role": "assistant", "content": m.get("content") or ""})
            else:
                out.append({"role": "user", "content": m["content"]})
        return out

    def _params(
        self,
        messages: List[Message],
        system: Optional[str],
        tools: Optional[Sequence[ToolSpec]],
        max_tokens: Optional[int],
        effort: Optional[str],
    ) -> Dict[str, Any]:
        cap = max_tokens or self.default_max_tokens
        if self.model.startswith(_ALWAYS_THINKING_PREFIXES):
            cap = max(cap, _MIN_MAX_TOKENS_WHEN_THINKING)
        params: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": cap,
            "messages": self._to_api_messages(messages),
        }
        if system:
            params["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        if tools:
            params["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools
            ]
        if effort and _supports_effort(self.model):
            params["output_config"] = {"effort": effort}
        if self.refusal_fallback:
            params["betas"] = [_FALLBACK_BETA]
            params["fallbacks"] = "default"
        return params

    def _messages_api(self, params: Dict[str, Any]):
        # The fallback parameter lives on the beta surface; everything else is identical.
        return self._client.beta.messages if "betas" in params else self._client.messages

    # ---- response parsing -------------------------------------------------------------

    def _to_response(self, msg: Any) -> LLMResponse:
        text_parts: List[str] = []
        calls: List[ToolCall] = []
        for block in msg.content:
            btype = _btype(block)
            if btype == "text":
                text_parts.append(block.text)
            elif btype == "tool_use":
                args = block.input if isinstance(block.input, dict) else json.loads(block.input or "{}")
                calls.append(ToolCall(id=block.id, name=block.name, arguments=args))
            # thinking / fallback / other blocks are kept only in `native` for replay.
        stop = _map_stop_reason(msg.stop_reason)
        if stop == "refusal":
            logger.warning("Claude declined the request (stop_details=%s)", getattr(msg, "stop_details", None))
        u = msg.usage
        cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(u, "cache_creation_input_tokens", 0) or 0
        return LLMResponse(
            text="".join(text_parts),
            model=getattr(msg, "model", None) or self.model,
            usage=Usage(
                input_tokens=(u.input_tokens or 0) + cache_read + cache_write,
                output_tokens=u.output_tokens or 0,
                cached_input_tokens=cache_read,
            ),
            tool_calls=calls,
            stop_reason=stop,  # type: ignore[arg-type]
            native=list(msg.content),
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
        temperature: Optional[float] = None,  # not supported by the Claude API; see module doc
        effort: Optional[str] = None,
    ) -> LLMResponse:
        params = self._params(messages, system, tools, max_tokens, effort)
        msg = await self._messages_api(params).create(**params)
        return self._to_response(msg)

    async def stream(
        self,
        messages: List[Message],
        *,
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        effort: Optional[str] = None,
    ) -> AsyncIterator[StreamEvent]:
        params = self._params(messages, system, None, max_tokens, effort)
        async with self._messages_api(params).stream(**params) as stream:
            async for text in stream.text_stream:
                yield StreamEvent(type="text", text=text)
            final = await stream.get_final_message()
        yield StreamEvent(type="done", response=self._to_response(final))
