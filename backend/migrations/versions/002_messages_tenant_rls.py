"""Add tenant_id + RLS to messages (F3)

The messages table was created without a tenant_id and was the one tenant table
excluded from the RLS loop in 001_initial. It was isolated only transitively, via
its conversation_id FK. This migration gives it first-class tenant isolation:

- add messages.tenant_id, backfilled from the parent conversation
- make it NOT NULL so future inserts that forget the tenant fail loudly
- index it and attach the standard tenant_isolation RLS policy

(FORCE ROW LEVEL SECURITY for messages — and every other tenant table — is applied
together with the non-owner app role in migration 003 / F9.)

Revision ID: 002_messages_tenant_rls
Revises: 001_initial
Create Date: 2026-05-31
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "002_messages_tenant_rls"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add the column nullable so existing rows don't violate NOT NULL mid-migration.
    op.add_column("messages", sa.Column("tenant_id", UUID(as_uuid=True), nullable=True))

    # 2. Backfill from the parent conversation. Every message has a NOT NULL
    #    conversation_id FK, so this covers all rows.
    op.execute(
        """
        UPDATE messages m
        SET tenant_id = c.tenant_id
        FROM conversations c
        WHERE m.conversation_id = c.id
        """
    )

    # 3. Now enforce NOT NULL.
    op.alter_column("messages", "tenant_id", nullable=False)

    # 4. Index for tenant-scoped queries / RLS.
    op.create_index("ix_messages_tenant_id", "messages", ["tenant_id"])

    # 5. Enable RLS with the same policy shape as the other tenant tables.
    op.execute("ALTER TABLE messages ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_messages ON messages
        USING (tenant_id::text = current_setting('app.current_tenant', true))
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_messages ON messages")
    op.execute("ALTER TABLE messages DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_messages_tenant_id", table_name="messages")
    op.drop_column("messages", "tenant_id")
