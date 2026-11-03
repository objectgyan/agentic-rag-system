# Block 5 — Policy and metering

**Files:** `backend/app/api/deps/access.py` (`assert_model_allowed`), `backend/app/services/usage.py`
(`record_usage`), `backend/app/services/rag/tracing.py` (`MODEL_PRICING`, `estimate_cost`),
`backend/app/core/config.py` (`TierLimits`) · **Tests:** `tests/test_llm_providers.py::test_model_allowlist`

A SaaS that resells model calls needs two things the demo version usually skips: **who may use
which model**, and **what each call cost**.

## Model policy

`TierLimits` always listed which models each tier may use (`free`: `gpt-4o-mini`,
`claude-haiku-4-5`; `pro`: adds `gpt-4o`, Sonnet, Opus; `enterprise`: `*`) — but nothing enforced
it. A free-tier user could request the most expensive model. Now every query, stream, WebSocket
frame and agent run calls:

```python
await assert_model_allowed(db, user, req.model)   # 403 if the tier doesn't allow it
```

Design points:

- **`None` is always allowed** — "use the server default" is the operator's choice.
- **The tier is read from the database**, not the JWT claim, so an upgrade/downgrade applies
  immediately instead of when the token expires.
- Entries can be exact names or provider wildcards (`"ollama:*"`), matched through the same
  `resolve()` the LLM registry uses — so `anthropic:claude-haiku-4-5` matches `claude-haiku-4-5`.
- The check runs **before** a stream starts: a 403 inside an SSE stream is hard for clients to handle.

## Metering

`record_usage()` writes one `UsageRecord` per billable run: tokens, model, estimated cost, month.
This phase closed two gaps — **streamed chats** (`/query/stream`, `/query/ws/chat`) and **agent
runs** (`/agents/execute`, `/agents/stream`) previously recorded nothing.

Costs come from `MODEL_PRICING` (USD per million tokens, input/output), matched by **longest
prefix**. The old first-match lookup would have priced `claude-opus-5-5` using the `claude-opus-5`
entry. Unknown models (self-hosted) cost `0.0` rather than a guess.

Keep `MODEL_PRICING` current: vendors change prices, and a stale table under-bills silently.
Cached prompt tokens bill at a fraction of input price; `Usage.cached_input_tokens` is recorded on
every response so the estimate can be refined to account for it.

## Recipe — client-specific commercials

- **Per-tenant model allowlists** (an enterprise customer pays for Opus only): move `models` from
  `TierLimits` to a `tenants.allowed_models` column and read it in `assert_model_allowed`.
- **Budgets:** sum `UsageRecord.cost_usd` for the tenant's current period before running; reject or
  downgrade to `DEFAULT_FAST_MODEL` when over budget.
- **Chargeback:** `resource_type` (`query` / `agent`) plus `user_id` lets a client split cost by team.

## Vocabulary

*entitlements, allowlist, metering, chargeback, cost attribution, price table drift.*
