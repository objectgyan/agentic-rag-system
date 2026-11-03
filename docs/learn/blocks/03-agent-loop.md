# Block 3 — The agent loop (native tool calling)

**Files:** `backend/app/services/agents/orchestrator.py`, `tools.py`, `safe_math.py` ·
**Tests:** `tests/test_agent_loop.py`, `tests/test_safe_math.py`, `tests/test_delegation.py`

## The loop

Every modern agent, from a support bot to a coding agent, runs the same loop:

```
messages = [user task]
repeat up to max_steps:
    reply = llm.complete(messages, system=..., tools=tool_specs)
    if reply has no tool calls:  return reply.text            # final answer
    messages += [reply as assistant turn]
    for call in reply.tool_calls:
        messages += [tool result for call]                    # all results go back together
```

`AgentOrchestrator.run()` is exactly this, yielding events (`step_start`, `thought`, `action`,
`observation`, `final_answer` / `max_steps_reached`). `execute()` collects the events into the step
trace the API returns; `execute_stream()` forwards them as SSE. One loop, two views.

## Why native tool calling replaced ReAct text parsing

The previous orchestrator asked the model to write `Thought:` / `Action:` / `Action Input: {json}`
lines, then scraped them with string matching. That was the 2022 technique, from before providers
supported tools natively. Problems: the model drifts out of the format; JSON in free text breaks;
one action per turn; no validation.

With native tool calling the model receives each tool's **JSON Schema** and returns structured
`tool_use` / `tool_calls` blocks. The provider was trained on this format — it is more reliable,
supports several calls per turn, and arguments arrive as parsed JSON.

## Tools are a schema plus a handler

```python
{
    "name": "retrieval",
    "description": "Search the user's document collections ... Use this first for any question about the user's documents.",
    "input_schema": {"type": "object", "properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}}, "required": ["query"]},
}
```

The **description is a prompt**: it tells the model *when* to use the tool, not just what it does.
Adding a client system (CRM lookup, ticket creation, SQL over their warehouse) is one entry in
`ToolRegistry.TOOLS` plus one `_tool_<name>` handler.

## Security: tools are an attack surface

An agent reads retrieved documents, and documents can contain instructions ("ignore previous
instructions and…"). This is **indirect prompt injection**. Anything the model can pass to a
tool, an attacker who controls a document can try to pass too. Three defenses ship in this block:

1. **No `eval`.** The calculator used `eval()` with empty builtins — a well-known escapable
   sandbox (`().__class__.__mro__…` reaches arbitrary code). It now uses `safe_math.py`: parse to
   an AST, interpret only whitelisted nodes, cap exponents/result size (`9**9**9` is rejected).
2. **Scope comes from the request, not the model.** The retrieval tool used to accept
   `collection_ids` from the model's arguments — so injected text could steer it into collections
   the user may not access (RLS still enforced the *tenant*, but not per-collection access). The
   scope is now fixed to the request's authorized collections.
3. **Tool output is data.** The system prompt tells the model to ignore instructions inside tool
   results, and observations are capped at 8,000 chars so one result can't flood the context.

Phase 2 adds screening of retrieved text itself. The general rule for client tools: **validate
every argument server-side as if a stranger typed it**, because effectively one might have.

## Concurrency note

The model can request several tools in one turn. They run **sequentially** here because they
share the request's SQLAlchemy `AsyncSession`, which must not be used by concurrent coroutines.
Tools that don't touch the session (HTTP APIs) could run with `asyncio.gather`; results still go
back to the model in one turn either way.

## Alternatives on the market

| Option | When to prefer it |
|---|---|
| **Anthropic tool runner** (`client.beta.messages.tool_runner`) | Claude-only agents where you want the SDK to own the loop. |
| **OpenAI Agents SDK, LangGraph, CrewAI** | Multi-agent orchestration, handoffs, persistence, human-in-the-loop graphs. |
| **Managed agent platforms** (e.g. Claude Managed Agents) | The vendor hosts the loop and a sandbox; you supply tools and config. |
| **This block** | Provider-neutral, ~150 lines, every step visible and testable with `FakeLLM`. |

## Vocabulary

*agentic loop, tool calling / function calling, JSON Schema, parallel tool calls, max steps,
indirect prompt injection, sandbox escape, least privilege.*
