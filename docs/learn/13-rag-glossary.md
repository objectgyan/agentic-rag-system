# 12 · The RAG glossary — talk the AI language

The vocabulary a technical customer (or interviewer, or teammate) will use, defined so you can use
it *back* with confidence. Each term gets: a plain definition, where it lives **in this repo**, and
— for the ones that come up in conversations — a **say-it-like** example of using it naturally.

Read the two pages after this to see the words in motion: [a query traced end-to-end](14-query-trace.md)
and [measuring quality](15-evaluation.md).

> How to use this page: skim the section headers, read the terms you can't yet explain out loud.
> The test of "I know this word" is being able to say the *say-it-like* sentence unprompted.

---

## 1. The big picture

**RAG (Retrieval-Augmented Generation)** — instead of asking an LLM to answer from memory, you first
*retrieve* relevant text from your own documents and *augment* the prompt with it, so the model
answers from *your* data with citations. Cuts hallucination, lets the model use private/fresh info.
→ The whole `backend/app/services/rag/` package. *Say it like:* "It's RAG — we ground the model on
the customer's docs rather than fine-tuning."

**Grounding** — forcing the answer to come from provided context, not the model's parametric memory.
The opposite failure is **hallucination** — a fluent, confident, *wrong* answer. → We measure
grounding with the **faithfulness** metric (§7). *Say it like:* "Is the answer grounded, or is it
hallucinating?"

**Agentic RAG** — RAG where an **agent** (an LLM in a loop that can *decide* actions and call
**tools**) drives retrieval, instead of a fixed retrieve-then-generate pipeline. → `services/agents/`.

**Context window** — the maximum tokens a model can read at once (prompt + retrieved context +
answer). Everything you retrieve competes for this budget — the reason chunking and compression
exist. *Say it like:* "We're blowing the context window on long chats — we need a token budget."

---

## 2. Chunking — splitting documents

**Chunk** — a small slice of a document (a few hundred tokens) that is embedded and retrieved as a
unit. You retrieve *chunks*, not whole files. → the `chunks` table; `services/rag/chunker.py`.

**Token** — the unit models actually read — roughly ¾ of a word. Counted with **`tiktoken`** (the
library that tokenizes the way OpenAI models do). *Say it like:* "That doc is ~4k tokens, so it's
about 8 chunks."

**Chunk overlap** — repeating a bit of text between adjacent chunks so a sentence split across a
boundary still appears whole somewhere. → a real bug lived here: overlap ≥ size → infinite loop
(now clamped). *Say it like:* "We use 50-token overlap so we don't cut a sentence in half."

**Chunking strategy** — *how* you split. Named strategies (chosen per collection in `chunker.py`):
- **Fixed** — every N tokens with overlap. Simple, predictable.
- **Semantic / paragraph** — split on natural boundaries (paragraphs, headings). Keeps ideas intact.
- **Recursive** — try big separators first (sections), fall back to smaller (sentences).
- **Parent-child** — tiny chunks for *precise matching*, each linked to a bigger *parent* chunk that
  gives the model *context*. You match small, feed large.

*Say it like:* "What's your chunking strategy? Fixed-size will fragment these tables — try recursive
or parent-child."

---

## 3. Embeddings & vector search (dense / semantic)

**Embedding** — a list of numbers (a **vector**, 1536-dim by default here) that captures the *meaning*
of a piece of text. Similar meanings → nearby vectors. → `services/rag/embedder.py`, OpenAI
`text-embedding-3-small`. *Say it like:* "We embed each chunk and the query into the same vector space."

**Embedding model** — the model that produces embeddings (distinct from the *LLM* that writes answers).
→ `default_embedding_model` in `config.py`.

**Dense search / semantic search / vector search** — three names for the same thing: embed the query,
find chunks whose vectors are **nearest**. Finds *paraphrases* ("car" ~ "automobile") that keyword
search misses. → `HybridRetriever._dense_search` in `retriever.py`.

**Cosine similarity** — the distance measure between two vectors (angle-based). pgvector's `<=>`
operator. Score near 1 = very similar. → the `1 - (embedding <=> query)` in the dense SQL.

**pgvector** — the Postgres extension that stores vectors and does nearest-neighbor search in the DB,
so you don't need a separate vector database. → the `embedding` column on `chunks`.

**Vector database** — a store specialized for vectors (Pinecone, Weaviate, Qdrant, Milvus). We use
pgvector *instead* — one fewer moving part. *Say it like:* "We didn't need a dedicated vector DB;
pgvector handles our scale."

**ANN (Approximate Nearest Neighbor)** — finding *near*-nearest vectors fast by not checking every
one. Exact search is O(n); ANN is what makes vector search scale.

**HNSW / IVFFlat** — the two common ANN **index** types. **HNSW** (a navigable graph) is the modern
default — fast, accurate, more memory. **IVFFlat** (clustered lists) is lighter, needs tuning. →
this repo has an HNSW index on the vector column. *Say it like:* "Make sure there's an HNSW index or
you're doing a sequential scan on every query."

---

## 4. Keyword search (sparse / lexical)

**Sparse search / lexical search / keyword search** — matching on the actual *words*, not meaning.
Nails exact terms embeddings blur: names, IDs, error codes, SKUs. Called "sparse" because the
representation is a huge mostly-zero vector over the vocabulary. → `HybridRetriever._sparse_search`.

**BM25** — the classic keyword-relevance ranking function (what search engines used pre-embeddings).
Scores a doc by query-term frequency, dampened by how common each term is. *Say it like:* "Pure
vector search was missing exact part numbers, so we added a keyword pass."

**Postgres full-text search (`tsvector` / `ts_rank`)** — how this repo does sparse search **in the
database**: a GIN-indexed `content_tsv` generated column, ranked with `ts_rank` over
`websearch_to_tsquery` (migration 006). It replaced an earlier in-Python BM25 that loaded ≤1000 rows
per query — the same keyword idea, but indexed and scalable instead of scored in-process.

**tsvector** — Postgres' built-in full-text search type; this repo's sparse search is a GIN-indexed
`content_tsv` column (see the entry just above) — how keyword search scales beyond in-process scoring.

---

## 5. Combining & reranking

**Hybrid search** — running **dense + sparse together** and merging, so you get *both* meaning and
exact-term matching. The current best-practice default. → `HybridRetriever.retrieve`.

**RRF (Reciprocal Rank Fusion)** — the simple, robust way to *merge two ranked lists* without
worrying that their scores are on different scales: each item scores `Σ weight / (k + rank)`; items
ranked highly by *either* list rise. → `_rrf_fusion` in `retriever.py`. *Say it like:* "We fuse the
dense and sparse rankings with RRF — no score normalization headaches."

**Top-k** — how many results you keep (`top_k=5` here). **@k** in metrics means "measured over the
top k."

**Reranking** — a precise *second pass* that reorders the shortlist a cheap first pass produced. →
`_rerank`; deep-dive in [`../RERANKING.md`](../RERANKING.md). *Say it like:* "Retrieval gets us 100
candidates; the reranker picks the best 5."

**Bi-encoder** — embeds query and document **separately**, compares vectors. Fast (embed docs once,
offline) — this is your Stage-1 retriever.

**Cross-encoder** — feeds the **(query, document) pair together** into one model for a far more
accurate relevance score. Slow — only affordable on the shortlist. This is your **reranker**. →
`_rerank_local` (sentence-transformers) / `_rerank_cohere`. *Say it like:* "The reranker is a
cross-encoder — it sees the query and passage together, so it's sharper than the bi-encoder."

**Cohere Rerank** — a hosted cross-encoder reranker (API, proprietary). **sentence-transformers** —
the open-source library for running cross-encoders (and bi-encoders) locally, free. → both wired in
`retriever.py`; Cohere if a key is set, else a local model, else no-op.

---

## 6. Query understanding

**Query enhancement** — rewriting/expanding the user's query *before* retrieval, because their wording
may not match the documents'. → `services/rag/query_enhancer.py`.

**HyDE (Hypothetical Document Embeddings)** — ask the LLM to *write a fake answer* to the question,
then embed *that* and search with it. Counter-intuitive but effective: a hypothetical answer often
sits closer in vector space to real answer passages than the question does. → `hyde_generate`.
*Say it like:* "Short queries retrieve poorly, so we use HyDE to expand them first."

**Multi-query expansion** — generate several rephrasings of the question, retrieve for each, merge —
casting a wider net. → `multi_query_expand`.

**Query decomposition** — break a complex question into simpler sub-questions. → `decompose_query`.

**Intent classification / query routing** — classify the query up front (product-search vs comparison
vs chit-chat) and branch — e.g. skip retrieval for chit-chat. Under the hood it's a small
**zero-shot classification** — often the same cross-encoder scoring the query against candidate
intent labels (`argmax` instead of top-k). → *not built yet*; it's roadmap item ④. *Say it like:*
"We route on intent so smalltalk doesn't hit the retriever."

---

## 7. Generation & citations

**LLM (Large Language Model)** — the model that writes the final answer (`gpt-4o-mini` here). Chosen
by name string — `startswith("claude")` → Anthropic, else OpenAI. → `generator.py`.

**Prompt** — the assembled input: system instructions + retrieved context + the question. **System
prompt** — the standing instructions ("answer only from context, cite `[Source N]`").

**Temperature** — the randomness dial (0 = deterministic/factual, higher = creative). RAG uses low
temperature (0.1) — you want faithful, not imaginative. *Say it like:* "Keep temperature low for RAG;
we're not writing poetry."

**Citation** — the `[Source N]` reference tying a claim to the chunk it came from. This repo does a
neat trick: it parses the answer and returns **only the sources actually cited**, not everything
retrieved. → `_extract_cited_sources` in `pipeline.py`.

---

## 8. Advanced RAG moves

**Multi-hop retrieval** — retrieve → *reason about what's still missing* → retrieve again. For
questions needing facts from different chunks ("who manages the author of the 2019 report?"). →
`multihop.py`, bounded by `max_hops`. *Say it like:* "That needs multi-hop — one retrieval can't
join those two facts."

**Contextual compression** — after retrieving, use a cheap model to **distil each chunk to only the
sentences relevant to the query**, so you don't waste the context window on noise. → `compressor.py`.

**Knowledge graph / GraphRAG** — extract `(subject, predicate, object)` **triples** (e.g. *Maria Chen
→ reports to → Tom Blake*) at ingest, then at query time pull facts about the entities in the
question. Answers questions from *relationships* no single chunk states. → `graph.py`, `graph_edges`
table.

**ReAct** — the agent loop pattern: **Reason** (Thought) → **Act** (call a tool) → observe → repeat
until a Final Answer. This repo parses it from text rather than using native tool-calling. →
`orchestrator.py`. **Tool** — a capability the agent can invoke (search, web, delegate). →
`tools.py`.

---

## 9. Evaluation — measuring quality

The language of the [evaluation chapter](15-evaluation.md). This is what separates hobby RAG from
production RAG.

**Golden set / ground truth** — human-labeled `(question → which chunks/docs are correct, ideal
answer)` cases you score against. The most valuable artifact in a RAG project. → `evaluation/dataset.py`.

**Relevance judgment** — a human's call that "chunk X *is* a correct source for question Q." The
labels that make retrieval metrics possible.

**Retrieval metrics** (do we fetch the right chunks?):
- **recall@k** — of all the relevant chunks, how many made the top-k? *(coverage — did we miss any?)*
- **precision@k** — of the top-k shown, how many were relevant? *(noise — how much junk?)*
- **MRR (Mean Reciprocal Rank)** — average of 1/(rank of first relevant hit). Rewards putting a good
  result *first*.
- **hit@k** — did we get *anything* relevant in the top-k? (binary)
- **nDCG@k** — like recall but *rank-weighted*: a relevant item higher up scores more.
→ all in `evaluation/metrics.py`. *Say it like:* "recall@5 is 0.6 — we're missing 40% of the gold
chunks, so retrieval is the bottleneck, not generation."

**Generation metrics** (given context, is the answer good?):
- **Faithfulness / groundedness** — fraction of the answer's claims supported by the context.
- **Answer relevance** — does the answer address the question?
- **Context precision** — how much of the retrieved context was actually useful.
- **Completeness** — does the answer cover the ground truth?
→ `evaluator.py` (scored by an **LLM-as-judge**).

**LLM-as-judge** — using an LLM to *score* another LLM's output (faithfulness, relevance). Cheap,
scalable, imperfect — you spot-check it. *Say it like:* "We grade faithfulness with an LLM judge and
sample 10% by hand."

**Offline vs online eval** — **offline** = scoring against a fixed golden set (a regression suite you
run on every change). **online** = measuring real user behavior in production (click-through,
thumbs-up). You need both.

**Regression (gating)** — failing the build if a change *drops* a metric below a threshold. →
`--min-recall` / `--min-faithfulness` on the eval CLI.

**RAGAS** — a popular open-source RAG-eval library (faithfulness, answer/context relevance). Worth
naming; our `evaluator.py` implements the same ideas in-house.

---

## 10. Platform & multi-tenancy

**Multi-tenant** — one app instance serving many isolated customers (**tenants**). The central
invariant here. *Say it like:* "It's multi-tenant with hard isolation — tenant A can never see
tenant B's chunks."

**RLS (Row-Level Security)** — Postgres enforcing "you only see your tenant's rows" *at the database*,
not in app code. The keystone of the isolation. → `init.sql` policies + `set_tenant_context`. *Say it
like:* "Isolation is enforced by Postgres RLS, so an app bug can't leak across tenants."

**Ingestion** — the write path: upload → extract → chunk → embed → store. Runs async in a **Celery**
worker. → `services/processing/`. **Retrieval/query** — the read path this glossary is mostly about.

---

Prev: [Build it yourself](12-build-it-yourself.md) · Next: [A query, traced end-to-end →](14-query-trace.md)
