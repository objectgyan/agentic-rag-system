"""Inspect and migrate the vector column when switching embedding models.

    python -m app.cli.embeddings status
    python -m app.cli.embeddings resize --yes          # to DEFAULT_EMBEDDING_DIMENSIONS
    python -m app.cli.embeddings reembed [--batch 64]  # fill embeddings from stored chunk text

Typical switch (e.g. OpenAI -> a free local model):
    1. set DEFAULT_EMBEDDING_MODEL and DEFAULT_EMBEDDING_DIMENSIONS in .env
    2. resize --yes   (search returns nothing until step 3 finishes)
    3. reembed

In Docker: ``docker compose exec backend python -m app.cli.embeddings status``.
Connects as the table owner via DATABASE_SYNC_URL (see vector_schema.py for why).
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.core.config import settings
from app.services.rag import vector_schema


def _owner_engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    url = settings.database_sync_url
    if url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]
    return create_async_engine(url)


async def _status() -> int:
    engine = _owner_engine()
    try:
        async with engine.connect() as conn:
            st = await vector_schema.status(conn)
    finally:
        await engine.dispose()
    target = settings.default_embedding_dimensions
    print(f"embedding model     : {settings.default_embedding_model}")
    print(f"configured size     : {target}")
    print(f"column size         : {st['column_dimensions']}")
    print(f"chunks              : {st['chunks']} ({st['chunks_with_embedding']} embedded)")
    if st["column_dimensions"] != target:
        print("\nMISMATCH: run `resize --yes`, then `reembed`.")
        return 1
    if st["chunks_with_embedding"] < st["chunks"]:
        print("\nSome chunks have no embedding: run `reembed`.")
    return 0


async def _resize(yes: bool) -> int:
    target = settings.default_embedding_dimensions
    if not yes:
        print(f"This clears every stored embedding and resizes the column to vector({target}). "
              "Re-run with --yes to proceed, then run `reembed`.")
        return 2
    engine = _owner_engine()
    try:
        async with engine.begin() as conn:  # one transaction: all of it or none of it
            warning = await vector_schema.resize(conn, target)
    finally:
        await engine.dispose()
    print(f"Resized chunks.embedding to vector({target}); embeddings cleared. Now run `reembed`.")
    if warning:
        print(f"WARNING: {warning}")
    return 0


async def _reembed(batch: int) -> int:
    from app.services.rag.embedder import EmbeddingService

    embedder = EmbeddingService()
    engine = _owner_engine()
    try:
        async with engine.connect() as conn:
            st = await vector_schema.status(conn)
            if st["column_dimensions"] != settings.default_embedding_dimensions:
                print("Column size doesn't match the configured model — run `resize --yes` first.")
                return 1
            todo = st["chunks"] - st["chunks_with_embedding"]
            print(f"Embedding {todo} chunks with {embedder.model} ...")

            def progress(n: int) -> None:
                print(f"  {n}/{todo}")

            done = await vector_schema.reembed(conn, embedder.embed_texts, batch_size=batch, on_batch=progress)
            await conn.commit()
    finally:
        await engine.dispose()
    print(f"Done: {done} chunks embedded.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli.embeddings", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="compare the column size with the configured model")
    p_resize = sub.add_parser("resize", help="resize the column to DEFAULT_EMBEDDING_DIMENSIONS (clears embeddings)")
    p_resize.add_argument("--yes", action="store_true", help="confirm clearing all stored embeddings")
    p_re = sub.add_parser("reembed", help="embed every chunk that has no embedding")
    p_re.add_argument("--batch", type=int, default=64)
    args = parser.parse_args(argv)

    if args.cmd == "status":
        return asyncio.run(_status())
    if args.cmd == "resize":
        return asyncio.run(_resize(args.yes))
    return asyncio.run(_reembed(args.batch))


if __name__ == "__main__":
    sys.exit(main())
