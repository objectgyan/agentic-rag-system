"""Tool registry and implementations for agentic AI."""

from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.llm import ToolSpec, get_fast_llm
from app.services.agents.safe_math import SafeMathError, evaluate_expression
from app.services.rag.retriever import HybridRetriever


class ToolRegistry:
    """Manages available tools for agent execution."""

    # Single source of truth for what tools actually exist. Every entry here MUST have a
    # matching handler in execute_tool(); /agents/types derives its advertised tools from
    # this so the API can't claim a capability that isn't wired up (C2).
    TOOLS: List[dict] = [
        {
            "name": "retrieval",
            "description": "Search the user's document collections and return the most relevant passages, "
            "numbered [Source N]. Use this first for any question about the user's documents.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to search for."},
                    "top_k": {"type": "integer", "description": "How many passages to return (1-20).", "default": 5},
                },
                "required": ["query"],
            },
        },
        {
            "name": "calculator",
            "description": "Evaluate an arithmetic expression exactly (+ - * / // % **, parentheses, and math "
            "functions such as sqrt, log, sin). Use it instead of doing arithmetic in your head.",
            "input_schema": {
                "type": "object",
                "properties": {"expression": {"type": "string", "description": "e.g. \"(17.5 * 3) / sqrt(2)\""}},
                "required": ["expression"],
            },
        },
        {
            "name": "web_search",
            "description": "Search the public web for current information not in the user's documents.",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
        {
            "name": "summarize",
            "description": "Summarize a long text to roughly max_length words.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "max_length": {"type": "integer", "default": 200},
                },
                "required": ["text"],
            },
        },
        {
            "name": "compare",
            "description": "Compare two pieces of text or data: similarities, differences, key insights.",
            "input_schema": {
                "type": "object",
                "properties": {"text_a": {"type": "string"}, "text_b": {"type": "string"}},
                "required": ["text_a", "text_b"],
            },
        },
        {
            "name": "delegate",
            "description": "Hand a focused sub-task to a specialized sub-agent and get its answer back.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "agent_type": {"type": "string", "enum": ["research", "analyst", "summarizer", "code"]},
                    "task": {"type": "string", "description": "The self-contained sub-task."},
                },
                "required": ["task"],
            },
        },
    ]

    # How deep agent->agent delegation may go. The top agent is depth 0; once an agent
    # reaches this depth it no longer sees the delegate tool, bounding recursion/cost (A4).
    MAX_DELEGATION_DEPTH = 1

    def __init__(
        self,
        db: AsyncSession,
        tenant_id: str,
        retriever: HybridRetriever,
        user_id: Optional[str] = None,
        depth: int = 0,
        collection_ids: Optional[List[str]] = None,
    ):
        self.db = db
        self.collection_ids = collection_ids
        self.tenant_id = tenant_id
        self.retriever = retriever
        self.user_id = user_id
        self.depth = depth

    @classmethod
    def _tool_is_enabled(cls, name: str) -> bool:
        """Whether a tool is actually usable given current config (C4).

        web_search needs a provider key; if absent we don't offer it (and the handler
        reports itself unconfigured) rather than advertising a capability that fakes
        results. All other tools are always available.
        """
        if name == "web_search":
            return bool(settings.web_search_api_key)
        return True

    @classmethod
    def available_tools(cls) -> List[dict]:
        return [t for t in cls.TOOLS if cls._tool_is_enabled(t["name"])]

    @classmethod
    def available_tool_names(cls) -> List[str]:
        return [t["name"] for t in cls.available_tools()]

    def tool_specs(self, tool_names: Optional[List[str]] = None) -> List[ToolSpec]:
        """The available tools as provider-neutral specs for native tool calling."""
        return [ToolSpec(t["name"], t["description"], t["input_schema"]) for t in self.get_tools(tool_names)]

    def get_tools(self, tool_names: Optional[List[str]] = None, collection_ids: Optional[List[str]] = None) -> List[dict]:
        """Get available (config-enabled, depth-aware) tool descriptions."""
        tools = self.available_tools()
        # Stop offering delegation once we hit the recursion cap (A4).
        if self.depth >= self.MAX_DELEGATION_DEPTH:
            tools = [t for t in tools if t["name"] != "delegate"]
        if tool_names:
            return [t for t in tools if t["name"] in tool_names]
        return tools

    async def execute_tool(self, tool_name: str, params: dict) -> str:
        """Execute a tool and return the result as a string."""
        handlers = {
            "retrieval": self._tool_retrieval,
            "calculator": self._tool_calculator,
            "web_search": self._tool_web_search,
            "summarize": self._tool_summarize,
            "compare": self._tool_compare,
            "delegate": self._tool_delegate,
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
        top_k = max(1, min(int(params.get("top_k", 5) or 5), 20))
        # Scope comes from the authorized request, never from the model's arguments — otherwise
        # injected text could point the agent at collections the user may not access.
        chunks = await self.retriever.retrieve(
            query=query, collection_ids=self.collection_ids, top_k=top_k
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
        """Evaluate arithmetic via an AST whitelist — never ``eval``.

        Agent inputs are model-generated, and the model reads retrieved documents, so a poisoned
        document can steer what reaches this tool (prompt injection). ``eval`` with empty
        builtins is escapable (``().__class__.__mro__...``); the AST evaluator cannot run code.
        """
        try:
            return str(evaluate_expression(str(params.get("expression", ""))))
        except SafeMathError as e:
            return f"Calculation error: {e}"
    async def _tool_web_search(self, params: dict) -> str:
        """Web search via Tavily when configured; honest about it when not (C4)."""
        query = params.get("query", "")
        if not settings.web_search_api_key:
            return (
                "Web search is not configured on this server (no WEB_SEARCH_API_KEY). "
                "Use the retrieval tool for document-based search."
            )

        import httpx
        try:
            async with httpx.AsyncClient(timeout=settings.external_api_timeout_seconds) as client:
                resp = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": settings.web_search_api_key,
                        "query": query,
                        "max_results": 5,
                        "search_depth": "basic",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            return f"Web search failed: {e}"

        results = data.get("results", [])
        if not results:
            return f"No web results found for '{query}'."
        lines = [
            f"- {r.get('title', 'Untitled')}: {r.get('content', '')[:200]} ({r.get('url', '')})"
            for r in results
        ]
        return f"Web results for '{query}':\n" + "\n".join(lines)

    async def _tool_summarize(self, params: dict) -> str:
        """Summarize text using the helper model."""
        text = params.get("text", "")
        max_length = params.get("max_length", 200)
        resp = await get_fast_llm().complete(
            [{"role": "user", "content": text[:5000]}],
            system=f"Summarize the following text in about {max_length} words.",
            temperature=0.3,
            max_tokens=500,
            effort="low",
        )
        return resp.text

    async def _tool_compare(self, params: dict) -> str:
        """Compare two texts using the helper model."""
        text_a = params.get("text_a", "")
        text_b = params.get("text_b", "")
        resp = await get_fast_llm().complete(
            [{"role": "user", "content": f"Text A:\n{text_a[:2000]}\n\nText B:\n{text_b[:2000]}"}],
            system="Compare the two texts. Highlight similarities, differences, and key insights.",
            temperature=0.3,
            max_tokens=500,
            effort="low",
        )
        return resp.text

    async def _tool_delegate(self, params: dict) -> str:
        """Run a focused sub-task on a fresh sub-agent and return its answer (A4).

        The sub-agent is created at depth+1, so it cannot delegate again once the cap is
        reached — bounding recursion and runaway cost. Imported lazily to avoid the
        orchestrator <-> tools import cycle.
        """
        if self.depth >= self.MAX_DELEGATION_DEPTH:
            return "Delegation depth limit reached; handle this sub-task directly."

        task = params.get("task") or params.get("input")
        if not task:
            return "Delegate error: provide a 'task' to hand off."
        agent_type = params.get("agent_type", "research")

        from app.services.agents.orchestrator import AgentOrchestrator

        sub_agent = AgentOrchestrator(
            db=self.db, tenant_id=self.tenant_id, user_id=self.user_id, depth=self.depth + 1
        )
        # The sub-agent inherits the parent's authorized collection scope; without it, its
        # retrieval would search the whole tenant.
        result = await sub_agent.execute(
            task=task, agent_type=agent_type, max_steps=5, collection_ids=self.collection_ids
        )
        return result.get("result", "")
