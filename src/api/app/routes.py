from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.guardrails import validate_upload
from app.models import Batch, Document, Job, JobStatus
from app.schemas import (
    BatchCreateResponse,
    BatchStatusResponse,
    DocumentCreate,
    DocumentCreateResponse,
    JobStatusResponse,
    ReviewDecision,
    ReviewQueueItem,
    ReviewResponse,
)
from app.storage import BUCKET_NAME, ensure_bucket, get_storage_client

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
    validate_upload(file)

    from app.worker import parse_document, classify_document, authenticate_document, redact_document, extract_document
    from celery import chain

    content_type = file.content_type or "application/pdf"

    doc = Document(filename=file.filename, content_type=content_type)
    db.add(doc)
    db.flush()

    job = Job(document_id=doc.id, status=JobStatus.QUEUED)
    db.add(job)
    db.flush()

    # Upload to storage
    raw_key = f"raw/{doc.id}/{file.filename}"
    storage = get_storage_client()
    ensure_bucket(storage)
    file.file.seek(0, 2)
    file_size = file.file.tell()
    file.file.seek(0)
    storage.put_object(
        BUCKET_NAME,
        raw_key,
        file.file,
        length=file_size,
        content_type=content_type,
    )

    db.commit()

    try:
        chain(
            parse_document.s(str(job.id), str(doc.id), file.filename),
            classify_document.si(str(job.id), str(doc.id)),
            authenticate_document.si(str(job.id), str(doc.id), file.filename),
            redact_document.si(str(job.id), str(doc.id)),
            extract_document.si(str(job.id), str(doc.id)),
        ).apply_async()
    except Exception as exc:
        job.status = JobStatus.FAILED
        job.error_message = f"Enqueue failed: {str(exc)[:200]}"
        job.updated_at = func.now()
        db.commit()
        raise HTTPException(status_code=503, detail=f"Failed to enqueue pipeline: {exc}")

    return DocumentCreateResponse(document_id=doc.id, job_id=job.id, status=job.status.value)


@router.post("/batches/upload", response_model=BatchCreateResponse)
def upload_batch(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    """
    Industry-ready batch upload: processes multiple documents in a single
    logical transaction. Returns a batch_id to track the collective status.
    """
    from celery import chain
    from app.worker import (
        parse_document, classify_document, authenticate_document,
        redact_document, extract_document
    )

    batch = Batch()
    db.add(batch)
    db.flush()

    storage = get_storage_client()
    ensure_bucket(storage)

    responses = []

    for file in files:
        validate_upload(file)
        content_type = file.content_type or "application/pdf"

        doc = Document(batch_id=batch.id, filename=file.filename, content_type=content_type)
        db.add(doc)
        db.flush()

        job = Job(document_id=doc.id, status=JobStatus.QUEUED)
        db.add(job)
        db.flush()

        # Upload to storage
        raw_key = f"raw/{doc.id}/{file.filename}"
        file.file.seek(0, 2)
        file_size = file.file.tell()
        file.file.seek(0)

        storage.put_object(
            BUCKET_NAME,
            raw_key,
            file.file,
            length=file_size,
            content_type=content_type,
        )

        # Enqueue pipeline
        try:
            chain(
                parse_document.s(str(job.id), str(doc.id), file.filename),
                classify_document.si(str(job.id), str(doc.id)),
                authenticate_document.si(str(job.id), str(doc.id), file.filename),
                redact_document.si(str(job.id), str(doc.id)),
                extract_document.si(str(job.id), str(doc.id)),
            ).apply_async()
        except Exception as exc:
            job.status = JobStatus.FAILED
            job.error_message = f"Enqueue failed: {str(exc)[:200]}"
            job.updated_at = func.now()

        responses.append(DocumentCreateResponse(
            document_id=doc.id,
            job_id=job.id,
            status=job.status.value
        ))

    db.commit()
    return BatchCreateResponse(batch_id=batch.id, jobs=responses)


@router.get("/batches/{batch_id}", response_model=BatchStatusResponse)
def get_batch_status(batch_id: UUID, db: Session = Depends(get_db)):
    batch = db.get(Batch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    job_statuses = []
    for doc in batch.documents:
        job = db.query(Job).filter(Job.document_id == doc.id).order_by(Job.created_at.desc()).first()
        if job:
            job_statuses.append(JobStatusResponse(
                job_id=job.id,
                document_id=doc.id,
                filename=doc.filename,
                status=job.status.value,
                error_message=job.error_message,
                created_at=job.created_at,
                updated_at=job.updated_at,
            ))

    # Aggregate status
    statuses = [j.status for j in job_statuses]
    if not statuses:
        aggregate = "EMPTY"
    elif "FAILED" in statuses:
        aggregate = "FAILED"
    elif "RUNNING" in statuses or "QUEUED" in statuses:
        aggregate = "RUNNING"
    elif "NEEDS_REVIEW" in statuses:
        aggregate = "NEEDS_REVIEW"
    else:
        aggregate = "SUCCEEDED"

    return BatchStatusResponse(
        batch_id=batch.id,
        status=aggregate,
        jobs=job_statuses,
        created_at=batch.created_at
    )


@router.get("/jobs/review", response_model=list[ReviewQueueItem])
def list_review_queue(db: Session = Depends(get_db)):
    """Return all jobs currently sitting in NEEDS_REVIEW, with their metadata."""
    jobs = (
        db.query(Job, Document.filename)
        .join(Document, Job.document_id == Document.id)
        .filter(Job.status == JobStatus.NEEDS_REVIEW)
        .order_by(Job.created_at.asc())
        .all()
    )
    return [
        ReviewQueueItem(
            job_id=job.id,
            document_id=job.document_id,
            filename=filename,
            confidence_score=job.confidence_score,
            authentic=job.authentic,
            auth_confidence=job.auth_confidence,
            entity_count=job.entity_count,
            pii_types_found=job.pii_types_found,
            error_message=job.error_message,
            created_at=job.created_at,
        )
        for job, filename in jobs
    ]


@router.post("/jobs/{job_id}/review", response_model=ReviewResponse)
def submit_review(job_id: UUID, payload: ReviewDecision, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != JobStatus.NEEDS_REVIEW:
        raise HTTPException(status_code=409, detail=f"Job is not in NEEDS_REVIEW state (current: {job.status.value})")

    job.review_status = payload.decision
    job.status = JobStatus.SUCCEEDED if payload.decision == "approved" else JobStatus.FAILED
    if payload.decision == "rejected" and payload.notes:
        job.error_message = payload.notes[:1024]
    job.updated_at = func.now()
    db.commit()

    return ReviewResponse(job_id=job.id, status=job.status.value, review_status=job.review_status)


@router.get("/jobs/{job_id}/results")
def get_job_results(job_id: UUID, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    from app.models import Document
    doc = db.get(Document, job.document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    doc_id = str(doc.id)
    storage = get_storage_client()
    result: dict[str, Any] = {}

    def _fetch(key: str):
        try:
            data = storage.get_object(BUCKET_NAME, key)
            return json.loads(data.read().decode("utf-8"))
        except Exception:
            return None

    extraction = _fetch(f"extracted/{doc_id}/extraction.json")
    if extraction:
        result["extraction"] = extraction

    score = _fetch(f"extracted/{doc_id}/score_breakdown.json")
    if score:
        result["score_breakdown"] = score

    auth = _fetch(f"authenticated/{doc_id}/authenticity_report.json")
    if auth:
        result["authenticity_report"] = auth

    redaction = _fetch(f"redacted/{doc_id}/redaction_report.json")
    if redaction:
        result["redaction_report"] = redaction

    if not result:
        raise HTTPException(status_code=404, detail="No results available yet")

    return result


@router.get("/jobs/{job_id}/redacted-preview")
def get_redacted_preview(job_id: UUID, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    from app.models import Document
    doc = db.get(Document, job.document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    doc_id = str(doc.id)
    storage = get_storage_client()

    try:
        data = storage.get_object(BUCKET_NAME, f"redacted/{doc_id}/redacted.txt")
        redacted_text = data.read().decode("utf-8")
    except Exception:
        raise HTTPException(status_code=404, detail="Redacted text not yet available")

    redaction_report = []
    try:
        data = storage.get_object(BUCKET_NAME, f"redacted/{doc_id}/redaction_report.json")
        redaction_report = json.loads(data.read().decode("utf-8"))
    except Exception:
        pass

    return {"redacted_text": redacted_text, "redaction_report": redaction_report}


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
