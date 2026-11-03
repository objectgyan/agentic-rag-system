"""Live-DB test of resizing and re-embedding chunks.embedding (runs in CI; skips without a DB).

Everything happens inside one transaction that is rolled back — Postgres DDL is transactional,
so the shared test database is left exactly as it was.
"""

import os
import uuid

import pytest

pytest.importorskip("asyncpg")

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.services.rag import vector_schema  # noqa: E402


def _owner_url():
    url = os.getenv("DATABASE_SYNC_URL", "")
    return url.replace("postgresql://", "postgresql+asyncpg://", 1) if url.startswith("postgresql://") else None


@pytest.mark.asyncio
async def test_resize_then_reembed_round_trip():
    url = _owner_url()
    if not url:
        pytest.skip("DATABASE_SYNC_URL not set")
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            try:
                await conn.execute(text("SELECT 1"))
            except Exception as exc:  # pragma: no cover - environment dependent
                pytest.skip(f"database not reachable: {exc}")
            await conn.rollback()  # end the probe's implicit transaction before starting ours
            tx = await conn.begin()
            try:
                tenant, coll, doc = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
                await conn.execute(text("INSERT INTO tenants (id, name, slug) VALUES (:i, 'VS', :s)"),
                                   {"i": tenant, "s": f"vs-{tenant.hex[:8]}"})
                await conn.execute(text("INSERT INTO collections (id, tenant_id, name) VALUES (:c, :t, 'vs')"),
                                   {"c": coll, "t": tenant})
                await conn.execute(text(
                    "INSERT INTO documents (id, tenant_id, collection_id, filename, original_filename, doc_type) "
                    "VALUES (:d, :t, :c, 'f', 'f', 'txt')"), {"d": doc, "t": tenant, "c": coll})
                for i in range(3):
                    await conn.execute(text(
                        "INSERT INTO chunks (tenant_id, document_id, collection_id, content, chunk_index) "
                        "VALUES (:t, :d, :c, :content, :i)"),
                        {"t": tenant, "d": doc, "c": coll, "content": f"chunk {i}", "i": i})

                assert await vector_schema.resize(conn, 4) is None
                st = await vector_schema.status(conn)
                assert st["column_dimensions"] == 4
                assert st["chunks_with_embedding"] == 0

                async def fake_embed(texts):
                    return [[float(len(t)), 0.0, 1.0, 0.5] for t in texts]

                n = await vector_schema.reembed(conn, fake_embed, batch_size=2)
                assert n == st["chunks"]  # every chunk in the table, across all tenants
                st = await vector_schema.status(conn)
                assert st["chunks_with_embedding"] == st["chunks"]
                # the ANN index was rebuilt for the new size
                assert await conn.scalar(text(
                    "SELECT count(*) FROM pg_indexes WHERE indexname = 'ix_chunks_embedding'")) == 1
            finally:
                await tx.rollback()
    finally:
        await engine.dispose()
