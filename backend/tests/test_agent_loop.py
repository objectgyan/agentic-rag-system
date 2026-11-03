"""The native tool-calling agent loop, driven by a scripted FakeLLM."""

import pytest

from app.llm import FakeLLM, LLMResponse, ToolCall, Usage, use_llm
from app.services.agents.orchestrator import AgentOrchestrator
from app.services.rag.retriever import RetrievedChunk


class _Retriever:
    def __init__(self):
        self.calls = []

    async def retrieve(self, query, collection_ids=None, top_k=5, use_reranking=True):
        self.calls.append({"query": query, "collection_ids": collection_ids, "top_k": top_k})
        return [RetrievedChunk(chunk_id="c1", document_id="d1", content="Paris is the capital.", score=0.9)]


def _agent():
    agent = AgentOrchestrator(db=None, tenant_id="t", user_id="u")
    agent.tool_registry.retriever = _Retriever()
    return agent


def _tool_turn(*calls, text=""):
    return LLMResponse(text=text, model="fake", tool_calls=list(calls), stop_reason="tool_use", usage=Usage(100, 20))


@pytest.mark.asyncio
async def test_agent_calls_tools_then_answers():
    fake = FakeLLM([
        _tool_turn(
            ToolCall("a", "retrieval", {"query": "capital of France"}),
            ToolCall("b", "calculator", {"expression": "6 * 7"}),
            text="Let me check.",
        ),
        LLMResponse(text="Paris [Source 1]; 6*7 = 42.", model="fake", usage=Usage(150, 30)),
    ])
    agent = _agent()
    with use_llm(fake):
        out = await agent.execute("What is the capital of France, and 6*7?")

    assert out["result"] == "Paris [Source 1]; 6*7 = 42."
    assert [s["action"] for s in out["steps"]] == ["retrieval", "calculator", None]
    assert out["steps"][0]["thought"] == "Let me check."
    assert "Paris is the capital" in out["steps"][0]["observation"]
    assert out["steps"][1]["observation"] == "42"
    assert (out["total_tokens"], out["prompt_tokens"], out["completion_tokens"]) == (300, 250, 50)

    # Tools were offered natively (with schemas), and both results went back in the second call.
    first, second = fake.calls
    assert {t.name for t in first["tools"]} >= {"retrieval", "calculator"}
    assert [m["role"] for m in second["messages"]] == ["user", "assistant", "tool", "tool"]


@pytest.mark.asyncio
async def test_retrieval_is_scoped_to_the_authorized_collections():
    # The model tries to widen the search scope; the tool must ignore it.
    fake = FakeLLM([
        _tool_turn(ToolCall("a", "retrieval", {"query": "secrets", "collection_ids": ["someone-elses"]})),
        "done",
    ])
    agent = _agent()
    with use_llm(fake):
        await agent.execute("find secrets", collection_ids=["mine"])
    assert agent.tool_registry.retriever.calls[0]["collection_ids"] == ["mine"]


@pytest.mark.asyncio
async def test_max_steps_bounds_the_loop():
    fake = FakeLLM(_tool_turn(ToolCall("a", "calculator", {"expression": "1+1"}), text="again"))
    with use_llm(fake):
        out = await _agent().execute("loop forever", max_steps=3)
    assert len(fake.calls) == 3
    assert out["result"].startswith("Max steps reached.")


@pytest.mark.asyncio
async def test_stream_and_execute_share_one_loop():
    replies = [_tool_turn(ToolCall("a", "calculator", {"expression": "2+3"})), "five"]
    with use_llm(FakeLLM(replies)):
        events = [e async for e in _agent().execute_stream("2+3?")]
    assert [e["type"] for e in events] == [
        "step_start", "thought", "action", "observation", "step_start", "final_answer",
    ]
    assert events[3]["content"] == "5" and events[-1]["content"] == "five"


@pytest.mark.asyncio
async def test_unknown_tool_is_reported_to_the_model_not_raised():
    fake = FakeLLM([_tool_turn(ToolCall("a", "rm_rf", {})), "ok"])
    with use_llm(fake):
        out = await _agent().execute("x")
    assert out["steps"][0]["observation"] == "Unknown tool: rm_rf"
