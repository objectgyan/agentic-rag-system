"""Tool registry and implementations for agentic AI."""

from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.rag.retriever import HybridRetriever
from app.core.llm_clients import openai_client


class ToolRegistry:
    """Manages available tools for agent execution."""

    def __init__(self, db: AsyncSession, tenant_id: str, retriever: HybridRetriever):
        self.db = db
        self.tenant_id = tenant_id
        self.retriever = retriever

    def get_tools(self, tool_names: Optional[List[str]] = None, collection_ids: Optional[List[str]] = None) -> List[dict]:
        """Get available tool descriptions."""
        all_tools = [
            {
                "name": "retrieval",
                "description": "Search through document collections. Input: {\"query\": \"search terms\", \"top_k\": 5}",
            },
            {
                "name": "calculator",
                "description": "Evaluate mathematical expressions. Input: {\"expression\": \"2 + 2\"}",
            },
            {
                "name": "web_search",
                "description": "Search the web for current information. Input: {\"query\": \"search terms\"}",
            },
            {
                "name": "summarize",
                "description": "Summarize a long text. Input: {\"text\": \"long text here\", \"max_length\": 200}",
            },
            {
                "name": "compare",
                "description": "Compare two pieces of text or data. Input: {\"text_a\": \"...\", \"text_b\": \"...\"}",
            },
        ]

        if tool_names:
            return [t for t in all_tools if t["name"] in tool_names]
        return all_tools

    async def execute_tool(self, tool_name: str, params: dict) -> str:
        """Execute a tool and return the result as a string."""
        handlers = {
            "retrieval": self._tool_retrieval,
            "calculator": self._tool_calculator,
            "web_search": self._tool_web_search,
            "summarize": self._tool_summarize,
            "compare": self._tool_compare,
        }

        handler = handlers.get(tool_name)
        if not handler:
            return f"Unknown tool: {tool_name}"

        try:
            return await handler(params)
        except Exception as e:
            return f"Tool error: {str(e)}"

    async def _tool_retrieval(self, params: dict) -> str:
        """Search documents using RAG retrieval."""
        query = params.get("query", params.get("input", ""))
        top_k = params.get("top_k", 5)
        collection_ids = params.get("collection_ids")

        chunks = await self.retriever.retrieve(
            query=query, collection_ids=collection_ids, top_k=top_k
        )

        if not chunks:
            return "No relevant documents found."

        results = []
        for i, chunk in enumerate(chunks, 1):
            source = f"[Source {i}]"
            if chunk.section_title:
                source += f" {chunk.section_title}"
            if chunk.page_number:
                source += f" (p.{chunk.page_number})"
            results.append(f"{source}: {chunk.content[:500]}")

        return "\n\n".join(results)

    async def _tool_calculator(self, params: dict) -> str:
        """Safely evaluate mathematical expressions."""
        expression = params.get("expression", "")
        try:
            # Safe eval using restricted builtins
            allowed = {"__builtins__": {}}
            import math
            allowed.update({k: getattr(math, k) for k in dir(math) if not k.startswith("_")})
            result = eval(expression, allowed)
            return str(result)
        except Exception as e:
            return f"Calculation error: {str(e)}"

    async def _tool_web_search(self, params: dict) -> str:
        """Placeholder for web search integration."""
        query = params.get("query", "")
        return f"Web search for '{query}' — integration pending. Use retrieval tool for document-based search."

    async def _tool_summarize(self, params: dict) -> str:
        """Summarize text using LLM."""
        text = params.get("text", "")
        max_length = params.get("max_length", 200)

        client = openai_client()

        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": f"Summarize the following text in about {max_length} words."},
                {"role": "user", "content": text[:5000]},
            ],
            temperature=0.3,
            max_tokens=500,
        )
        return response.choices[0].message.content

    async def _tool_compare(self, params: dict) -> str:
        """Compare two texts using LLM."""
        text_a = params.get("text_a", "")
        text_b = params.get("text_b", "")

        client = openai_client()

        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Compare the two texts. Highlight similarities, differences, and key insights."},
                {"role": "user", "content": f"Text A:\n{text_a[:2000]}\n\nText B:\n{text_b[:2000]}"},
            ],
            temperature=0.3,
            max_tokens=500,
        )
        return response.choices[0].message.content
