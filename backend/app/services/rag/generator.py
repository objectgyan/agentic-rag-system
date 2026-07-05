"""LLM generation service with streaming support."""

from typing import Any, AsyncIterator, Dict, List, Optional

from app.core.config import settings
from app.core.llm_clients import anthropic_client, openai_client
from app.services.rag.retriever import RetrievedChunk


class GenerationService:
    """Generate answers from retrieved context using LLMs."""

    def __init__(self, model: Optional[str] = None, temperature: float = 0.1):
        self.model = model or settings.default_llm_model
        self.temperature = temperature

    def _build_context(self, chunks: List[RetrievedChunk]) -> str:
        """Format retrieved chunks into context string."""
        context_parts = []
        for i, chunk in enumerate(chunks, 1):
            source_info = f"[Source {i}]"
            if chunk.section_title:
                source_info += f" Section: {chunk.section_title}"
            if chunk.page_number:
                source_info += f" Page: {chunk.page_number}"
            context_parts.append(f"{source_info}\n{chunk.content}")
        return "\n\n---\n\n".join(context_parts)

    def _build_system_prompt(self) -> str:
        return (
            "You are a helpful assistant that answers questions based on the provided context. "
            "Always cite your sources using [Source N] notation. If the context doesn't contain "
            "enough information to fully answer the question, say so clearly. "
            "Be concise, accurate, and helpful."
        )

    async def generate(
        self,
        query: str,
        chunks: List[RetrievedChunk],
        conversation_history: Optional[List[dict]] = None,
        graph_facts: Optional[List[str]] = None,
        conversation_summary: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate an answer from context."""
        context = self._build_context(chunks)
        if graph_facts:
            kg = "\n".join(f"- {f}" for f in graph_facts)
            context = f"[Knowledge Graph Facts]\n{kg}\n\n---\n\n{context}"

        system = self._build_system_prompt()
        # A running summary of older conversation turns (C-phase item 2), when provided.
        if conversation_summary:
            system += (
                "\n\nSummary of earlier conversation (for context; the most recent turns follow "
                f"verbatim):\n{conversation_summary}"
            )
        messages = [{"role": "system", "content": system}]

        if conversation_history:
            # Bounded by ConversationMemory upstream; the slice is a safety cap for callers
            # that pass raw history.
            messages.extend(conversation_history[-settings.conversation_recent_messages:])

        messages.append({
            "role": "user",
            "content": f"Context:\n{context}\n\n---\n\nQuestion: {query}",
        })

        if self.model.startswith("claude"):
            return await self._generate_anthropic(messages)
        else:
            return await self._generate_openai(messages)

    async def generate_stream(
        self,
        query: str,
        chunks: List[RetrievedChunk],
        conversation_history: Optional[List[dict]] = None,
        conversation_summary: Optional[str] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Stream-generate an answer."""
        context = self._build_context(chunks)

        system = self._build_system_prompt()
        if conversation_summary:
            system += (
                "\n\nSummary of earlier conversation (for context; the most recent turns follow "
                f"verbatim):\n{conversation_summary}"
            )
        messages = [{"role": "system", "content": system}]

        if conversation_history:
            messages.extend(conversation_history[-settings.conversation_recent_messages:])

        messages.append({
            "role": "user",
            "content": f"Context:\n{context}\n\n---\n\nQuestion: {query}",
        })

        # Yield citations first
        yield {
            "type": "citations",
            "citations": [
                {
                    "chunk_id": c.chunk_id,
                    "document_id": c.document_id,
                    "content": c.content[:200],
                    "score": c.score,
                    "page_number": c.page_number,
                }
                for c in chunks
            ],
        }

        # Stream the answer
        if self.model.startswith("claude"):
            async for chunk in self._stream_anthropic(messages):
                yield chunk
        else:
            async for chunk in self._stream_openai(messages):
                yield chunk

    async def _generate_openai(self, messages: List[dict]) -> Dict[str, Any]:
        client = openai_client()

        response = await client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            max_tokens=2000,
        )

        usage = response.usage
        return {
            "answer": response.choices[0].message.content,
            "model_used": self.model,
            "tokens_used": usage.total_tokens if usage else None,
            "prompt_tokens": usage.prompt_tokens if usage else None,
            "completion_tokens": usage.completion_tokens if usage else None,
        }

    async def _generate_anthropic(self, messages: List[dict]) -> Dict[str, Any]:
        client = anthropic_client()

        system_msg = messages[0]["content"] if messages[0]["role"] == "system" else ""
        chat_msgs = [m for m in messages if m["role"] != "system"]

        response = await client.messages.create(
            model=self.model,
            system=system_msg,
            messages=chat_msgs,
            temperature=self.temperature,
            max_tokens=2000,
        )

        return {
            "answer": response.content[0].text,
            "model_used": self.model,
            "tokens_used": response.usage.input_tokens + response.usage.output_tokens,
            "prompt_tokens": response.usage.input_tokens,
            "completion_tokens": response.usage.output_tokens,
        }

    async def _stream_openai(self, messages: List[dict]) -> AsyncIterator[Dict[str, Any]]:
        client = openai_client()

        stream = await client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            max_tokens=2000,
            stream=True,
        )

        async for chunk in stream:
            if chunk.choices[0].delta.content:
                yield {"type": "token", "content": chunk.choices[0].delta.content}

        yield {"type": "done", "model_used": self.model}

    async def _stream_anthropic(self, messages: List[dict]) -> AsyncIterator[Dict[str, Any]]:
        client = anthropic_client()

        system_msg = messages[0]["content"] if messages[0]["role"] == "system" else ""
        chat_msgs = [m for m in messages if m["role"] != "system"]

        async with client.messages.stream(
            model=self.model,
            system=system_msg,
            messages=chat_msgs,
            temperature=self.temperature,
            max_tokens=2000,
        ) as stream:
            async for text in stream.text_stream:
                yield {"type": "token", "content": text}

        yield {"type": "done", "model_used": self.model}
