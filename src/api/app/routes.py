from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session
from uuid import UUID
from fastapi import UploadFile, File

from app.db import get_db
from app.models import Document, Job, JobStatus
from app.schemas import (
    DocumentCreate,
    DocumentCreateResponse,
    JobStatusResponse,
    DocumentUploadResponse,
)
from app.storage import get_minio_client

router = APIRouter()


@router.post("/documents", response_model=DocumentCreateResponse)
def create_document(payload: DocumentCreate, db: Session = Depends(get_db)):
    doc = Document(filename=payload.filename, content_type=payload.content_type)
    db.add(doc)
    db.flush()

    job = Job(document_id=doc.id, status=JobStatus.QUEUED)
    db.add(job)
    db.commit()

    return DocumentCreateResponse(document_id=doc.id, job_id=job.id, status=job.status.value)


@router.post("/documents/upload", response_model=DocumentCreateResponse)
def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    from app.worker import parse_document

    content_type = file.content_type or "application/pdf"

    doc = Document(filename=file.filename, content_type=content_type)
    db.add(doc)
    db.flush()

    job = Job(document_id=doc.id, status=JobStatus.QUEUED)
    db.add(job)
    db.flush()  # get IDs without committing yet

    # Upload to MinIO before committing.
    # Stream directly to avoid loading the entire file into memory:
    # seek to end to measure size, then rewind for the actual upload.
    raw_key = f"raw/{doc.id}/{file.filename}"
    minio = get_minio_client()
    ensure_bucket(minio)
    file.file.seek(0, 2)
    file_size = file.file.tell()
    file.file.seek(0)
    minio.put_object(
        MINIO_BUCKET,
        raw_key,
        file.file,
        length=file_size,
        content_type=content_type,
    )

    db.commit()

    # Enqueue parse task after committing so worker can find the records.
    try:
        parse_document.apply_async(args=[str(job.id), str(doc.id), file.filename])
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Failed to enqueue parse task: {exc}")

    return DocumentCreateResponse(document_id=doc.id, job_id=job.id, status=job.status.value)


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_job(job_id: UUID, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return JobStatusResponse(
        job_id=job.id,
        document_id=job.document_id,
        status=job.status.value,
        error_message=job.error_message,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )

@router.post("/documents/upload", response_model=DocumentUploadResponse)
def upload_document(file: UploadFile = File(...), db: Session = Depends(get_db)):
    # MVP: accept PDFs only
    if file.content_type not in ("application/pdf",):
        raise HTTPException(status_code=400, detail="Only PDF uploads are supported for MVP")

    # 1) Create document + job (but don't commit yet)
    doc = Document(filename=file.filename, content_type=file.content_type)
    db.add(doc)
    db.flush()  # doc.id exists now

    job = Job(document_id=doc.id, status=JobStatus.QUEUED)
    db.add(job)
    db.flush()

    # 2) Upload raw PDF to MinIO
    client, bucket = get_minio_client()
    object_key = f"raw/{doc.id}/{file.filename}"

    # Ensure bucket exists (safe even if it already exists)
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)

    try:
        # Upload streaming body
        client.put_object(
            bucket_name=bucket,
            object_name=object_key,
            data=file.file,
            length=-1,
            part_size=10 * 1024 * 1024,  # 10MB parts
            content_type=file.content_type,
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to upload to object storage: {e}")

    # 3) Commit DB after storage succeeded
    db.commit()

    return DocumentUploadResponse(
        document_id=doc.id,
        job_id=job.id,
        status=job.status.value,
        s3_key=object_key,
    )
