"""Tests for the LLM building block: adapters translate correctly, the registry resolves, fakes work.

Adapters are exercised against tiny fake SDK clients that record the request they receive, so
these tests pin down the *wire shape* each provider gets — no network, no API keys.
"""

from types import SimpleNamespace as NS

import pytest

from app.core.config import settings
from app.llm import FakeLLM, LLMResponse, ToolCall, ToolSpec, get_llm, model_allowed, resolve, use_llm
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.openai_provider import OpenAIProvider
from app.llm.types import tool_result_message

# ---------------------------------------------------------------------------------- fakes


class _Recorder:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _anthropic_msg(*blocks, stop="end_turn", model="claude-haiku-4-5"):
    return NS(
        content=list(blocks),
        stop_reason=stop,
        model=model,
        usage=NS(input_tokens=10, output_tokens=5, cache_read_input_tokens=4, cache_creation_input_tokens=0),
    )


def _anthropic(model="claude-haiku-4-5", response=None, **kw):
    rec = _Recorder(response or _anthropic_msg(NS(type="text", text="hi")))
    client = NS(messages=rec, beta=NS(messages=rec))
    return AnthropicProvider(model, client=client, **kw), rec


def _openai_resp(content="hi", tool_calls=None, finish="stop"):
    msg = NS(content=content, tool_calls=tool_calls)
    usage = NS(prompt_tokens=10, completion_tokens=5, prompt_tokens_details=NS(cached_tokens=2))
    return NS(choices=[NS(message=msg, finish_reason=finish)], usage=usage, model="gpt-4o-mini")


def _openai(model="gpt-4o-mini", response=None, **kw):
    rec = _Recorder(response or _openai_resp())
    return OpenAIProvider(model, client=NS(chat=NS(completions=rec)), **kw), rec


SEARCH = ToolSpec("search", "Search docs", {"type": "object", "properties": {"q": {"type": "string"}}})

# ---------------------------------------------------------------------------------- Anthropic


@pytest.mark.asyncio
async def test_anthropic_system_is_cached_and_sampling_params_are_not_sent():
    llm, rec = _anthropic()
    out = await llm.complete([{"role": "user", "content": "q"}], system="be brief", temperature=0.2)
    req = rec.calls[0]
    assert req["system"] == [{"type": "text", "text": "be brief", "cache_control": {"type": "ephemeral"}}]
    assert "temperature" not in req  # removed from the Claude API
    assert out.text == "hi"
    # cache reads count as input tokens, and are broken out
    assert (out.usage.input_tokens, out.usage.cached_input_tokens, out.usage.output_tokens) == (14, 4, 5)


@pytest.mark.asyncio
async def test_anthropic_text_skips_thinking_blocks():
    # The old code read response.content[0].text, which breaks when block 0 is thinking.
    msg = _anthropic_msg(NS(type="thinking", thinking=""), NS(type="text", text="answer"))
    llm, _ = _anthropic(response=msg)
    assert (await llm.complete([{"role": "user", "content": "q"}])).text == "answer"


@pytest.mark.asyncio
async def test_anthropic_effort_only_where_supported():
    haiku, rec = _anthropic("claude-haiku-4-5")
    await haiku.complete([{"role": "user", "content": "q"}], effort="low")
    assert "output_config" not in rec.calls[0]

    opus, rec = _anthropic("claude-opus-5-5", refusal_fallback=False)
    await opus.complete([{"role": "user", "content": "q"}], effort="low", max_tokens=16)
    assert rec.calls[0]["output_config"] == {"effort": "low"}
    # thinking is always on for this model, so tiny caps are raised to leave room for it
    assert rec.calls[0]["max_tokens"] >= 4096


@pytest.mark.asyncio
async def test_anthropic_refusal_fallback_opt_in():
    opus, rec = _anthropic("claude-opus-5-5")
    await opus.complete([{"role": "user", "content": "q"}])
    assert rec.calls[0]["fallbacks"] == "default"
    assert rec.calls[0]["betas"] == ["server-side-fallback-2026-07-01"]

    haiku, rec = _anthropic("claude-haiku-4-5")
    await haiku.complete([{"role": "user", "content": "q"}])
    assert "fallbacks" not in rec.calls[0]


@pytest.mark.asyncio
async def test_anthropic_refusal_is_reported_not_raised():
    llm, _ = _anthropic(response=_anthropic_msg(stop="refusal"))
    out = await llm.complete([{"role": "user", "content": "q"}])
    assert out.stop_reason == "refusal" and out.text == ""


@pytest.mark.asyncio
async def test_anthropic_tool_round_trip_merges_results_and_replays_native_blocks():
    tool_use = NS(type="tool_use", id="t1", name="search", input={"q": "x"})
    tool_use2 = NS(type="tool_use", id="t2", name="search", input={"q": "y"})
    first = _anthropic_msg(NS(type="text", text="looking"), tool_use, tool_use2, stop="tool_use")
    llm, rec = _anthropic(response=first)

    resp = await llm.complete([{"role": "user", "content": "q"}], tools=[SEARCH])
    assert rec.calls[0]["tools"][0]["input_schema"] == SEARCH.input_schema
    assert [c.name for c in resp.tool_calls] == ["search", "search"]

    history = [
        {"role": "user", "content": "q"},
        resp.to_message(),
        tool_result_message(resp.tool_calls[0], "r1"),
        tool_result_message(resp.tool_calls[1], "r2", is_error=True),
    ]
    await llm.complete(history, tools=[SEARCH])
    sent = rec.calls[1]["messages"]
    # assistant turn replayed verbatim (thinking blocks would survive too)
    assert sent[1] == {"role": "assistant", "content": first.content}
    # both results in ONE user message — required for parallel tool use
    assert len(sent) == 3
    assert [b["tool_use_id"] for b in sent[2]["content"]] == ["t1", "t2"]
    assert sent[2]["content"][1]["is_error"] is True


def test_anthropic_fallback_echo_rule():
    from app.llm.anthropic_provider import _echoable

    blocks = [
        {"type": "thinking"}, {"type": "text"}, {"type": "tool_use"},
        {"type": "fallback"}, {"type": "thinking"}, {"type": "tool_use"},
    ]
    # before the last fallback block: drop thinking/tool_use, keep text; after it: keep all
    assert [b["type"] for b in _echoable(blocks)] == ["text", "fallback", "thinking", "tool_use"]


# ---------------------------------------------------------------------------------- OpenAI


@pytest.mark.asyncio
async def test_openai_system_first_and_tool_calls_parsed():
    tc = NS(id="c1", function=NS(name="search", arguments='{"q": "x"}'))
    llm, rec = _openai(response=_openai_resp(content=None, tool_calls=[tc], finish="tool_calls"))
    resp = await llm.complete([{"role": "user", "content": "q"}], system="sys", tools=[SEARCH], temperature=0.1)
    req = rec.calls[0]
    assert req["messages"][0] == {"role": "system", "content": "sys"}
    assert req["temperature"] == 0.1 and req["max_completion_tokens"] == 4096
    assert req["tools"][0]["function"]["parameters"] == SEARCH.input_schema
    assert resp.tool_calls == [ToolCall("c1", "search", {"q": "x"})]
    assert resp.stop_reason == "tool_use"

    await llm.complete([*req["messages"][1:], resp.to_message(), tool_result_message(resp.tool_calls[0], "r")])
    sent = rec.calls[1]["messages"]
    assert sent[1]["tool_calls"][0]["function"] == {"name": "search", "arguments": '{"q": "x"}'}
    assert sent[2] == {"role": "tool", "tool_call_id": "c1", "content": "r"}


@pytest.mark.asyncio
async def test_openai_reasoning_models_drop_temperature_and_use_reasoning_effort():
    llm, rec = _openai("gpt-5-mini")
    await llm.complete([{"role": "user", "content": "q"}], temperature=0.1, effort="low")
    assert "temperature" not in rec.calls[0] and rec.calls[0]["reasoning_effort"] == "low"


@pytest.mark.asyncio
async def test_openai_compatible_endpoint_uses_max_tokens():
    llm, rec = _openai("llama3.1:8b", base_url="http://ollama:11434/v1", provider_name="ollama")
    await llm.complete([{"role": "user", "content": "q"}])
    assert "max_tokens" in rec.calls[0] and "max_completion_tokens" not in rec.calls[0]
    assert llm.name == "ollama"


@pytest.mark.asyncio
async def test_openai_stream_collects_usage_for_billing():
    chunks = [
        NS(usage=None, model="gpt-4o-mini", choices=[NS(delta=NS(content="Hel"), finish_reason=None)]),
        NS(usage=None, model="gpt-4o-mini", choices=[NS(delta=NS(content="lo"), finish_reason="stop")]),
        NS(usage=NS(prompt_tokens=7, completion_tokens=2, prompt_tokens_details=None), choices=[]),
    ]

    class _Stream:
        def __init__(self):
            self.kwargs = None

        async def create(self, **kwargs):
            self.kwargs = kwargs

            async def gen():
                for c in chunks:
                    yield c

            return gen()

    s = _Stream()
    llm = OpenAIProvider("gpt-4o-mini", client=NS(chat=NS(completions=s)))
    events = [e async for e in llm.stream([{"role": "user", "content": "q"}])]
    assert s.kwargs["stream_options"] == {"include_usage": True}
    assert "".join(e.text for e in events if e.type == "text") == "Hello"
    done = events[-1].response
    assert done.text == "Hello" and done.usage.total_tokens == 9


# ---------------------------------------------------------------------------------- registry


def test_resolve_model_strings(monkeypatch):
    monkeypatch.setattr(settings, "llm_endpoints", {"ollama": {"base_url": "http://ollama:11434/v1"}})
    assert resolve("claude-opus-5-5") == ("anthropic", "claude-opus-5-5")
    assert resolve("gpt-4o") == ("openai", "gpt-4o")
    assert resolve("anthropic:claude-haiku-4-5") == ("anthropic", "claude-haiku-4-5")
    # only a known provider prefix splits, so Ollama tags survive
    assert resolve("ollama:llama3.1:8b") == ("ollama", "llama3.1:8b")
    assert resolve("llama3.1:8b") == ("openai", "llama3.1:8b")


def test_custom_endpoint_builds_openai_compatible_provider(monkeypatch):
    monkeypatch.setattr(settings, "llm_endpoints", {"local": {"base_url": "http://vllm:8000/v1"}})
    llm = get_llm("local:qwen3-32b")
    assert isinstance(llm, OpenAIProvider) and llm.name == "local" and llm.model == "qwen3-32b"


def test_unknown_explicit_provider_falls_back_to_inference():
    # "foo:bar" has no known provider prefix, so it is treated as a bare (openai) model name.
    assert resolve("foo:bar") == ("openai", "foo:bar")


def test_model_allowlist():
    assert model_allowed(["*"], "anything")
    assert model_allowed(["gpt-4o-mini"], None)  # server default is always allowed
    assert model_allowed(["gpt-4o-mini", "claude-haiku-4-5"], "anthropic:claude-haiku-4-5")
    assert not model_allowed(["gpt-4o-mini"], "claude-opus-5-5")


@pytest.mark.asyncio
async def test_use_llm_overrides_every_lookup(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    fake = FakeLLM(["one", LLMResponse(text="two", model="m")])
    with use_llm(fake):
        assert (await get_llm("claude-opus-5-5").complete([])).text == "one"
        assert (await get_llm().complete([], system="s")).text == "two"
    assert fake.calls[1]["system"] == "s"
    assert get_llm("gpt-4o") is not fake
