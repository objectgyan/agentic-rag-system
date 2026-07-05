# AgentRAG — Learning Guide

A from-scratch explanation of **every** part of this stack, written for someone who knows
**.NET / C#** but is **new to Python and this ecosystem**. The goal: understand each
component well enough to rebuild the whole thing yourself.

Each page assumes you've read the ones before it. They reference the real source files so
you can read the actual code alongside the explanation.

## How to use this guide

1. Read in order — concepts build on each other.
2. Keep the repo open. When a page says *"see `backend/app/...`"*, open that file.
3. Run the stack (`docker-compose up -d`) and poke the live API at http://localhost:8000/docs
   while you read. Seeing it run is half the learning.

## The learning path

| # | Page | What you'll learn |
|---|------|-------------------|
| 00 | [Orientation](01-orientation.md) | What the system does, the tech stack, and a **.NET → this stack** translation table |
| 01 | [Python & async primer](02-python-async-primer.md) | Just enough Python, `async/await`, packaging, and types — for a C# dev |
| 02 | [The request lifecycle](03-request-lifecycle.md) | ASGI, Uvicorn, FastAPI, middleware, dependency injection, Pydantic |
| 03 | [Multi-tenancy & RLS](04-multitenancy-and-rls.md) | How one app safely serves many customers — the keystone of the design |
| 04 | [Authentication & authorization](05-auth.md) | JWTs, refresh tokens, API keys, RBAC, password hashing |
| 05 | [Data, the ORM & vectors](06-data-and-vectors.md) | Postgres, SQLAlchemy, async DB access, `pgvector`, embeddings, migrations |
| 06 | [The RAG pipeline](07-rag-pipeline.md) | Chunking, embeddings, hybrid search, re-ranking, multi-hop, compression, knowledge graph, generation |
| 07 | [The agent system](08-agents.md) | The ReAct loop, tools, and multi-agent delegation |
| 08 | [Async ingestion](09-async-ingestion.md) | Celery, Redis, background workers, retries |
| 09 | [The frontend](10-frontend.md) | React, Vite, Zustand, TanStack Query, streaming over SSE/WebSocket |
| 10 | [Ops, Docker & deploy](11-ops-and-deploy.md) | Containers, compose, CI, metrics, logging, Helm |
| 11 | [Build it yourself](12-build-it-yourself.md) | A staged plan to rebuild this from an empty folder |
| 12 | [The RAG glossary](13-rag-glossary.md) | **Talk the AI language** — every term (dense/sparse/hybrid, BM25, RRF, cross-encoder, chunking strategies, HyDE, faithfulness…) with its repo pointer |
| 13 | [A query, traced end-to-end](14-query-trace.md) | One real question followed through every hop, naming the tech at each step |
| 14 | [Measuring quality (evaluation)](15-evaluation.md) | The eval harness: golden sets, recall@k / MRR / nDCG, faithfulness, LLM-as-judge, regression gating |

## The 60-second mental model

AgentRAG is a **multi-tenant SaaS** that lets organizations upload documents and then **ask
questions answered from those documents** (this is "RAG" — Retrieval-Augmented Generation).

- A **web API** (Python/FastAPI) handles requests — like an ASP.NET Core Web API.
- **PostgreSQL** stores everything, including the documents' **vector embeddings** (numeric
  fingerprints of meaning) so we can search by *similarity*, not just keywords.
- **Background workers** (Celery) do the slow work — reading files, splitting them, computing
  embeddings — off the request thread.
- **Redis** is the message queue between the API and the workers (and a cache / rate-limit store).
- **MinIO** stores the raw uploaded files (it's an S3-compatible blob store).
- A **React** single-page app is the UI.
- Big **language models** (OpenAI/Anthropic) write the final answers from the retrieved text.

> See also: [`../ARCHITECTURE.md`](../ARCHITECTURE.md) for the one-diagram overview, and
> [`../PRODUCTION_ROADMAP.md`](../PRODUCTION_ROADMAP.md) for *why* the code looks the way it does.
