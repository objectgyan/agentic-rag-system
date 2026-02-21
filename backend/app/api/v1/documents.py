"""Document upload and management endpoints."""

import uuid
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from uuid import UUID
from typing import List
from app.core.database import get_db
from app.core.storage import upload_file
from app.models.user import User
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.collection import Collection, CollectionVisibility
from app.schemas.document import DocumentUploadResponse, DocumentResponse, DocumentURLIngest
from app.api.deps.auth import get_current_user, require_member
from app.core.audit import create_audit_log

router = APIRouter()

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
    try:
        # Verify collection access
        result = await db.execute(
            select(Collection).where(
                Collection.id == collection_id,
                Collection.tenant_id == user.tenant_id,
            )
        )
        collection = result.scalar_one_or_none()
        if not collection:
            raise HTTPException(status_code=404, detail="Collection not found")
        if collection.visibility == "private" and collection.owner_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied to private collection")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[DEBUG] Error during collection verification: {type(e).__name__}: {e}")
        raise

    try:
        print(f"[DEBUG] Starting upload for {len(files)} files")
        responses = []
        doc_ids = []
        for file in files:
            print(f"[DEBUG] Processing file: {file.filename}")
            content = await file.read()
            print(f"[DEBUG] File read complete, size: {len(content)}")
            file_id = str(uuid.uuid4())
            storage_path = f"{user.tenant_id}/{collection_id}/{file_id}/{file.filename}"

            # Upload to object storage
            print(f"[DEBUG] Uploading to MinIO: {storage_path}")
            upload_file(storage_path, content, file.content_type or "application/octet-stream")
            print(f"[DEBUG] MinIO upload complete")

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
            print(f"[DEBUG] Document added to session")
            await db.flush()
            print(f"[DEBUG] Database flushed, doc_id: {doc.id}")
            
            doc_ids.append(str(doc.id))
            responses.append(DocumentUploadResponse.model_validate(doc))

        print(f"[DEBUG] About to commit transaction")
        await db.commit()
        print(f"[DEBUG] Transaction committed successfully")
        
        # Create audit log
        await create_audit_log(
            db=db,
            user=user,
            action="documents.uploaded",
            resource_type="collection",
            resource_id=str(collection_id),
            details={"count": len(files), "filenames": [f.filename for f in files]},
        )
        
        # Trigger async processing after commit
        print(f"[DEBUG] Queuing Celery tasks for {len(doc_ids)} documents")
        from app.services.processing.tasks import process_document
        for doc_id in doc_ids:
            try:
                print(f"[DEBUG] Queuing task for {doc_id}")
                process_document.delay(doc_id)
                print(f"[DEBUG] Task queued for {doc_id}")
            except Exception as e:
                # Log but don't fail the request if task queuing fails
                print(f"Failed to queue task for document {doc_id}: {e}")
        
        print(f"[DEBUG] Returning response")
        return responses
    except Exception as e:
        print(f"[DEBUG] EXCEPTION during upload: {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@router.post("/url", response_model=DocumentUploadResponse)
async def ingest_url(
    req: DocumentURLIngest,
    user: User = Depends(require_member),
    db: AsyncSession = Depends(get_db),
):
    """Ingest a document from a URL."""
    result = await db.execute(
        select(Collection).where(
            Collection.id == req.collection_id,
            Collection.tenant_id == user.tenant_id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Collection not found")

    doc = Document(
        tenant_id=user.tenant_id,
        collection_id=req.collection_id,
        filename=req.url.split("/")[-1] or "webpage",
        original_filename=req.url,
        doc_type=DocumentType.URL.value,
        status=DocumentStatus.PENDING.value,
        source_url=req.url,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    from app.services.processing.tasks import process_document
    process_document.delay(str(doc.id))

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

    # Delete from storage
    if doc.storage_path:
        try:
            from app.core.storage import delete_file
            delete_file(doc.storage_path)
        except Exception:
            pass

    await db.delete(doc)
    await db.commit()
    return {"status": "deleted"}
