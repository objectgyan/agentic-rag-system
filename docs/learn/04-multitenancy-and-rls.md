# 03 · Multi-tenancy & Row-Level Security (the keystone)

This is the most important design idea in the whole system. Get this and the rest makes sense.

## What "multi-tenant" means

One running application serves **many customer organizations (tenants)**, and tenant A must
**never** see tenant B's data. Three common ways to isolate tenants:

| Strategy | Isolation | Cost |
|----------|-----------|------|
| Database per tenant | Strongest | Expensive, hard to manage at scale |
| Schema per tenant | Strong | Many schemas to migrate |
| **Shared tables with a `tenant_id` column** | Depends entirely on *every query* filtering by `tenant_id` | Cheapest, but one missed `WHERE` = a data leak |

AgentRAG uses the **shared-table** model — every table has a `tenant_id` — and then adds a
database-enforced safety net so a forgotten filter can't leak data. That safety net is
**Row-Level Security (RLS)**.

## The problem with "just filter by tenant_id"

If isolation depends on developers remembering `WHERE tenant_id = @me` on every query, you're
one bug away from a breach. (.NET devs: EF Core global query filters help, but they can be
bypassed with `IgnoreQueryFilters()` or raw SQL.) We want the **database itself** to refuse to
return another tenant's rows, no matter what the app code does.

## How RLS works in Postgres

PostgreSQL can attach a **policy** to a table that automatically adds a condition to *every*
query. Our policy (created in the migrations) is, for each tenant table:

```sql
CREATE POLICY tenant_isolation_documents ON documents
USING (tenant_id::text = current_setting('app.current_tenant', true));
```

Read it as: *"a row is only visible if its `tenant_id` equals the session variable
`app.current_tenant`."*

- `current_setting('app.current_tenant', true)` reads a **configuration variable** on the
  current connection (a "GUC"). The `true` means "return NULL if it's not set" (instead of
  erroring).
- If the variable isn't set, the comparison is `tenant_id = NULL` → `NULL` → **the row is
  hidden**. So it **fails closed** (deny by default). Good.

So the app must, at the start of each request, tell the database "I am tenant X" by setting
that variable. That happens in the `get_current_user` dependency:

```python
# core/database.py
async def set_tenant_context(session, tenant_id):
    canonical = str(uuid.UUID(str(tenant_id)))          # validated as a real UUID first
    session.info["tenant_id"] = canonical               # remembered for later transactions
    if session.in_transaction():
        await session.execute(
            sa_text("SELECT set_config('app.current_tenant', :tenant_id, true)"),
            {"tenant_id": canonical},
        )
```

> Note the value is passed as a **bound parameter**, never string-concatenated into SQL — that
> avoids SQL injection (.NET: always use parameters, never interpolate; same rule here).

### Transaction-local, re-applied on every BEGIN

The third argument to `set_config` is `is_local`. It decides how long the setting lives:

| `is_local` | Lives until | Risk |
|---|---|---|
| `false` (session) | the connection closes | A pooled connection keeps tenant A after the request commits. The next request that checks it out starts as tenant A until it sets its own context. |
| `true` (transaction) | the next `COMMIT` / `ROLLBACK` | Nothing can leak through the pool, but the setting disappears mid-request. |

We use `true`. The catch is that many paths **commit and keep querying on the same session**:
the worker marks a document PROCESSING, commits, then writes chunks; login commits before the
audit-log insert; the query WebSocket commits once per message. After that first commit there is
no tenant set, so reads return zero rows and inserts fail the policy's `WITH CHECK`.

So the tenant is also stored on the session, and a SQLAlchemy event applies it again at the
start of every transaction:

```python
class TenantSession(Session):            # the sync session behind every AsyncSession
    ...

@event.listens_for(TenantSession, "after_begin")
def _apply_tenant_on_begin(session, transaction, connection):
    tenant_id = session.info.get("tenant_id")
    if tenant_id:
        connection.execute(_SET_TENANT_SQL, {"tenant_id": tenant_id})

AsyncSessionLocal = make_sessionmaker(engine)   # sync_session_class=TenantSession
```

That is why `set_tenant_context` only runs the SQL itself when the session is **already** in a
transaction: `after_begin` has fired for that one, so it must be set now. Otherwise the next
BEGIN takes care of it. Any session that touches tenant data must come from
`AsyncSessionLocal` / `make_sessionmaker()`. A plain `AsyncSession` has no listener and
fails closed after its first commit.

`tests/test_rls_integration.py` proves all three behaviors against live Postgres: the tenant
survives a commit, it does **not** survive the connection going back to the pool, and an
insert after a commit still passes `WITH CHECK`.

> **Client recipe:** the same pattern works for any per-request database setting (tenant, user
> id for audit triggers, `statement_timeout`). Make the setting transaction-local, remember it on
> the session, and re-apply it in `after_begin`. With PgBouncer in transaction-pooling mode,
> session-level settings aren't just leaky, they're wrong: consecutive transactions can run on
> different server connections. So this pattern is the only one that works there.

```mermaid
sequenceDiagram
    participant H as Handler (get_current_user)
    participant DB as Postgres
    H->>DB: SET LOCAL app.current_tenant = 'tenant-A' (each transaction)
    H->>DB: SELECT * FROM documents
    Note over DB: RLS rewrites it to<br/>... WHERE tenant_id = 'tenant-A'
    DB-->>H: only tenant-A rows
```

## The keystone gotcha — and the fix

Here's the subtle, critical part that was the headline bug in this codebase's history:

> **Postgres exempts a table's owner (and any superuser) from RLS** unless you *also* tell it to
> FORCE the policy. And even FORCE doesn't constrain a superuser.

The app originally connected to Postgres as the `agentrag` role, which **owned the tables and
is a superuser**. So RLS policies existed but were **completely ignored** — isolation was
silently resting on app-level `WHERE` clauses only. The policies were decoration.

The fix (two parts, see migration `004_app_role_force_rls.py`):

1. **A dedicated, non-owner, non-superuser role** — `agentrag_app` — that the **app and worker
   connect as at runtime**. Because it doesn't own the tables and isn't a superuser, RLS
   policies actually apply to it.
2. **`ALTER TABLE ... FORCE ROW LEVEL SECURITY`** on every tenant table — defense in depth.

Migrations still run as the owner `agentrag` (DDL needs ownership). So:

```mermaid
flowchart TB
    subgraph Runtime
        APP[FastAPI app] -->|connects as agentrag_app<br/>RLS APPLIES| PG[(Postgres)]
        WORKER[Celery worker] -->|connects as agentrag_app<br/>RLS APPLIES| PG
    end
    subgraph DDL
        ALEMBIC[Alembic migrations] -->|connects as owner agentrag<br/>RLS bypass, DDL only| PG
    end
```

This is wired purely through **two connection strings** in `docker-compose.yml`:
- `DATABASE_URL` (the app/worker, async) → `agentrag_app`
- `DATABASE_SYNC_URL` (Alembic) → `agentrag`

**The lesson worth tattooing on your brain:** *a multi-tenant guarantee is only as real as the
database role the app connects as.* RLS policies are inert until the connecting role is a
non-owner, non-superuser role subject to them.

## Defense in depth — two layers

The app uses **both** layers, on purpose:

1. **App-level checks** (the "gate") — e.g. `assert_collections_accessible` in
   `api/deps/access.py` returns a clear `403`/`404` when you ask for a collection you can't
   access. This gives good error messages and works even if RLS were misconfigured.
2. **RLS** (the "net") — even if a developer forgets a filter, the database returns nothing for
   the wrong tenant.

Belt *and* suspenders. Never rely on one layer for a security-critical invariant.

## Where tenant_id comes from

It's a **claim inside the JWT** the user presents. When they log in, the token is signed with
`tenant_id`, `sub` (user id), `tier`, and `role`. The middleware reads it; the `get_current_user`
dependency re-validates it and sets the RLS context. So the chain is:

`login → signed JWT(tenant_id) → each request carries it → dependency sets DB session variable →
RLS scopes every query`.

## One subtlety to remember (connection pooling)

The tenant variable is set **per database connection/session**. With connection pooling, you
must set it at the start of *every* request that touches tenant data — which is exactly what the
`get_current_user` dependency guarantees, because (almost) every tenant-data route depends on
it. The few paths that don't (like `login`, which writes an audit log before auth resolves) set
the context explicitly. If you ever add a route that touches tenant tables without
`get_current_user`, RLS will return **zero rows** (fail-closed) — a loud, safe failure.

---

Prev: [Request lifecycle](03-request-lifecycle.md) · Next: [Auth →](05-auth.md)
