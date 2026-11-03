# Block 2 — The query pipeline as stages

**Files:** `backend/app/services/rag/stages.py`, `pipeline.py`, `generator.py` ·
**Tests:** `tests/test_stages.py` (plus the unchanged `test_pipeline_degradation.py`,
`test_conversation_memory.py`, `test_intent.py`, which pass against the new design)

## The problem it solves

`RAGPipeline.query()` used to be one long function with 18 boolean flags. The streaming method was a
separate, shorter copy, and the two had drifted:

- streaming **ignored** HyDE, multi-query, multi-hop, routing, business boosts, compression, graph,
  evaluation and tracing — the chat UI silently got a weaker pipeline than the API;
- streaming **never recorded usage**, so streamed chats were never billed;
- citations ran one DB query per cited chunk (N+1).

The general lesson: *two code paths that should behave the same will drift unless they share one
implementation.* Stages are how this pipeline shares one.

## The port

```python
class Stage(Protocol):
    name: str          # also the label recorded in ctx.degraded when it fails
    fail_soft: bool    # True = optional accelerator; a failure is logged and skipped
    def applies(self, ctx: QueryContext) -> bool: ...
    async def run(self, ctx: QueryContext) -> None: ...
```

`QueryContext` is the state for one request: the query, `QueryOptions` (every API knob), the
working `search_query`, `chunks`, `intent`, `hops`, `graph_facts`, bounded `history` + `summary`,
`degraded`, the `trace`, and an `extras` dict for custom stages.

## How a request flows

```
_prepare:   run_stages(self.stages, ctx)        # each stage auto-traced as "stage.<name>"
              routing → hyde → retrieve → business_rerank → compression → graph → conversation_memory
generate:   GenerationService.generate(...)     # or generate_stream(...) — same prompt builder
_finalize:  citations (one query) → optional self-eval → trace + cost → usage row
```

`query()` and `query_stream()` call the same `_prepare` and `_finalize`. Streaming emits
`token` events, then `citations`, then one final `done` (with usage, degraded stages, intent, trace).

## Fail-soft semantics

Retrieval is the only `fail_soft = False` built-in — without it there's nothing to answer from.
Everything else is an *accelerator*: if HyDE's LLM call fails, the query still runs on the raw
question and the response says `"degraded": ["hyde"]`. This "degrade, don't die" rule (F12) is
now enforced in exactly one place (`run_stages`) instead of eight copy-pasted `try/except` blocks.

## Recipe — a client-specific stage

A document-level permission filter (the client's IAM decides who may see which document):

```python
class AclFilterStage:
    name = "acl_filter"
    fail_soft = False                       # security: if the check fails, fail the request

    def __init__(self, acl_client):
        self.acl = acl_client

    def applies(self, ctx):
        return bool(ctx.chunks)

    async def run(self, ctx):
        allowed = await self.acl.allowed_documents(ctx.pipeline.user_id, {c.document_id for c in ctx.chunks})
        ctx.chunks = [c for c in ctx.chunks if c.document_id in allowed]

stages = list(DEFAULT_STAGES)
stages.insert(stages.index(next(s for s in stages if s.name == "retrieve")) + 1, AclFilterStage(acl))
pipeline = RAGPipeline(db, tenant_id, user_id, stages=stages)
```

Other stages clients ask for: PII redaction before generation, a "freshness" boost for news,
query translation for multilingual corpora, a cache lookup that short-circuits retrieval.

Note `fail_soft`: choose it per stage, deliberately. An ACL or PII stage must **fail closed**
(`False`); an enrichment stage should **fail open** (`True`).

## Alternatives on the market

| Option | Model | Trade-off |
|---|---|---|
| **LangChain LCEL / LangGraph** | composable runnables / a state graph | Powerful branching; a large framework to learn and keep up with. |
| **LlamaIndex query pipelines** | query engines + node postprocessors | Batteries-included retrieval; customizing deep internals is harder. |
| **Haystack 2.x pipelines** | typed components wired into a DAG | Closest in spirit; explicit I/O typing at the cost of more ceremony. |
| **This block** | an ordered list of 4-member objects over one context | Linear by design — easy to read, debug, and explain to a client. Branching (e.g. routing) is a stage setting flags others read. |

If a client's flow becomes truly graph-shaped (loops, parallel branches, human approval), that is the
moment to reach for LangGraph — and the stages port over as nodes.

## Vocabulary

*pipeline stage, context object, fail-open vs fail-closed, graceful degradation, N+1 query,
code-path parity, tracing span.*
