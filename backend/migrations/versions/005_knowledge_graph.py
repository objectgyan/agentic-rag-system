"""Knowledge graph: graph_edges table + collections.enable_graph (A3)

A document collection can opt into knowledge-graph extraction. When enabled, the
ingestion worker extracts (subject, predicate, object) triples from each document and
stores them in graph_edges; at query time, facts whose entities appear in the question
are pulled in to augment the generated answer.

graph_edges carries tenant_id and gets the same RLS treatment as the other tenant tables
(enable + force + the tenant_isolation policy). It's created by the owner role, so the
agentrag_app default privileges from migration 004 already grant it CRUD.

Revision ID: 005_knowledge_graph
Revises: 004_app_role_force_rls
Create Date: 2026-05-31
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "005_knowledge_graph"
down_revision: Union[str, None] = "004_app_role_force_rls"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "collections",
        sa.Column("enable_graph", sa.Boolean, server_default=sa.text("false"), nullable=False),
    )

    op.create_table(
        "graph_edges",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()")),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "document_id",
            UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("collection_id", UUID(as_uuid=True), nullable=False),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("predicate", sa.String(500), nullable=False),
        sa.Column("object", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_graph_edges_tenant_id", "graph_edges", ["tenant_id"])
    op.create_index("ix_graph_edges_collection_id", "graph_edges", ["collection_id"])
    op.create_index("ix_graph_edges_document_id", "graph_edges", ["document_id"])
    # Case-insensitive lookups by entity (subject/object) using pg_trgm (enabled in 001).
    op.execute("CREATE INDEX ix_graph_edges_subject_trgm ON graph_edges USING gin (lower(subject) gin_trgm_ops)")
    op.execute("CREATE INDEX ix_graph_edges_object_trgm ON graph_edges USING gin (lower(object) gin_trgm_ops)")

    op.execute("ALTER TABLE graph_edges ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE graph_edges FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_graph_edges ON graph_edges
        USING (tenant_id::text = current_setting('app.current_tenant', true))
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_graph_edges ON graph_edges")
    op.drop_table("graph_edges")
    op.drop_column("collections", "enable_graph")
