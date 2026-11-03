"""Answer generation: turn retrieved chunks into a grounded, cited answer.

Provider-agnostic — the model call goes through the LLM port (``app/llm``), so the same code
runs on Claude, OpenAI, Azure, or a self-hosted model. Prompt construction is shared by the
blocking and streaming paths so they can never drift apart.
"""

from typing import Any, AsyncIterator, Dict, List, Optional

from app.core.config import settings
from app.llm import LLMResponse, get_llm
from app.services.rag.retriever import RetrievedChunk

# Shown when a provider's safety system declines and no fallback rescued the request.
REFUSAL_MESSAGE = "I'm not able to help with that request."

SYSTEM_PROMPT = (
    "You are a helpful assistant that answers questions based on the provided context. "
    "Always cite your sources using [Source N] notation. If the context doesn't contain "
    "enough information to fully answer the question, say so clearly. "
    "Be concise, accurate, and helpful."
)


class GenerationService:
    """Generate answers from retrieved context using any configured LLM."""

    def __init__(self, model: Optional[str] = None, temperature: float = 0.1):
        self.model = model or settings.default_llm_model
        self.temperature = temperature

    def _build_context(self, chunks: List[RetrievedChunk]) -> str:
        """Format retrieved chunks into a numbered context block the model can cite."""
        context_parts = []
        for i, chunk in enumerate(chunks, 1):
            source_info = f"[Source {i}]"
            if chunk.section_title:
                source_info += f" Section: {chunk.section_title}"
            if chunk.page_number:
                source_info += f" Page: {chunk.page_number}"
            context_parts.append(f"{source_info}\n{chunk.content}")
        return "\n\n---\n\n".join(context_parts)

    def _build_system_prompt(self, conversation_summary: Optional[str] = None) -> str:
        system = SYSTEM_PROMPT
        # A running summary of older conversation turns (C-phase item 2), when provided.
        if conversation_summary:
            system += (
                "\n\nSummary of earlier conversation (for context; the most recent turns follow "
                f"verbatim):\n{conversation_summary}"
            )
        return system

    def _build_messages(
        self,
        query: str,
        chunks: List[RetrievedChunk],
        conversation_history: Optional[List[dict]],
        graph_facts: Optional[List[str]],
    ) -> List[dict]:
        context = self._build_context(chunks)
        if graph_facts:
            kg = "\n".join(f"- {f}" for f in graph_facts)
            context = f"[Knowledge Graph Facts]\n{kg}\n\n---\n\n{context}"

        messages: List[dict] = []
        if conversation_history:
            # Bounded by ConversationMemory upstream; the slice is a safety cap for callers
            # that pass raw history.
            messages.extend(
                {"role": m["role"], "content": m["content"]}
                for m in conversation_history[-settings.conversation_recent_messages:]
            )
        messages.append({"role": "user", "content": f"Context:\n{context}\n\n---\n\nQuestion: {query}"})
        return messages

    def _result(self, resp: LLMResponse) -> Dict[str, Any]:
        answer = resp.text
        if resp.stop_reason == "refusal" and not answer.strip():
            answer = REFUSAL_MESSAGE
        return {
            "answer": answer,
            "model_used": self.model,
            "tokens_used": resp.usage.total_tokens,
            "prompt_tokens": resp.usage.input_tokens,
            "completion_tokens": resp.usage.output_tokens,
            "cached_tokens": resp.usage.cached_input_tokens,
            "stop_reason": resp.stop_reason,
        }

    async def generate(
        self,
        query: str,
        chunks: List[RetrievedChunk],
        conversation_history: Optional[List[dict]] = None,
        graph_facts: Optional[List[str]] = None,
        conversation_summary: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate an answer from context."""
        resp = await get_llm(self.model).complete(
            self._build_messages(query, chunks, conversation_history, graph_facts),
            system=self._build_system_prompt(conversation_summary),
            temperature=self.temperature,
        )
        return self._result(resp)

    async def generate_stream(
        self,
        query: str,
        chunks: List[RetrievedChunk],
        conversation_history: Optional[List[dict]] = None,
        conversation_summary: Optional[str] = None,
        graph_facts: Optional[List[str]] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Stream an answer: ``{"type": "token"}`` events, then one ``{"type": "done"}`` with usage."""
        stream = get_llm(self.model).stream(
            self._build_messages(query, chunks, conversation_history, graph_facts),
            system=self._build_system_prompt(conversation_summary),
            temperature=self.temperature,
        )
        async for event in stream:
            if event.type == "text":
                yield {"type": "token", "content": event.text}
            elif event.response is not None:
                result = self._result(event.response)
                if result["answer"] == REFUSAL_MESSAGE and not event.response.text:
                    yield {"type": "token", "content": REFUSAL_MESSAGE}
                yield {"type": "done", **result}
