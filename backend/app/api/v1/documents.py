"""Document upload and management endpoints."""

import logging
import uuid
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.access import assert_collection_accessible
from app.api.deps.auth import get_current_user, require_member
from app.core.audit import create_audit_log
from app.core.config import settings
from app.core.database import get_db
from app.core.storage import upload_file
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.user import User
from app.schemas.document import DocumentResponse, DocumentUploadResponse, DocumentURLIngest

logger = logging.getLogger(__name__)

router = APIRouter()


async def read_upload_capped(file: UploadFile, max_bytes: int) -> bytes:
    """Read an UploadFile into memory, aborting once it exceeds ``max_bytes`` (F7).

    ``await file.read()`` reads the entire upload with no bound — a single large file
    can exhaust memory/disk. Reading in chunks and stopping at the cap means a hostile
    upload is rejected with 413 after at most ``max_bytes`` are buffered, not gigabytes.
    """
    chunks: List[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)  # 1 MiB at a time
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File '{file.filename}' exceeds the "
                f"{max_bytes // (1024 * 1024)} MB upload limit",
            )
        chunks.append(chunk)
    return b"".join(chunks)

MIME_TO_DOCTYPE = {
    "application/pdf": DocumentType.PDF,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": DocumentType.DOCX,
    "text/plain": DocumentType.TXT,
    "text/markdown": DocumentType.MARKDOWN,
    "text/csv": DocumentType.CSV,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": DocumentType.XLSX,
    "text/html": DocumentType.HTML,
    "image/png": DocumentType.IMAGE,
    "image/jpeg": DocumentType.IMAGE,
    "image/webp": DocumentType.IMAGE,
    "audio/mpeg": DocumentType.AUDIO,
    "audio/wav": DocumentType.AUDIO,
    "audio/mp4": DocumentType.AUDIO,
    "video/mp4": DocumentType.VIDEO,
    "video/webm": DocumentType.VIDEO,
}


def detect_doc_type(mime: str, filename: str) -> DocumentType:
    if mime in MIME_TO_DOCTYPE:
        return MIME_TO_DOCTYPE[mime]
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    ext_map = {
        "pdf": DocumentType.PDF, "docx": DocumentType.DOCX, "txt": DocumentType.TXT,
        "md": DocumentType.MARKDOWN, "csv": DocumentType.CSV, "xlsx": DocumentType.XLSX,
        "html": DocumentType.HTML, "htm": DocumentType.HTML,
        "png": DocumentType.IMAGE, "jpg": DocumentType.IMAGE, "jpeg": DocumentType.IMAGE,
        "mp3": DocumentType.AUDIO, "wav": DocumentType.AUDIO, "m4a": DocumentType.AUDIO,
        "mp4": DocumentType.VIDEO, "webm": DocumentType.VIDEO,
    }
    return ext_map.get(ext, DocumentType.TXT)


@router.post("/upload", response_model=List[DocumentUploadResponse])
async def upload_documents(
    collection_id: UUID = Form(...),
    files: List[UploadFile] = File(...),
    user: User = Depends(require_member),
    db: AsyncSession = Depends(get_db),
):
    """Upload one or more documents to a collection."""
    # Verify collection access: tenant scope + private-owner enforcement (F2).
    await assert_collection_accessible(db, user, collection_id)

    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    responses = []
    doc_ids = []
    try:
        for file in files:
            content = await read_upload_capped(file, max_bytes)
            file_id = str(uuid.uuid4())
            storage_path = f"{user.tenant_id}/{collection_id}/{file_id}/{file.filename}"

            upload_file(storage_path, content, file.content_type or "application/octet-stream")

            doc_type = detect_doc_type(file.content_type or "", file.filename)
            doc = Document(
                tenant_id=user.tenant_id,
                collection_id=collection_id,
                filename=f"{file_id}_{file.filename}",
                original_filename=file.filename,
                doc_type=doc_type.value,
                status=DocumentStatus.PENDING.value,
                storage_path=storage_path,
                file_size=len(content),
                mime_type=file.content_type,
            )
            db.add(doc)
            await db.flush()
            doc_ids.append(str(doc.id))
            responses.append(DocumentUploadResponse.model_validate(doc))

        await db.commit()

        await create_audit_log(
            db=db,
            user=user,
            action="documents.uploaded",
            resource_type="collection",
            resource_id=str(collection_id),
            details={"count": len(files), "filenames": [f.filename for f in files]},
        )
    except HTTPException:
        # Client errors (403 access, 413 too-large): roll back and propagate as-is.
        await db.rollback()
        raise
    except Exception:
        # Unexpected server error: log the detail server-side, return a generic 500
        # (don't leak internal exception text to the client).
        await db.rollback()
        logger.exception(
            "document upload failed (tenant=%s collection=%s)", user.tenant_id, collection_id
        )
        raise HTTPException(status_code=500, detail="Upload failed")

    # Queue async processing only after the transaction has committed. A queue failure
    # must not fail the request — the document is saved and can be reprocessed.
    from app.services.processing.tasks import process_document
    for doc_id in doc_ids:
        try:
            process_document.delay(doc_id, str(user.tenant_id))
        except Exception:
            logger.warning("failed to queue processing for document %s", doc_id, exc_info=True)

    return responses


@router.post("/url", response_model=DocumentUploadResponse)
async def ingest_url(
    req: DocumentURLIngest,
    user: User = Depends(require_member),
    db: AsyncSession = Depends(get_db),
):
    """Ingest a document from a URL."""
    await assert_collection_accessible(db, user, req.collection_id)

    doc = Document(
        tenant_id=user.tenant_id,
        collection_id=req.collection_id,
        filename=req.url.split("/")[-1] or "webpage",
        original_filename=req.url,
        doc_type=DocumentType.URL.value,
        status=DocumentStatus.PENDING.value,
        source_url=req.url,
        # Crawl options the worker reads at extraction time.
        metadata_extra={"recursive": req.recursive, "max_pages": req.max_pages},
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    from app.services.processing.tasks import process_document
    process_document.delay(str(doc.id), str(user.tenant_id))

    return DocumentUploadResponse.model_validate(doc)


@router.get("", response_model=List[DocumentResponse])
@router.get("/", response_model=List[DocumentResponse])
async def list_documents(
    collection_id: UUID = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List documents, optionally filtered by collection."""
    query = select(Document).where(Document.tenant_id == user.tenant_id)
    if collection_id:
        query = query.where(Document.collection_id == collection_id)
    query = query.order_by(Document.created_at.desc())

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get document details and processing status."""
    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.tenant_id == user.tenant_id,
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.delete("/{document_id}")
async def delete_document(
    document_id: UUID,
    user: User = Depends(require_member),
    db: AsyncSession = Depends(get_db),
):
    """Delete a document and its chunks."""
    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.tenant_id == user.tenant_id,
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Delete from storage. Best-effort: a storage failure shouldn't block removing the
    # DB record, but it must be logged (orphaned object, not a silent loss) (F12).
    if doc.storage_path:
        try:
            from app.core.storage import delete_file
            delete_file(doc.storage_path)
        except Exception:
            logger.warning("failed to delete storage object %s", doc.storage_path, exc_info=True)

    await db.delete(doc)
    await db.commit()
    return {"status": "deleted"}
