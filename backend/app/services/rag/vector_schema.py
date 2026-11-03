"""The vector column's size, and changing it safely.

``chunks.embedding`` is ``vector(N)``: every stored embedding must have exactly N numbers, and N
is a property of the embedding *model* (1536 for OpenAI text-embedding-3-small, 384 for
BAAI/bge-small-en-v1.5, ...). So switching to a model with a different size is a schema change
plus a data change — vectors from two models are not comparable, so old ones must be replaced.

    status   — what the column is vs what the config expects        (read-only)
    resize   — drop the ANN index, clear embeddings, ALTER the type, rebuild the index
    reembed  — fill empty embeddings from the chunk text already in the DB (no re-extraction)

Used by migration 007 (automatic only when the table has no embeddings yet — never wipes data
at boot) and by the operator CLI ``python -m app.cli.embeddings``. Both connect as the table
OWNER (``DATABASE_SYNC_URL``): DDL needs ownership, and the owner role bypasses RLS so a
re-embed covers every tenant. The app's runtime role (``DATABASE_URL``) cannot do either.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from sqlalchemy import text

INDEX_NAME = "ix_chunks_embedding"
# pgvector's HNSW index supports up to 2,000 dimensions for the `vector` type.
HNSW_MAX_DIMENSIONS = 2000

_DIMENSION_SQL = (
    "SELECT atttypmod FROM pg_attribute "
    "WHERE attrelid = 'chunks'::regclass AND attname = 'embedding' AND NOT attisdropped"
)
_COUNTS_SQL = "SELECT count(*), count(embedding) FROM chunks"


def resize_statements(dimensions: int) -> List[str]:
    """The DDL that moves ``chunks.embedding`` to ``vector(dimensions)``, in order."""
    if not isinstance(dimensions, int) or dimensions < 1:
        raise ValueError(f"dimensions must be a positive integer, got {dimensions!r}")
    statements = [
        f"DROP INDEX IF EXISTS {INDEX_NAME}",
        # USING NULL: old vectors have the wrong size and come from another model anyway.
        f"ALTER TABLE chunks ALTER COLUMN embedding TYPE vector({dimensions}) USING NULL",
    ]
    if dimensions <= HNSW_MAX_DIMENSIONS:
        statements.append(f"CREATE INDEX {INDEX_NAME} ON chunks USING hnsw (embedding vector_cosine_ops)")
    return statements


def resize_warning(dimensions: int) -> Optional[str]:
    if dimensions > HNSW_MAX_DIMENSIONS:
        return (
            f"{dimensions} dimensions exceeds pgvector's HNSW limit ({HNSW_MAX_DIMENSIONS}); no ANN index "
            "was created, so vector search will scan every chunk. Prefer a smaller model, or reduce "
            "the output dimensions (text-embedding-3-large supports `dimensions=`)."
        )
    return None


async def status(conn) -> Dict[str, Optional[int]]:
    """Column size plus chunk counts. ``conn`` is a SQLAlchemy ``AsyncConnection``."""
    dims = (await conn.execute(text(_DIMENSION_SQL))).scalar()
    total, embedded = (await conn.execute(text(_COUNTS_SQL))).one()
    return {
        "column_dimensions": dims if dims and dims > 0 else None,
        "chunks": total,
        "chunks_with_embedding": embedded,
    }


async def resize(conn, dimensions: int) -> Optional[str]:
    """Apply ``resize_statements``. Run inside a transaction; returns a warning, if any."""
    for stmt in resize_statements(dimensions):
        await conn.execute(text(stmt))
    return resize_warning(dimensions)


async def reembed(
    conn,
    embed_texts: Callable,
    batch_size: int = 64,
    on_batch: Optional[Callable[[int], None]] = None,
) -> int:
    """Embed every chunk whose ``embedding`` is NULL, in batches. Returns the number updated.

    ``embed_texts`` is ``EmbeddingService().embed_texts`` (documents, not queries). Commit is the
    caller's job, so a failure mid-way can be rolled back or resumed — it only picks up NULLs.
    """
    done = 0
    last_id = None
    while True:
        params = {"n": batch_size}
        where = "embedding IS NULL"
        if last_id is not None:
            where += " AND id > :last_id"
            params["last_id"] = last_id
        rows = (await conn.execute(
            text(f"SELECT id, content FROM chunks WHERE {where} ORDER BY id LIMIT :n"), params
        )).all()
        if not rows:
            return done
        vectors = await embed_texts([r.content for r in rows])
        for row, vec in zip(rows, vectors):
            await conn.execute(
                text("UPDATE chunks SET embedding = CAST(:v AS vector) WHERE id = :id"),
                {"v": "[" + ",".join(f"{x:.8g}" for x in vec) + "]", "id": row.id},
            )
        done += len(rows)
        last_id = rows[-1].id
        if on_batch:
            on_batch(done)
