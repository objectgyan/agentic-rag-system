"""Size chunks.embedding to the configured embedding model (DEFAULT_EMBEDDING_DIMENSIONS)

001 created ``embedding vector(1536)`` — OpenAI's text-embedding-3-small size — so any other model
(a free local one, Cohere, ...) failed at the first insert. This migration makes the column follow
the config:

- column already the configured size  -> nothing to do (every existing 1536 deployment);
- different size and NO embeddings yet -> resize automatically (fresh installs);
- different size WITH embeddings       -> stop, and say how to migrate. Embeddings from another
  model are useless with the new one, but wiping them implicitly on boot (alembic runs at startup)
  is not something a migration should ever do. The operator runs
  ``python -m app.cli.embeddings resize --yes`` then ``reembed``.

Changing models *after* this migration has run is handled the same way, by the CLI.

Revision ID: 007_embedding_dimensions
Revises: 006_chunks_fts
Create Date: 2026-11-03
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "007_embedding_dimensions"
down_revision: Union[str, None] = "006_chunks_fts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from app.core.config import settings
    from app.services.rag.vector_schema import resize_statements, resize_warning

    target = settings.default_embedding_dimensions
    bind = op.get_bind()
    current = bind.execute(text(
        "SELECT atttypmod FROM pg_attribute "
        "WHERE attrelid = 'chunks'::regclass AND attname = 'embedding' AND NOT attisdropped"
    )).scalar()
    if current == target:
        return

    embedded = bind.execute(text("SELECT count(embedding) FROM chunks")).scalar()
    if embedded:
        raise RuntimeError(
            f"chunks.embedding is vector({current}) holding {embedded} embeddings, but "
            f"DEFAULT_EMBEDDING_DIMENSIONS={target} (model {settings.default_embedding_model!r}). "
            "Either restore the previous embedding settings, or switch models deliberately: "
            "`python -m app.cli.embeddings resize --yes` then `python -m app.cli.embeddings reembed`."
        )
    for stmt in resize_statements(target):
        op.execute(stmt)
    warning = resize_warning(target)
    if warning:
        print(f"WARNING: {warning}")


def downgrade() -> None:
    # The size is configuration, not schema history: to go back, change the settings and resize.
    pass
