from __future__ import annotations

import io
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
