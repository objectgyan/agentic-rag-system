"""Chunks full-text search: content_tsv generated column + GIN index (item 5)

Moves sparse retrieval off the in-Python BM25 path (which loaded up to 1000 rows per query and
scored them in-process — a scaling cliff) into Postgres full-text search.

``content_tsv`` is a STORED generated column: ``to_tsvector('english', content)`` is immutable, so
Postgres computes it for every existing row when the column is added and keeps it in sync on every
write — no worker or model change needed. A GIN index makes ``@@`` / ``ts_rank`` lookups scale.

The column is added by the owner role (Alembic uses DATABASE_SYNC_URL); the agentrag_app role's
existing table-level SELECT on chunks already covers the new column and index.

Revision ID: 006_chunks_fts
Revises: 005_knowledge_graph
Create Date: 2026-07-05
"""
from typing import Sequence, Union

from alembic import op

revision: str = "006_chunks_fts"
down_revision: Union[str, None] = "005_knowledge_graph"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE chunks ADD COLUMN content_tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('english', content)) STORED"
    )
    op.execute("CREATE INDEX ix_chunks_content_tsv ON chunks USING gin (content_tsv)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_content_tsv")
    op.execute("ALTER TABLE chunks DROP COLUMN IF EXISTS content_tsv")
