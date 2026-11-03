"""Smoke-test a deployment's model configuration against the real provider APIs.

    python -m app.cli.smoke                          # the configured default + fast models
    python -m app.cli.smoke --model claude-sonnet-5-5 --model ollama:llama3.1:8b
    python -m app.cli.smoke --no-agent               # skip the (pricier) multi-turn agent check

Run it whenever a client's keys, models or endpoints change: it proves each model answers,
streams, and works inside the RAG pipeline and the tool-calling agent loop. No database is
needed — retrieval is replaced by two in-memory passages; everything else is the real path.

It makes real API calls (a fraction of a cent to a few cents, printed at the end) and never
prints a key. In Docker: ``docker compose exec backend python -m app.cli.smoke``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from app.core.config import settings
from app.llm import get_llm
from app.services.rag.retriever import RetrievedChunk
from app.services.rag.tracing import estimate_cost

PASSAGES = [
    RetrievedChunk("c1", "d1", "The Apollo internal project was led by engineer Maria Chen, who designed its core system.", 0.9),
    RetrievedChunk("c2", "d2", "Apollo shipped in March 2026 with a budget of 1.2 million dollars across 3 teams.", 0.8),
]


class _InMemoryRetriever:
    async def retrieve(self, query, collection_ids=None, top_k=5, use_reranking=True):
        return list(PASSAGES)

    async def _rerank(self, query, chunks, top_k):
        return chunks[:top_k]


class Report:
    def __init__(self):
        self.rows = []
        self.cost = 0.0

    def add(self, name, ok, detail, model=None, usage=None):
        cost = estimate_cost(model, usage.input_tokens, usage.output_tokens) if (model and usage) else 0.0
        self.cost += cost
        self.rows.append(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}" + (f"  (${cost:.5f})" if cost else ""), flush=True)

    async def run(self, name, coro):
        start = time.time()
        try:
            await coro
        except Exception as exc:  # report and keep going: one broken model shouldn't hide the rest
            self.add(name, False, f"{type(exc).__name__}: {str(exc)[:300]}")
        print(f"       took {time.time() - start:.1f}s", flush=True)


async def check_complete(report, model):
    resp = await get_llm(model).complete(
        [{"role": "user", "content": "Reply with exactly the word: pong"}],
        system="You are a terse test endpoint.", max_tokens=64,
    )
    ok = "pong" in resp.text.lower() and resp.stop_reason == "end_turn" and resp.usage.output_tokens > 0
    report.add(f"complete [{model}]", ok, f"text={resp.text.strip()[:40]!r} stop={resp.stop_reason}", model, resp.usage)


async def check_stream(report, model):
    parts, final = [], None
    async for ev in get_llm(model).stream(
        [{"role": "user", "content": "Count from 1 to 5, separated by spaces."}], max_tokens=64
    ):
        if ev.type == "text":
            parts.append(ev.text)
        else:
            final = ev.response
    text = "".join(parts)
    ok = final is not None and "1" in text and "5" in text and final.usage.total_tokens > 0
    report.add(f"stream [{model}]", ok, f"{len(parts)} deltas, text={text.strip()[:30]!r}",
               model, final.usage if final else None)


async def check_pipeline(report, model):
    from app.services.rag.pipeline import RAGPipeline

    pipeline = RAGPipeline(db=object(), tenant_id="smoke", user_id="smoke")
    pipeline.retriever = _InMemoryRetriever()
    billed = []

    async def track(tokens, m, prompt_tokens=None, completion_tokens=None):
        billed.append(tokens)

    async def citations(chunks, answer):
        return [{"chunk_id": c.chunk_id} for i, c in enumerate(chunks, 1) if f"[Source {i}]" in answer]

    pipeline._track_usage = track  # no DB: capture billing and citations instead of writing them
    pipeline._citations = citations
    events = [e async for e in pipeline.query_stream("Who led the Apollo project?", model=model)]
    answer = "".join(e["content"] for e in events if e["type"] == "token")
    cites = next((e["citations"] for e in events if e["type"] == "citations"), [])
    ok = ("Maria Chen" in answer and events[-1]["type"] == "done" and billed and billed[0] > 0
          and any(c["chunk_id"] == "c1" for c in cites))
    report.add(f"RAG pipeline stream [{model}]", ok,
               f"answer={answer.strip()[:70]!r} citations={[c['chunk_id'] for c in cites]} tokens={billed}")


async def check_agent(report, model):
    from app.services.agents.orchestrator import AgentOrchestrator

    agent = AgentOrchestrator(db=None, tenant_id="smoke", user_id="smoke")
    agent.tool_registry.retriever = _InMemoryRetriever()
    out = await agent.execute(
        "Using the documents, find Apollo's budget and team count, then use the calculator to compute "
        "the budget per team in dollars. Give the final number.",
        model=model, tools=["retrieval", "calculator"], max_steps=6,
    )
    actions = [s["action"] for s in out["steps"] if s["action"]]
    ok = "retrieval" in actions and "calculator" in actions and "400" in out["result"].replace(",", "")
    report.add(f"agent tool loop [{model}]", ok,
               f"actions={actions} result={out['result'].strip()[:80]!r} tokens={out['total_tokens']}")


async def check_embeddings(report):
    from app.services.rag.embedder import EmbeddingService

    svc = EmbeddingService()
    vec = await svc.embed_query("smoke test")
    ok = len(vec) == settings.default_embedding_dimensions
    report.add(f"embed [{svc.model}]", ok,
               f"{len(vec)} dimensions (configured {settings.default_embedding_dimensions})")


async def main_async(models, agent) -> int:
    report = Report()
    await report.run("embed", check_embeddings(report))
    for i, model in enumerate(models):
        await report.run(f"complete [{model}]", check_complete(report, model))
        await report.run(f"stream [{model}]", check_stream(report, model))
        if i == 0:  # the answer model: also run it inside the pipeline and the agent loop
            await report.run(f"RAG pipeline stream [{model}]", check_pipeline(report, model))
            if agent:
                await report.run(f"agent tool loop [{model}]", check_agent(report, model))
    passed = sum(report.rows)
    print(f"\n{passed}/{len(report.rows)} passed; estimated cost of priced calls ${report.cost:.4f}")
    return 0 if passed == len(report.rows) else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli.smoke", description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", action="append",
                        help="model to check (repeatable). Default: DEFAULT_LLM_MODEL then DEFAULT_FAST_MODEL")
    parser.add_argument("--no-agent", action="store_true", help="skip the multi-turn agent check")
    args = parser.parse_args(argv)
    models = args.model or list(dict.fromkeys([settings.default_llm_model, settings.default_fast_model]))
    print(f"models: {', '.join(models)}  |  keys: "
          f"anthropic={'set' if settings.anthropic_api_key else 'unset'} "
          f"openai={'set' if settings.openai_api_key else 'unset'} "
          f"endpoints={sorted(settings.llm_endpoints) or 'none'}\n")
    return asyncio.run(main_async(models, agent=not args.no_agent))


if __name__ == "__main__":
    sys.exit(main())
