# Building Blocks — taking AgentRAG apart

The [Learning Guide](../README.md) explains *how this app works*. This section explains how it is
**assembled** — each component as a block with a clear interface, so you can lift it into another
codebase, swap its implementation for a client, or rebuild it from scratch.

Every block follows the same four-part shape. Learn it once and you can read (and design) any of them:

| Part | What it is | Example (LLM block) |
|---|---|---|
| **Port** | The interface the rest of the app depends on. Small, vendor-neutral. | `LLMProvider.complete()` / `.stream()` in `app/llm/base.py` |
| **Adapters** | One implementation per vendor or strategy, translating to/from the port. | `AnthropicProvider`, `OpenAIProvider` |
| **Composition** | How the app picks an adapter: config, a registry, or an injected list. | `get_llm("anthropic:claude-opus-5-5")` in `registry.py` |
| **Fake** | A first-class offline implementation for tests and demos. | `FakeLLM` + `use_llm()` |

This is the **ports-and-adapters** (hexagonal) architecture. Its payoff in client work: the
customer's constraints ("must run on Azure", "no data leaves our VPC", "use our ranking signals")
become *configuration and new adapters*, not a rewrite.

## The blocks

| # | Block | Port | Shipped adapters | Read |
|---|---|---|---|---|
| 1 | **LLM provider** | `LLMProvider` | Anthropic (Claude), OpenAI, any OpenAI-compatible server (Azure, Ollama, vLLM, Groq…), Fake | [01](01-llm-provider.md) |
| 2 | **Query pipeline** | `Stage` | route, HyDE, retrieve, business re-rank, compress, graph, memory | [02](02-pipeline-stages.md) |
| 3 | **Agent loop** | tools as `ToolSpec` + handlers | native tool-calling loop; retrieval, calculator, web search, summarize, compare, delegate | [03](03-agent-loop.md) |
| 4 | **Retrieval** | `EmbeddingService`, `Reranker` | OpenAI / Cohere / local embeddings; Cohere / cross-encoder / metadata-boost rerankers | [04](04-retrieval-blocks.md) |
| 5 | **Policy & metering** | `assert_model_allowed`, `record_usage` | tier model allowlists, per-call usage + cost rows | [05](05-policy-and-metering.md) |

Earlier blocks — multi-tenancy/RLS, auth, ingestion — are covered in the main guide
([04](../04-multitenancy-and-rls.md), [05](../05-auth.md), [09](../09-async-ingestion.md)).

## Using the blocks on a client engagement

A typical first week, expressed as block decisions:

1. **Model hosting** (block 1). Where may data go? Claude/OpenAI API → defaults. Azure tenant →
   an `LLM_ENDPOINTS` entry. Air-gapped → Ollama/vLLM endpoint. No code changes.
2. **Retrieval quality** (block 4). Pick embeddings for the language/domain; measure with the
   [eval harness](../15-evaluation.md) before and after — numbers, not adjectives.
3. **Business logic** (block 2). Their rules become stages: an ACL filter, a "prefer in-stock
   products" boost, a PII scrubber. Each is ~20 lines and independently testable.
4. **Agent tools** (block 3). Their systems (CRM, ticketing, SQL) become tools with JSON schemas.
5. **Commercials** (block 5). Which tiers get which models; usage rows feed invoices.

## Roadmap for the blocks

This is phase 1 of the upgrade. Next phases add blocks with the same shape:

- **Phase 2 — Safety & trust:** a `Guardrail` stage family (prompt-injection screening of
  retrieved text, PII redaction, citation-grounding verification).
- **Phase 3 — Retrieval quality:** contextual retrieval, layout/table-aware parsing, agentic
  retrieval — each gated by the eval harness.
- **Phase 4 — Integrations:** the pipeline exposed as an MCP server, OpenTelemetry/Langfuse trace
  export, data-source connectors.
