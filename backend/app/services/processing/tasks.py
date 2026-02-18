"""Celery tasks for async document processing."""

import asyncio
from datetime import datetime, timezone
from app.core.celery_app import celery_app
from app.core.config import settings


def run_async(coro):
    """Helper to run async code in sync Celery tasks."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def process_document(self, document_id: str):
    """Process a document: extract content, chunk, embed, and index."""
    run_async(_process_document_async(self, document_id))


async def _process_document_async(task, document_id: str):
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
    from sqlalchemy import select

    engine = create_async_engine(settings.database_url, pool_size=5)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        try:
            from app.models.document import Document, DocumentStatus
            from app.models.chunk import Chunk

            # Load document
            result = await db.execute(select(Document).where(Document.id == document_id))
            doc = result.scalar_one_or_none()
            if not doc:
                return

            doc.status = DocumentStatus.PROCESSING.value
            await db.commit()

            # Extract content
            content = await _extract_content(doc)
            if not content or not content.text.strip():
                doc.status = DocumentStatus.FAILED.value
                doc.error_message = "No content could be extracted"
                await db.commit()
                return

            # Update metadata
            if content.metadata:
                doc.page_count = content.metadata.get("page_count")
                doc.metadata_extra = content.metadata

            # Load collection config
            from app.models.collection import Collection
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

            # Embed chunks
            from app.services.rag.embedder import EmbeddingService
            embedder = EmbeddingService(model=collection.embedding_model)
            texts = [c.content for c in text_chunks]
            embeddings = await embedder.embed_texts(texts)

            # Store chunks
            for i, (tc, embedding) in enumerate(zip(text_chunks, embeddings)):
                chunk = Chunk(
                    tenant_id=doc.tenant_id,
                    document_id=doc.id,
                    collection_id=doc.collection_id,
                    content=tc.content,
                    embedding=embedding,
                    chunk_index=tc.index,
                    token_count=tc.token_count,
                    page_number=tc.page_number,
                    section_title=tc.section_title,
                    metadata_extra={"parent_content": tc.parent_content} if tc.parent_content else {},
                )
                db.add(chunk)

            doc.chunk_count = len(text_chunks)
            doc.status = DocumentStatus.COMPLETED.value
            doc.processed_at = datetime.now(timezone.utc)
            await db.commit()

        except Exception as e:
            await db.rollback()
            # Update document status to failed
            result = await db.execute(select(Document).where(Document.id == document_id))
            doc = result.scalar_one_or_none()
            if doc:
                doc.status = DocumentStatus.FAILED.value
                doc.error_message = str(e)[:1000]
                await db.commit()
            raise task.retry(exc=e)

    await engine.dispose()


async def _extract_content(doc):
    """Extract content based on document type."""
    from app.services.processing.extractors import get_extractor, AudioExtractor, URLExtractor

    if doc.doc_type.value == "url":
        extractor = URLExtractor()
        return await extractor.extract(doc.source_url)

    if doc.doc_type.value == "audio":
        extractor = AudioExtractor()
        from app.core.storage import download_file
        data = download_file(doc.storage_path)
        return await extractor.extract(data, doc.original_filename)

    if doc.doc_type.value == "image":
        from app.services.processing.extractors import ImageExtractor
        extractor = ImageExtractor()
        from app.core.storage import download_file
        data = download_file(doc.storage_path)
        try:
            return await extractor.extract_with_vision(data)
        except Exception:
            return extractor.extract(data)

    extractor = get_extractor(doc.doc_type.value)
    if not extractor:
        from app.services.processing.extractors import TextExtractor
        extractor = TextExtractor()

    from app.core.storage import download_file
    data = download_file(doc.storage_path)
    return extractor.extract(data)
