"""The LLM building block: one port, many providers. See docs/learn/blocks/01-llm-provider.md."""

from app.llm.base import LLMProvider
from app.llm.fake import FakeLLM
from app.llm.registry import (
    get_fast_llm,
    get_llm,
    model_allowed,
    register_provider,
    resolve,
    use_llm,
)
from app.llm.types import (
    LLMResponse,
    Message,
    StreamEvent,
    ToolCall,
    ToolSpec,
    Usage,
    tool_result_message,
)

__all__ = [
    "FakeLLM",
    "LLMProvider",
    "LLMResponse",
    "Message",
    "StreamEvent",
    "ToolCall",
    "ToolSpec",
    "Usage",
    "get_fast_llm",
    "get_llm",
    "model_allowed",
    "register_provider",
    "resolve",
    "tool_result_message",
    "use_llm",
]
