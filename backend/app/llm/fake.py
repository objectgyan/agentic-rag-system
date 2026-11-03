"""A scripted, offline LLM for tests and local demos — a first-class adapter, not a mock hack.

    fake = FakeLLM(["first reply", "second reply"])
    with use_llm(fake):            # every get_llm() call now returns `fake`
        ...
    assert fake.calls[0]["system"].startswith("You are")

Each reply may be a ``str``, an ``LLMResponse`` (e.g. one carrying ``tool_calls``), an
``Exception`` instance (raised — for testing fail-soft paths), or a callable taking the call
record and returning any of those (for replies that depend on the prompt).
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Sequence, Union

from app.llm.types import LLMResponse, Message, StreamEvent, ToolSpec, Usage

Reply = Union[str, LLMResponse, Exception, Callable[[Dict[str, Any]], Any]]


class FakeLLM:
    name = "fake"

    def __init__(self, replies: Union[Reply, Sequence[Reply]] = "ok", model: str = "fake-model"):
        self.model = model
        if isinstance(replies, (str, LLMResponse, Exception)) or callable(replies):
            replies = [replies]
        self._replies: List[Reply] = list(replies)
        self._i = 0
        self.calls: List[Dict[str, Any]] = []

    def _next(self, record: Dict[str, Any]) -> LLMResponse:
        self.calls.append(record)
        # The last reply repeats once the script runs out, so "always answer X" is one entry.
        reply = self._replies[min(self._i, len(self._replies) - 1)]
        self._i += 1
        if callable(reply) and not isinstance(reply, (LLMResponse, Exception)):
            reply = reply(record)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, LLMResponse):
            return reply
        return LLMResponse(text=str(reply), model=self.model, usage=Usage(10, 5), provider=self.name)

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
        return self._next({
            "messages": list(messages),
            "system": system,
            "tools": list(tools or []),
            "max_tokens": max_tokens,
            "effort": effort,
        })

    async def stream(
        self,
        messages: List[Message],
        *,
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        effort: Optional[str] = None,
    ) -> AsyncIterator[StreamEvent]:
        resp = self._next({"messages": list(messages), "system": system, "stream": True})
        for word in resp.text.split(" "):
            yield StreamEvent(type="text", text=word + " ")
        yield StreamEvent(type="done", response=resp)
