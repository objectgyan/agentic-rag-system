"""Agent orchestrator implementing ReAct-style reasoning with tool use."""

import json
from typing import Any, AsyncIterator, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.llm_clients import anthropic_client, openai_client
from app.services.agents.tools import ToolRegistry
from app.services.rag.retriever import HybridRetriever

AGENT_SYSTEM_PROMPTS = {
    "research": (
        "You are a research agent. Your goal is to gather comprehensive information "
        "to answer the user's question. Use the retrieval tool to search through documents, "
        "and synthesize findings from multiple sources. Always cite your sources."
    ),
    "analyst": (
        "You are an analyst agent. Analyze data, identify patterns, compare information, "
        "and provide insights. Use tools to retrieve data and perform calculations."
    ),
    "summarizer": (
        "You are a summarizer agent. Your goal is to create clear, comprehensive summaries "
        "of document collections. Retrieve relevant sections and distill key points."
    ),
    "code": (
        "You are a code agent. Help users understand, debug, and generate code. "
        "Search through code repositories and documentation to provide accurate answers."
    ),
}

REACT_PROMPT = """You have access to the following tools:

{tools}

Use the following format:

Thought: Think about what to do next
Action: tool_name
Action Input: {{"param": "value"}}
Observation: (result from tool)
... (repeat Thought/Action/Observation as needed)
Thought: I now have enough information to answer
Final Answer: (your complete answer)

Begin!

Task: {task}"""


class AgentOrchestrator:
    """ReAct-style agent with tool use and multi-step reasoning."""

    def __init__(self, db: AsyncSession, tenant_id: str, user_id: str):
        self.db = db
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.retriever = HybridRetriever(db=db, tenant_id=tenant_id)
        self.tool_registry = ToolRegistry(db=db, tenant_id=tenant_id, retriever=self.retriever)

    async def execute(
        self,
        task: str,
        agent_type: str = "research",
        collection_ids: Optional[List[str]] = None,
        model: Optional[str] = None,
        max_steps: int = 10,
        tools: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Execute agent task with full step trace."""
        model = model or settings.default_llm_model
        system_prompt = AGENT_SYSTEM_PROMPTS.get(agent_type, AGENT_SYSTEM_PROMPTS["research"])

        available_tools = self.tool_registry.get_tools(tools, collection_ids)
        tools_desc = "\n".join(f"- {t['name']}: {t['description']}" for t in available_tools)

        prompt = REACT_PROMPT.format(tools=tools_desc, task=task)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        steps = []
        total_tokens = 0

        for step_num in range(max_steps):
            response = await self._call_llm(messages, model)
            total_tokens += response.get("tokens", 0)
            text = response["content"]
            messages.append({"role": "assistant", "content": text})

            # Parse the response
            step = self._parse_step(text, step_num + 1)
            steps.append(step)

            # Check for final answer
            if "Final Answer:" in text:
                final = text.split("Final Answer:")[-1].strip()
                return {
                    "result": final,
                    "steps": steps,
                    "model_used": model,
                    "total_tokens": total_tokens,
                }

            # Execute tool if action specified
            if step.get("action") and step.get("action_input"):
                observation = await self.tool_registry.execute_tool(
                    step["action"], step["action_input"]
                )
                step["observation"] = observation
                messages.append({"role": "user", "content": f"Observation: {observation}"})

        return {
            "result": "Max steps reached. " + (steps[-1].get("thought", "") if steps else ""),
            "steps": steps,
            "model_used": model,
            "total_tokens": total_tokens,
        }

    async def execute_stream(
        self,
        task: str,
        agent_type: str = "research",
        collection_ids: Optional[List[str]] = None,
        model: Optional[str] = None,
        max_steps: int = 10,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Stream agent execution step by step."""
        model = model or settings.default_llm_model
        system_prompt = AGENT_SYSTEM_PROMPTS.get(agent_type, AGENT_SYSTEM_PROMPTS["research"])

        available_tools = self.tool_registry.get_tools(None, collection_ids)
        tools_desc = "\n".join(f"- {t['name']}: {t['description']}" for t in available_tools)

        prompt = REACT_PROMPT.format(tools=tools_desc, task=task)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        for step_num in range(max_steps):
            yield {"type": "step_start", "step": step_num + 1}

            response = await self._call_llm(messages, model)
            text = response["content"]
            messages.append({"role": "assistant", "content": text})

            step = self._parse_step(text, step_num + 1)
            yield {"type": "thought", "content": step.get("thought", "")}

            if "Final Answer:" in text:
                final = text.split("Final Answer:")[-1].strip()
                yield {"type": "final_answer", "content": final}
                return

            if step.get("action"):
                yield {"type": "action", "tool": step["action"], "input": step.get("action_input")}
                observation = await self.tool_registry.execute_tool(
                    step["action"], step["action_input"] or {}
                )
                yield {"type": "observation", "content": observation}
                messages.append({"role": "user", "content": f"Observation: {observation}"})

        yield {"type": "max_steps_reached"}

    def _parse_step(self, text: str, step_num: int) -> dict:
        """Parse a ReAct step from LLM output."""
        step = {"step_number": step_num, "thought": "", "action": None, "action_input": None, "observation": None}

        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("Thought:"):
                step["thought"] = line[8:].strip()
            elif line.startswith("Action:"):
                step["action"] = line[7:].strip()
            elif line.startswith("Action Input:"):
                raw = line[13:].strip()
                try:
                    step["action_input"] = json.loads(raw)
                except json.JSONDecodeError:
                    step["action_input"] = {"input": raw}

        return step

    async def _call_llm(self, messages: List[dict], model: str) -> dict:
        """Call LLM and return content + token count."""
        if model.startswith("claude"):
            client = anthropic_client()

            system = messages[0]["content"] if messages[0]["role"] == "system" else ""
            chat_msgs = [m for m in messages if m["role"] != "system"]

            response = await client.messages.create(
                model=model, system=system, messages=chat_msgs,
                temperature=0.1, max_tokens=2000,
            )
            return {
                "content": response.content[0].text,
                "tokens": response.usage.input_tokens + response.usage.output_tokens,
            }
        else:
            client = openai_client()

            response = await client.chat.completions.create(
                model=model, messages=messages, temperature=0.1, max_tokens=2000,
            )
            return {
                "content": response.choices[0].message.content,
                "tokens": response.usage.total_tokens if response.usage else 0,
            }
