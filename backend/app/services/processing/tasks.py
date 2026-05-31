"""Celery tasks for async document processing."""

import asyncio
import logging
import random
from datetime import datetime, timezone
from typing import Optional

import app.models  # noqa: F401  -- ensure SQLAlchemy relationships are configured
from app.core.celery_app import celery_app
from app.core.config import settings

logger = logging.getLogger(__name__)

RETRY_BASE_DELAY_SECONDS = 10
RETRY_MAX_DELAY_SECONDS = 300


def _retry_countdown(retries: int) -> int:
    """Exponential backoff with equal jitter for failed processing (F13).

    ``retries`` is how many retries were already attempted (0 on the first failure),
    so the delay window doubles each attempt up to a cap; the jitter spreads retries
    out so many simultaneous failures don't hammer the same upstream in lockstep.
    """
    window = min(RETRY_MAX_DELAY_SECONDS, RETRY_BASE_DELAY_SECONDS * (2 ** retries))
    return int(window / 2 + random.uniform(0, window / 2))


_worker_loop: Optional[asyncio.AbstractEventLoop] = None


def run_async(coro):
    """Run a coroutine on a single event loop reused for the worker process's lifetime.

    The previous version created and closed a fresh loop per task. Async HTTP clients
    (OpenAI/httpx) hold connections in a pool bound to the loop they were created on;
    closing the loop out from under them raised "Event loop is closed" during cleanup
    and could wedge the worker so later tasks hung. Reusing one persistent loop per
    prefork process is the standard pattern and keeps those pooled connections valid.
    """
    global _worker_loop
    if _worker_loop is None or _worker_loop.is_closed():
        _worker_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_worker_loop)
    return _worker_loop.run_until_complete(coro)


@celery_app.task(bind=True, max_retries=3)
def process_document(self, document_id: str, tenant_id: str):
    """Process a document: extract content, chunk, embed, and index."""
    run_async(_process_document_async(self, document_id, tenant_id))


async def _process_document_async(task, document_id: str, tenant_id: str):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine(settings.database_url, pool_size=5)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with Session() as db:
            await _run_processing(task, db, document_id, tenant_id)
    finally:
        # Always dispose the per-task engine — including on the retry path — or the
        # connection pool leaks on every failed attempt (the old code only disposed
        # on success) (F13).
        await engine.dispose()


async def _run_processing(task, db, document_id: str, tenant_id: str):
    from sqlalchemy import select

    from app.core.database import set_tenant_context
    from app.models.chunk import Chunk
    from app.models.collection import Collection
    from app.models.document import Document, DocumentStatus

    try:
        # Set tenant context BEFORE any tenant-scoped query (F10). The worker connects
        # as the restricted, RLS-subject role (F9): without this the document is
        # invisible and the chunk INSERTs fail the policy WITH CHECK. tenant_id is
        # supplied by the endpoint that enqueued this task.
        await set_tenant_context(db, tenant_id)

        # Load document
        result = await db.execute(select(Document).where(Document.id == document_id))
        doc = result.scalar_one_or_none()
        if not doc:
            return

        doc.status = DocumentStatus.PROCESSING
        await db.commit()

        # Extract content
        content = await _extract_content(doc)
        if not content or not content.text.strip():
            doc.status = DocumentStatus.FAILED
            doc.error_message = "No content could be extracted"
            await db.commit()
            return

        # Clean text - remove null bytes and other problematic characters
        content.text = content.text.replace('\x00', '').replace('\0', '')
        # Also remove other control characters except newlines, tabs, and carriage returns
        import re
        content.text = re.sub(r'[\x01-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', content.text)

        # Update metadata
        if content.metadata:
            doc.page_count = content.metadata.get("page_count")
            doc.metadata_extra = content.metadata

        # Load collection config
        col_result = await db.execute(select(Collection).where(Collection.id == doc.collection_id))
        collection = col_result.scalar_one()

        # Chunk the content
        from app.services.rag.chunker import ChunkingService
        chunker = ChunkingService(
            strategy=collection.chunk_strategy,
            chunk_size=int(collection.chunk_size),
            chunk_overlap=int(collection.chunk_overlap),
        )
        text_chunks = chunker.chunk(content.text)

        logger.info(
            "chunked document %s into %d chunks", doc.original_filename, len(text_chunks)
        )

        # Embed chunks
        from app.services.rag.embedder import EmbeddingService
        embedder = EmbeddingService(model=collection.embedding_model)
        texts = [c.content for c in text_chunks]
        embeddings = await embedder.embed_texts(texts)

        # Store chunks
        for tc, embedding in zip(text_chunks, embeddings):
            # Clean chunk content to ensure no problematic characters
            clean_content = tc.content.replace('\x00', '').replace('\0', '')
            clean_section = tc.section_title.replace('\x00', '') if tc.section_title else None
            clean_parent = tc.parent_content.replace('\x00', '') if tc.parent_content else None

            chunk = Chunk(
                tenant_id=doc.tenant_id,
                document_id=doc.id,
                collection_id=doc.collection_id,
                content=clean_content,
                embedding=embedding,
                chunk_index=tc.index,
                token_count=tc.token_count,
                page_number=tc.page_number,
                section_title=clean_section,
                metadata_extra={"parent_content": clean_parent} if clean_parent else {},
            )
            db.add(chunk)

        # Knowledge-graph extraction (A3), opt-in per collection. Best-effort: a failure
        # here must not fail ingestion, since chunks/embeddings are the primary artifact.
        if getattr(collection, "enable_graph", False):
            try:
                from app.services.rag.graph import GraphService

                triples = await GraphService().extract_triples(content.text)
                n = await GraphService().store(
                    db, doc.tenant_id, doc.id, doc.collection_id, triples
                )
                logger.info("extracted %d knowledge-graph triples for %s", n, doc.original_filename)
            except Exception:
                logger.warning("knowledge-graph extraction failed for %s", doc.id, exc_info=True)

        doc.chunk_count = len(text_chunks)
        doc.status = DocumentStatus.COMPLETED
        doc.processed_at = datetime.now(timezone.utc)
        await db.commit()

    except Exception as e:
        await db.rollback()
        # Mark the document failed, then retry with exponential backoff + jitter (F13)
        # so a flaky upstream isn't hammered in lockstep across retries/workers.
        result = await db.execute(select(Document).where(Document.id == document_id))
        doc = result.scalar_one_or_none()
        if doc:
            doc.status = DocumentStatus.FAILED
            doc.error_message = str(e)[:1000]
            await db.commit()
        countdown = _retry_countdown(task.request.retries)
        logger.warning(
            "processing failed for document %s (attempt %d); retrying in %ds: %s",
            document_id, task.request.retries + 1, countdown, e,
        )
        raise task.retry(exc=e, countdown=countdown)


async def _extract_content(doc):
    """Extract content based on document type."""
    from app.services.processing.extractors import AudioExtractor, URLExtractor, VideoExtractor, get_extractor

    if doc.doc_type == "url":
        extractor = URLExtractor()
        return await extractor.extract(doc.source_url)

    if doc.doc_type == "audio":
        extractor = AudioExtractor()
        from app.core.storage import download_file
        data = download_file(doc.storage_path)
        return await extractor.extract(data, doc.original_filename)

    if doc.doc_type == "video":
        extractor = VideoExtractor()
        from app.core.storage import download_file
        data = download_file(doc.storage_path)
        return await extractor.extract(data, doc.original_filename)

    if doc.doc_type == "image":
        from app.services.processing.extractors import ImageExtractor
        extractor = ImageExtractor()
        from app.core.storage import download_file
        data = download_file(doc.storage_path)
        try:
            return await extractor.extract_with_vision(data)
        except Exception:
            return extractor.extract(data)

    extractor = get_extractor(doc.doc_type)
    if not extractor:
        from app.services.processing.extractors import TextExtractor
        extractor = TextExtractor()

    from app.core.storage import download_file
    data = download_file(doc.storage_path)
    return extractor.extract(data)
