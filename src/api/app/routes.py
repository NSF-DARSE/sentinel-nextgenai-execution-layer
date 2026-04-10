from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session
from uuid import UUID

from app.db import get_db
from app.models import Document, Job, JobStatus
from app.schemas import (
    DocumentCreate,
    DocumentCreateResponse,
    JobStatusResponse,
    ReviewQueueItem,
    ReviewDecision,
    ReviewResponse,
)
from app.storage import MINIO_BUCKET, ensure_bucket, get_minio_client
from app.guardrails import validate_upload

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

    from app.worker import parse_document

    content_type = file.content_type or "application/pdf"

    doc = Document(filename=file.filename, content_type=content_type)
    db.add(doc)
    db.flush()

    job = Job(document_id=doc.id, status=JobStatus.QUEUED)
    db.add(job)
    db.flush()  # get IDs without committing yet

    # Upload to MinIO before committing.
    # Seek to end to measure size, then rewind for the actual upload.
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

    # Enqueue parse → redact → extract chain after committing so workers can find the records.
    # .si() (immutable signature) ensures the None return of each step is not forwarded
    # to the next, so each task receives only its own explicit arguments.
    #
    # If enqueue fails (e.g. Redis is down), mark the job FAILED immediately so it
    # doesn't sit as QUEUED forever with no worker ever picking it up.
    try:
        from celery import chain
        from app.worker import authenticate_document, redact_document, extract_document
        chain(
            parse_document.s(str(job.id), str(doc.id), file.filename),
            authenticate_document.si(str(job.id), str(doc.id), file.filename),
            redact_document.si(str(job.id), str(doc.id)),
            extract_document.si(str(job.id), str(doc.id)),
        ).apply_async()
    except Exception as exc:
        from sqlalchemy import func
        job.status = JobStatus.FAILED
        job.error_message = f"Enqueue failed: {str(exc)[:200]}"
        job.updated_at = func.now()
        db.commit()
        raise HTTPException(status_code=503, detail=f"Failed to enqueue pipeline: {exc}")

    return DocumentCreateResponse(document_id=doc.id, job_id=job.id, status=job.status.value)


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
    """
    Approve or reject a NEEDS_REVIEW job.
    - approved → status becomes SUCCEEDED
    - rejected → status becomes FAILED
    """
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != JobStatus.NEEDS_REVIEW:
        raise HTTPException(status_code=409, detail=f"Job is not in NEEDS_REVIEW state (current: {job.status.value})")

    from sqlalchemy import func
    job.review_status = payload.decision
    job.status = JobStatus.SUCCEEDED if payload.decision == "approved" else JobStatus.FAILED
    if payload.decision == "rejected" and payload.notes:
        job.error_message = payload.notes[:1024]
    job.updated_at = func.now()
    db.commit()

    return ReviewResponse(job_id=job.id, status=job.status.value, review_status=job.review_status)


@router.get("/jobs/{job_id}/results")
def get_job_results(job_id: UUID, db: Session = Depends(get_db)):
    """Fetch all artifacts for a completed job from MinIO."""
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    from app.models import Document
    doc = db.get(Document, job.document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    doc_id = str(doc.id)
    minio = get_minio_client()
    result: dict = {}

    def _fetch(key: str):
        try:
            resp = minio.get_object(MINIO_BUCKET, key)
            try:
                return json.loads(resp.read().decode("utf-8"))
            finally:
                resp.close()
                resp.release_conn()
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
    """Return the redacted text with PII placeholders for the frontend diff view."""
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    from app.models import Document
    doc = db.get(Document, job.document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    doc_id = str(doc.id)
    minio = get_minio_client()

    try:
        resp = minio.get_object(MINIO_BUCKET, f"redacted/{doc_id}/redacted.txt")
        try:
            redacted_text = resp.read().decode("utf-8")
        finally:
            resp.close()
            resp.release_conn()
    except Exception:
        raise HTTPException(status_code=404, detail="Redacted text not yet available")

    redaction_report = []
    try:
        resp = minio.get_object(MINIO_BUCKET, f"redacted/{doc_id}/redaction_report.json")
        try:
            redaction_report = json.loads(resp.read().decode("utf-8"))
        finally:
            resp.close()
            resp.release_conn()
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
