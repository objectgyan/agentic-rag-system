"""Celery tasks for async document processing."""

import asyncio
from datetime import datetime, timezone
from app.core.celery_app import celery_app
from app.core.config import settings

# Import all models to ensure SQLAlchemy relationships are properly configured
import app.models  # noqa: F401


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
    from app.models.document import Document, DocumentStatus
    from app.models.chunk import Chunk
    from app.models.collection import Collection

    engine = create_async_engine(settings.database_url, pool_size=5)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        try:
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
            
            print(f"[DEBUG] Created {len(text_chunks)} chunks for document {doc.original_filename}")
            if text_chunks:
                max_tokens = max(tc.token_count for tc in text_chunks)
                print(f"[DEBUG] Largest chunk has {max_tokens} tokens")

            # Embed chunks
            from app.services.rag.embedder import EmbeddingService
            embedder = EmbeddingService(model=collection.embedding_model)
            texts = [c.content for c in text_chunks]
            embeddings = await embedder.embed_texts(texts)

            # Store chunks
            for i, (tc, embedding) in enumerate(zip(text_chunks, embeddings)):
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

            doc.chunk_count = len(text_chunks)
            doc.status = DocumentStatus.COMPLETED
            doc.processed_at = datetime.now(timezone.utc)
            await db.commit()

        except Exception as e:
            await db.rollback()
            # Update document status to failed
            result = await db.execute(select(Document).where(Document.id == document_id))
            doc = result.scalar_one_or_none()
            if doc:
                doc.status = DocumentStatus.FAILED
                doc.error_message = str(e)[:1000]
                await db.commit()
            raise task.retry(exc=e)

    await engine.dispose()


async def _extract_content(doc):
    """Extract content based on document type."""
    from app.services.processing.extractors import get_extractor, AudioExtractor, VideoExtractor, URLExtractor

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
