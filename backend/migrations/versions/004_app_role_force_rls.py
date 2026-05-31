"""Non-owner app role + FORCE ROW LEVEL SECURITY (F9)

This is the migration that makes RLS actually enforce. Until now every RLS policy
was dead weight, because the app connected as `agentrag`, which **owns** the tables,
and Postgres exempts table owners from RLS. Two changes fix that:

1. Create a dedicated `agentrag_app` LOGIN role that owns nothing. A non-owner role
   is subject to RLS, so the tenant_isolation policies finally apply to it. The app
   and Celery worker connect as this role (via DATABASE_URL in docker-compose);
   Alembic keeps using the owner `agentrag` (via DATABASE_SYNC_URL) because DDL needs
   ownership.
2. FORCE ROW LEVEL SECURITY on every tenant table, so even the owner is subject to
   the policies — belt and suspenders, and it means a stray owner connection can't
   leak data either.

The migration itself runs as the owner/superuser `agentrag`, which can CREATE ROLE
and FORCE RLS.

NOTE (dev convenience): the role password is a fixed dev default here. In a real
deployment, create the role out-of-band with a secret and grant it, or rotate this
password — do not ship 'agentrag_app' as a production credential.

Revision ID: 004_app_role_force_rls
Revises: 003_apikey_prefix_index
Create Date: 2026-05-31
"""
from typing import Sequence, Union
from alembic import op

revision: str = "004_app_role_force_rls"
down_revision: Union[str, None] = "003_apikey_prefix_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

APP_ROLE = "agentrag_app"
APP_ROLE_PASSWORD = "agentrag_app"  # dev default — see module docstring

# Every table carrying tenant_id and an RLS policy (7 from 001 + messages from 002).
TENANT_TABLES = [
    "documents",
    "chunks",
    "collections",
    "conversations",
    "api_keys",
    "audit_logs",
    "usage_records",
    "messages",
]


def upgrade() -> None:
    # 1. Create the non-owner application role (idempotent).
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_ROLE_PASSWORD}';
            END IF;
        END
        $$;
        """
    )

    # 2. Grant runtime privileges (no ownership, no DDL). Default privileges cover
    #    tables created by future migrations (which run as the owner agentrag).
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {APP_ROLE}"
    )

    # 3. FORCE RLS so the policies apply to everyone, owner included.
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")

    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {APP_ROLE}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE USAGE, SELECT ON SEQUENCES FROM {APP_ROLE}"
    )
    op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {APP_ROLE}")
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {APP_ROLE}")
    op.execute(f"REVOKE ALL ON SCHEMA public FROM {APP_ROLE}")
    op.execute(f"DROP ROLE IF EXISTS {APP_ROLE}")
