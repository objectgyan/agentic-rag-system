"""Provider-neutral types for the LLM building block.

Every adapter (Anthropic, OpenAI, any OpenAI-compatible server, the test fake) speaks these
types at its boundary, so the rest of the app never imports a vendor SDK. This is the
*ports & adapters* (hexagonal) pattern: the port is the shape below + ``LLMProvider``; each
vendor is an adapter that translates to and from it.

Conversation messages are plain dicts so they serialize cleanly (DB rows, JSON, logs):

    {"role": "user", "content": "..."}
    {"role": "assistant", "content": "...", "tool_calls": [ToolCall, ...]}   # tool_calls optional
    {"role": "tool", "tool_call_id": "...", "content": "...", "is_error": False}

The system prompt is NOT a message — it is passed separately (``system=``), because providers
disagree on where it goes (Anthropic: a top-level field; OpenAI: the first message).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

Message = Dict[str, Any]

StopReason = Literal["end_turn", "tool_use", "max_tokens", "refusal", "other"]


@dataclass(frozen=True)
class ToolSpec:
    """A tool the model may call. ``input_schema`` is a JSON Schema object."""

    name: str
    description: str
    input_schema: Dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    """One tool invocation requested by the model. ``arguments`` is already JSON-decoded."""

    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    # Prompt-cache reads (Anthropic) / cached prompt tokens (OpenAI). Already included in
    # input_tokens; broken out because cached tokens bill at a fraction of the price.
    cached_input_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class LLMResponse:
    text: str
    model: str
    usage: Usage = field(default_factory=Usage)
    tool_calls: List[ToolCall] = field(default_factory=list)
    stop_reason: StopReason = "end_turn"
    # The provider's own representation of this assistant turn (e.g. Anthropic content blocks
    # including thinking blocks). Adapters replay it verbatim on the next request, because some
    # providers require earlier turns to be sent back unchanged. Opaque to everyone else.
    native: Any = None
    provider: Optional[str] = None

    def to_message(self) -> Message:
        """This response as an assistant message to append to the conversation."""
        msg: Message = {"role": "assistant", "content": self.text}
        if self.tool_calls:
            msg["tool_calls"] = list(self.tool_calls)
        if self.native is not None:
            msg["_native"] = (self.provider, self.native)
        return msg


def tool_result_message(call: ToolCall, content: str, is_error: bool = False) -> Message:
    return {"role": "tool", "tool_call_id": call.id, "content": content, "is_error": is_error}


@dataclass
class StreamEvent:
    """``type="text"`` carries a ``text`` delta; the final ``type="done"`` carries ``response``."""

    type: Literal["text", "done"]
    text: str = ""
    response: Optional[LLMResponse] = None
