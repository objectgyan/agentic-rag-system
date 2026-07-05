"""Conversation memory management — bound chat history for the generator (competitive-phase item 2).

The old behavior loaded *all* prior turns and the generator kept only the last 10 messages — no
token budget, and everything older was silently dropped. On a long chat that both blows the LLM
context window (cost + latency) and loses early context with no trace.

This distills a full history into two parts the generator can safely consume:

- a **running summary** of the older turns (one cheap LLM call), and
- the most recent messages kept **verbatim**, trimmed to a token budget.

Fail-soft: if summarization errors, we drop the older turns (keep the recent window) rather than
failing the query — the caller records this in its ``degraded`` list.

Vocabulary: *context window, token budget, sliding-window memory, conversation summarization.*
"""

import logging
from typing import Dict, List, Optional, Tuple

from app.core.config import settings
from app.core.llm_clients import openai_client

logger = logging.getLogger(__name__)

_encoding = None

# Rough per-message overhead (role tag + delimiters) the way chat models account for it.
_PER_MESSAGE_OVERHEAD = 4


def _get_encoding():
    global _encoding
    if _encoding is None:
        import tiktoken

        _encoding = tiktoken.get_encoding("cl100k_base")
    return _encoding


def count_tokens(text: str) -> int:
    """Token count using the same cl100k_base tokenizer the rest of the app uses."""
    return len(_get_encoding().encode(text or ""))


class ConversationMemory:
    """Turns a full message history into a bounded (summary, recent-messages) pair."""

    def __init__(
        self,
        *,
        model: Optional[str] = None,
        recent_messages: Optional[int] = None,
        token_budget: Optional[int] = None,
        summary_enabled: Optional[bool] = None,
        summary_model: Optional[str] = None,
    ):
        self.model = model or settings.default_llm_model
        self.recent_messages = (
            recent_messages if recent_messages is not None else settings.conversation_recent_messages
        )
        self.token_budget = (
            token_budget if token_budget is not None else settings.conversation_token_budget
        )
        self.summary_enabled = (
            summary_enabled if summary_enabled is not None else settings.conversation_summary_enabled
        )
        self.summary_model = summary_model or settings.default_compression_model

    def _message_tokens(self, msg: Dict[str, str]) -> int:
        return count_tokens(msg.get("content", "")) + _PER_MESSAGE_OVERHEAD

    def _trim_to_budget(self, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Drop oldest messages until within the token budget; always keep at least the last one."""
        if not messages:
            return []
        out = list(messages)
        total = sum(self._message_tokens(m) for m in out)
        while len(out) > 1 and total > self.token_budget:
            total -= self._message_tokens(out.pop(0))
        return out

    async def prepare(
        self, history: Optional[List[Dict[str, str]]]
    ) -> Tuple[Optional[str], List[Dict[str, str]]]:
        """Return ``(summary_of_older_turns, bounded_recent_messages)``.

        ``summary`` is ``None`` when there are no older turns, or when summarization is disabled
        or failed. ``recent`` is the tail of the history, trimmed to the token budget.
        """
        if not history:
            return None, []

        recent = history[-self.recent_messages :] if self.recent_messages > 0 else []
        older = history[: len(history) - len(recent)]

        recent = self._trim_to_budget(recent)

        summary: Optional[str] = None
        if older and self.summary_enabled:
            try:
                summary = await self._summarize(older)
            except Exception:
                logger.warning("conversation summarization failed; dropping older turns", exc_info=True)
                summary = None
        return summary, recent

    async def _summarize(self, older: List[Dict[str, str]]) -> str:
        transcript = "\n".join(
            f"{m.get('role', 'user')}: {m.get('content', '')}" for m in older
        )
        client = openai_client()
        response = await client.chat.completions.create(
            model=self.summary_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Summarize the following conversation so a downstream assistant retains the "
                        "key facts, entities, decisions, and unresolved questions. Be concise, write "
                        "in the third person, and do not add information that is not present."
                    ),
                },
                {"role": "user", "content": transcript},
            ],
            temperature=0,
            max_tokens=250,
        )
        return response.choices[0].message.content.strip()
