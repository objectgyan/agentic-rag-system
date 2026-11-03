# Block 1 — The LLM provider

**Files:** `backend/app/llm/` — `types.py`, `base.py`, `anthropic_provider.py`,
`openai_provider.py`, `registry.py`, `fake.py` · **Tests:** `tests/test_llm_providers.py`

## The problem it solves

Before this block, ten modules each built an OpenAI or Anthropic client and called it directly,
choosing the vendor with `model.startswith("claude")`. Consequences, all real bugs found in the audit:

- Setting `DEFAULT_LLM_MODEL=claude-…` broke HyDE, multi-query and the evaluator — they sent the
  Claude model name **to the OpenAI client**.
- The Anthropic calls sent `temperature`, which current Claude models reject with a 400, and read
  `response.content[0].text`, which breaks when the first block is a *thinking* block.
- A client asking for Azure OpenAI or a self-hosted model meant editing ten files.

## The port

```python
class LLMProvider(Protocol):
    name: str
    model: str
    async def complete(messages, *, system=None, tools=None, max_tokens=None,
                       temperature=None, effort=None) -> LLMResponse: ...
    def stream(messages, *, system=None, max_tokens=None,
               temperature=None, effort=None) -> AsyncIterator[StreamEvent]: ...
```

Design decisions worth copying:

- **Messages are plain dicts**, not SDK objects — they serialize to JSON, a DB row, a log line.
- **`system` is separate from `messages`.** Providers disagree on where it goes (Anthropic: a
  top-level field; OpenAI: the first message). The port hides that.
- **Tool calls are neutral** (`ToolCall(id, name, arguments)`), and tool results are
  `{"role": "tool", ...}` messages. Each adapter translates to its wire format.
- **`temperature` is a hint, `effort` is a hint.** The Claude API removed sampling parameters
  (API version 2026-10-21), and OpenAI reasoning models reject them. Code that needs determinism
  must get it from prompts and robust parsing, never from `temperature=0`.
- **`LLMResponse.native`** carries the provider's own form of an assistant turn. Some providers
  require earlier turns replayed *unchanged* (Claude binds thinking blocks to the conversation);
  the adapter replays `native` when it sees its own name on it.
- **Refusals are data, not exceptions:** `stop_reason="refusal"`, empty text. Callers decide the
  user-facing message (`generator.REFUSAL_MESSAGE`).

## The adapters

| Adapter | Covers | Notable translation |
|---|---|---|
| `AnthropicProvider` | Claude API, Anthropic-compatible gateways | system prompt marked `cache_control` (prompt caching); parallel tool results merged into one user message; `effort` → `output_config.effort`; opts into the server-side refusal fallback on models that support it; raises tiny `max_tokens` on always-thinking models |
| `OpenAIProvider` | OpenAI, **Azure OpenAI, Ollama, vLLM, LM Studio, Groq, Together, OpenRouter, LiteLLM** | `max_completion_tokens` vs legacy `max_tokens`; reasoning models get `reasoning_effort` and no `temperature`; streaming requests usage (`stream_options.include_usage`) so streamed answers can be billed |
| `FakeLLM` | tests, demos, offline dev | scripted replies (text, tool calls, exceptions, or callables); records every call |

## Composition: the registry

```python
get_llm()                          # settings.default_llm_model
get_llm("claude-opus-5-5")         # bare name -> inferred provider (claude-* -> anthropic)
get_llm("anthropic:claude-haiku-4-5")
get_llm("ollama:llama3.1:8b")      # a configured endpoint; the tag's ":" survives
get_fast_llm()                     # settings.default_fast_model, for helper calls
```

Two production details hidden in `registry.py`:

1. **Instances are cached per event loop.** A client holds a connection pool bound to the loop
   that created it; Celery runs each task in a *new* loop, so a naive global cache dies with
   `Event loop is closed`. The cache key includes the running loop, with an LRU bound.
2. **Model strings stay backward compatible.** Every bare name already stored in conversations or
   sent by the frontend resolves exactly as before.

## Alternatives on the market

| Option | What it is | When to prefer it over this block |
|---|---|---|
| **LiteLLM** | A library + proxy that speaks the OpenAI format to 100+ providers | You need many exotic providers, or a central proxy with budgets/keys. It plugs in *behind* this block as one more OpenAI-compatible endpoint. |
| **LangChain / LlamaIndex chat-model classes** | Framework abstractions | You're already all-in on that framework. The cost is a large dependency surface and abstractions that move fast. |
| **Vendor SDKs directly** | `anthropic`, `openai` | A single-provider product — but you lose test fakes and portability. |
| **This block** | ~400 lines you own | You need to *explain and modify* every line for a client, test offline, and keep vendor features (prompt caching, refusal fallback, thinking replay) first-class. |

The honest trade-off: owning the adapter means you track vendor API changes yourself. The tests in
`test_llm_providers.py` pin the wire shapes, so an SDK upgrade that changes them fails loudly.

## Recipe — swap it for a client

**Azure OpenAI** (data stays in the client's tenant):

```env
LLM_ENDPOINTS={"azure": {"base_url": "https://acme.openai.azure.com/openai/v1/", "api_key": "<key>", "max_tokens_param": "max_completion_tokens"}}
DEFAULT_LLM_MODEL=azure:gpt-4o
DEFAULT_FAST_MODEL=azure:gpt-4o-mini
```

**Fully local** (air-gapped): run Ollama or vLLM next to the stack, then
`LLM_ENDPOINTS={"local": {"base_url": "http://vllm:8000/v1"}}` and `DEFAULT_LLM_MODEL=local:<model>`.

**Claude:** `ANTHROPIC_API_KEY=...`, `DEFAULT_LLM_MODEL=claude-opus-5-5`,
`DEFAULT_FAST_MODEL=claude-haiku-4-5`. Embeddings still need OpenAI, Cohere, or a local model —
Anthropic has no embeddings endpoint.

**A new provider kind** (e.g. a proprietary gateway with its own API): write an adapter class with
`complete`/`stream`, then `register_provider("acme", lambda model: AcmeProvider(model))`.

## Testing with it

```python
from app.llm import FakeLLM, LLMResponse, ToolCall, use_llm

with use_llm(FakeLLM([LLMResponse(text="", model="m", tool_calls=[ToolCall("1", "calculator", {"expression": "2+2"})]),
                      "The answer is 4."])):
    result = await agent.execute("what is 2+2?")
```

One seam for every LLM call in the app — no more monkeypatching a client factory per module.

## Vocabulary

*ports and adapters, provider abstraction, OpenAI-compatible API, prompt caching, reasoning
effort, refusal / stop reason, tool calling (function calling), streaming usage accounting.*
