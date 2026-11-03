"""The LLM port: the one interface every model provider implements."""

from __future__ import annotations

from typing import AsyncIterator, List, Optional, Protocol, Sequence, runtime_checkable

from app.llm.types import LLMResponse, Message, StreamEvent, ToolSpec


@runtime_checkable
class LLMProvider(Protocol):
    """A chat model behind a vendor-neutral interface.

    Arguments every adapter accepts:

    - ``system`` — the system prompt (kept separate from ``messages``; see ``types.py``).
    - ``tools`` — optional tools; tool calls come back on ``LLMResponse.tool_calls``.
    - ``max_tokens`` — output cap; ``None`` means the adapter's configured default.
    - ``temperature`` — a *hint*. Providers that no longer accept sampling parameters (the
      Claude API since version 2026-10-21, OpenAI reasoning models) ignore it, so never rely
      on it for correctness — make prompts and parsers robust instead.
    - ``effort`` — ``"low" | "medium" | "high"``; how hard a reasoning model should think.
      Use ``"low"`` for cheap helper calls (rewrites, classification). Ignored where unsupported.
    """

    name: str
    model: str

    async def complete(
        self,
        messages: List[Message],
        *,
        system: Optional[str] = None,
        tools: Optional[Sequence[ToolSpec]] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        effort: Optional[str] = None,
    ) -> LLMResponse: ...

    def stream(
        self,
        messages: List[Message],
        *,
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        effort: Optional[str] = None,
    ) -> AsyncIterator[StreamEvent]: ...
