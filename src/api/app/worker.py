from __future__ import annotations

import io
import json
import logging
import os

import pdfplumber
from celery import Celery
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Job, JobStatus
from app.storage import MINIO_BUCKET, ensure_bucket, get_minio_client

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

celery_app = Celery("sentinel", broker=REDIS_URL, backend=REDIS_URL)
celery_app.conf.task_serializer = "json"
celery_app.conf.result_serializer = "json"
celery_app.conf.accept_content = ["json"]


def _set_job_status(
    db: Session, job: Job, status: JobStatus, error_message: str | None = None
) -> None:
    from sqlalchemy import func

    job.status = status
    job.error_message = error_message
    job.updated_at = func.now()
    db.commit()


@celery_app.task(bind=True, max_retries=0)
def parse_document(self, job_id: str, doc_id: str, filename: str) -> None:
    """
    Parse step: fetch raw PDF from MinIO, extract text with pdfplumber,
    store extracted text back to MinIO as parsed/{doc_id}/extracted.txt.
    Idempotent: safe to re-run, will overwrite previous output.
    """
    log = logging.getLogger(__name__)

    db: Session = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job is None:
            log.error("job_id=%s not found in DB", job_id)
            return

        log.info("job_id=%s doc_id=%s status=RUNNING", job_id, doc_id)
        _set_job_status(db, job, JobStatus.RUNNING)

        minio = get_minio_client()
        ensure_bucket(minio)

        raw_key = f"raw/{doc_id}/{filename}"
        log.info("job_id=%s fetching object key=%s", job_id, raw_key)

        response = minio.get_object(MINIO_BUCKET, raw_key)
        try:
            pdf_bytes = response.read()
        finally:
            response.close()
            response.release_conn()

        # Extract text page by page, preserving boundaries
        pages: list[str] = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                pages.append(f"--- Page {page_num} ---\n{text}")
                log.info(
                    "job_id=%s doc_id=%s page=%d chars=%d",
                    job_id,
                    doc_id,
                    page_num,
                    len(text),
                )

        extracted_text = "\n\n".join(pages)
        text_bytes = extracted_text.encode("utf-8")

        parsed_key = f"parsed/{doc_id}/extracted.txt"
        minio.put_object(
            MINIO_BUCKET,
            parsed_key,
            io.BytesIO(text_bytes),
            length=len(text_bytes),
            content_type="text/plain",
        )
        log.info(
            "job_id=%s doc_id=%s stored parsed text key=%s total_chars=%d",
            job_id,
            doc_id,
            parsed_key,
            len(text_bytes),
        )

        _set_job_status(db, job, JobStatus.SUCCEEDED)
        log.info("job_id=%s status=SUCCEEDED", job_id)

    except Exception as exc:
        log.exception("job_id=%s parse failed: %s", job_id, exc)
        try:
            job = db.get(Job, job_id)
            if job:
                _set_job_status(db, job, JobStatus.FAILED, error_message=str(exc)[:1024])
        except Exception:
            pass
        raise
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=0)
def redact_document(self, job_id: str, doc_id: str) -> None:
    """
    Redact step: fetch parsed/{doc_id}/extracted.txt from MinIO, run PII
    detection and replacement via redact_text(), then store:
      - redacted/{doc_id}/redacted.txt        – scrubbed text
      - redacted/{doc_id}/redaction_report.json – audit trail

    Idempotent: overwrites previous output on re-run.
    The original parsed/{doc_id}/extracted.txt is never modified.
    """
    from collections import Counter

    from app.redactor import redact_text

    log = logging.getLogger(__name__)

    db: Session = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job is None:
            log.error("job_id=%s not found in DB", job_id)
            return

        log.info("job_id=%s doc_id=%s redact status=RUNNING", job_id, doc_id)
        _set_job_status(db, job, JobStatus.RUNNING)

        minio = get_minio_client()
        ensure_bucket(minio)

        parsed_key = f"parsed/{doc_id}/extracted.txt"
        log.info("job_id=%s fetching object key=%s", job_id, parsed_key)

        response = minio.get_object(MINIO_BUCKET, parsed_key)
        try:
            raw_text = response.read().decode("utf-8")
        finally:
            response.close()
            response.release_conn()

        redacted_text, audit = redact_text(raw_text)

        # Log entity counts for observability.
        counts = Counter(entry["entity_type"] for entry in audit)
        counts_str = " ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        log.info("job_id=%s found %s", job_id, counts_str or "no PII")

        # Store redacted text.
        redacted_bytes = redacted_text.encode("utf-8")
        redacted_key = f"redacted/{doc_id}/redacted.txt"
        minio.put_object(
            MINIO_BUCKET,
            redacted_key,
            io.BytesIO(redacted_bytes),
            length=len(redacted_bytes),
            content_type="text/plain",
        )
        log.info("job_id=%s stored redacted text key=%s", job_id, redacted_key)

        # Store redaction report (audit trail).
        report_bytes = json.dumps(audit, ensure_ascii=False, indent=2).encode("utf-8")
        report_key = f"redacted/{doc_id}/redaction_report.json"
        minio.put_object(
            MINIO_BUCKET,
            report_key,
            io.BytesIO(report_bytes),
            length=len(report_bytes),
            content_type="application/json",
        )
        log.info("job_id=%s stored redaction report key=%s entities=%d", job_id, report_key, len(audit))

        _set_job_status(db, job, JobStatus.SUCCEEDED)
        log.info("job_id=%s status=SUCCEEDED", job_id)

    except Exception as exc:
        log.exception("job_id=%s redact failed: %s", job_id, exc)
        try:
            job = db.get(Job, job_id)
            if job:
                _set_job_status(db, job, JobStatus.FAILED, error_message=str(exc)[:1024])
        except Exception:
            pass
        raise
    finally:
        db.close()
