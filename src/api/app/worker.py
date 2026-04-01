from __future__ import annotations

import io
import json
import logging
import os
import time

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
    from app.metrics import record_step, record_step_failure, record_job_outcome

    log = logging.getLogger(__name__)
    start = time.time()

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

        pages: list[str] = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                pages.append(f"--- Page {page_num} ---\n{text}")
                log.info(
                    "job_id=%s doc_id=%s page=%d chars=%d",
                    job_id, doc_id, page_num, len(text),
                )

        extracted_text = "\n\n".join(pages)
        text_bytes = extracted_text.encode("utf-8")

        parsed_key = f"parsed/{doc_id}/extracted.txt"
        minio.put_object(
            MINIO_BUCKET, parsed_key, io.BytesIO(text_bytes),
            length=len(text_bytes), content_type="text/plain",
        )
        log.info(
            "job_id=%s doc_id=%s stored parsed text key=%s total_chars=%d",
            job_id, doc_id, parsed_key, len(text_bytes),
        )

        _set_job_status(db, job, JobStatus.SUCCEEDED)
        record_step("parse", time.time() - start)
        record_job_outcome("succeeded")
        log.info("job_id=%s status=SUCCEEDED", job_id)

    except Exception as exc:
        log.exception("job_id=%s parse failed: %s", job_id, exc)
        record_step("parse", time.time() - start)
        record_step_failure("parse")
        record_job_outcome("failed")
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
def authenticate_document(self, job_id: str, doc_id: str, filename: str) -> None:
    """
    Authenticate step: fetch raw PDF + parsed text from MinIO, run deterministic
    authenticity checks (document type, balance math, PDF metadata), store result as
    authenticated/{doc_id}/authenticity_report.json.

    Runs after parse, before redact. Never blocks the pipeline — flags issues
    in the report and lets the downstream approval process decide.
    Idempotent: overwrites previous output on re-run.
    """
    from app.authenticator import authenticate_document as run_checks
    from app.metrics import record_step, record_step_failure, record_job_outcome

    log = logging.getLogger(__name__)
    start = time.time()

    db: Session = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job is None:
            log.error("job_id=%s not found in DB", job_id)
            return

        log.info("job_id=%s doc_id=%s authenticate status=RUNNING", job_id, doc_id)
        _set_job_status(db, job, JobStatus.RUNNING)

        minio = get_minio_client()
        ensure_bucket(minio)

        # Fetch raw PDF bytes for metadata inspection
        raw_key = f"raw/{doc_id}/{filename}"
        response = minio.get_object(MINIO_BUCKET, raw_key)
        try:
            pdf_bytes = response.read()
        finally:
            response.close()
            response.release_conn()

        # Fetch parsed text for content checks
        parsed_key = f"parsed/{doc_id}/extracted.txt"
        response = minio.get_object(MINIO_BUCKET, parsed_key)
        try:
            parsed_text = response.read().decode("utf-8")
        finally:
            response.close()
            response.release_conn()

        report = run_checks(parsed_text, pdf_bytes)

        log.info(
            "job_id=%s authentic=%s confidence=%.2f type=%s flags=%d",
            job_id, report["authentic"], report["confidence"],
            report["document_type"], len(report["flags"]),
        )
        if report["flags"]:
            for flag in report["flags"]:
                log.warning("job_id=%s flag: %s", job_id, flag)

        report_bytes = json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8")
        report_key = f"authenticated/{doc_id}/authenticity_report.json"
        minio.put_object(
            MINIO_BUCKET, report_key, io.BytesIO(report_bytes),
            length=len(report_bytes), content_type="application/json",
        )
        log.info("job_id=%s stored authenticity report key=%s", job_id, report_key)

        # Quality checkpoint: persist authentication results to Postgres
        from sqlalchemy import func
        job.document_type  = report["document_type"]
        job.authentic      = report["authentic"]
        job.auth_confidence = report["confidence"]
        job.updated_at     = func.now()
        db.commit()

        _set_job_status(db, job, JobStatus.SUCCEEDED)
        record_step("authenticate", time.time() - start)
        record_job_outcome("succeeded")
        log.info("job_id=%s status=SUCCEEDED", job_id)

    except Exception as exc:
        log.exception("job_id=%s authenticate failed: %s", job_id, exc)
        record_step("authenticate", time.time() - start)
        record_step_failure("authenticate")
        record_job_outcome("failed")
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
    from app.metrics import record_step, record_step_failure, record_entities, record_job_outcome

    log = logging.getLogger(__name__)
    start = time.time()

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
        response = minio.get_object(MINIO_BUCKET, parsed_key)
        try:
            raw_text = response.read().decode("utf-8")
        finally:
            response.close()
            response.release_conn()

        redacted_text, audit = redact_text(raw_text)

        counts = Counter(entry["entity_type"] for entry in audit)
        counts_str = " ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        log.info("job_id=%s found %s", job_id, counts_str or "no PII")

        # Quality checkpoint: persist redaction results to Postgres
        from sqlalchemy import func as sqlfunc
        job.entity_count    = len(audit)
        job.pii_types_found = ",".join(sorted(counts.keys())) or None
        job.updated_at      = sqlfunc.now()
        db.commit()

        redacted_bytes = redacted_text.encode("utf-8")
        redacted_key = f"redacted/{doc_id}/redacted.txt"
        minio.put_object(
            MINIO_BUCKET, redacted_key, io.BytesIO(redacted_bytes),
            length=len(redacted_bytes), content_type="text/plain",
        )

        report_bytes = json.dumps(audit, ensure_ascii=False, indent=2).encode("utf-8")
        report_key = f"redacted/{doc_id}/redaction_report.json"
        minio.put_object(
            MINIO_BUCKET, report_key, io.BytesIO(report_bytes),
            length=len(report_bytes), content_type="application/json",
        )
        log.info("job_id=%s stored redaction report key=%s entities=%d", job_id, report_key, len(audit))

        _set_job_status(db, job, JobStatus.SUCCEEDED)
        record_step("redact", time.time() - start)
        record_entities(audit)
        record_job_outcome("succeeded")
        log.info("job_id=%s status=SUCCEEDED", job_id)

    except Exception as exc:
        log.exception("job_id=%s redact failed: %s", job_id, exc)
        record_step("redact", time.time() - start)
        record_step_failure("redact")
        record_job_outcome("failed")
        try:
            job = db.get(Job, job_id)
            if job:
                _set_job_status(db, job, JobStatus.FAILED, error_message=str(exc)[:1024])
        except Exception:
            pass
        raise
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60, rate_limit="10/m")
def extract_document(self, job_id: str, doc_id: str) -> None:
    """
    LLM extraction step: fetch redacted/{doc_id}/redacted.txt from MinIO,
    send to Gemini via extractor.extract_from_redacted(), scan output for
    PII leaks, then store:
      - extracted/{doc_id}/extraction.json        – structured risk profile
      - extracted/{doc_id}/extraction_meta.json   – model/prompt versioning + token usage

    Guarantee: the LLM only ever receives the redacted text artifact.
    The raw PDF and parsed text are never touched by this task.
    Idempotent: overwrites previous output on re-run.
    """
    from app.extractor import extract_from_redacted, PROMPT_VERSION, MODEL
    from app.metrics import record_step, record_step_failure, record_job_outcome, record_pii_leak_block

    log = logging.getLogger(__name__)
    start = time.time()

    db: Session = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job is None:
            log.error("job_id=%s not found in DB", job_id)
            return

        log.info("job_id=%s doc_id=%s extract status=RUNNING", job_id, doc_id)
        _set_job_status(db, job, JobStatus.RUNNING)

        minio = get_minio_client()
        ensure_bucket(minio)

        redacted_key = f"redacted/{doc_id}/redacted.txt"
        response = minio.get_object(MINIO_BUCKET, redacted_key)
        try:
            redacted_text = response.read().decode("utf-8")
        finally:
            response.close()
            response.release_conn()

        log.info(
            "job_id=%s doc_id=%s redacted_chars=%d sending to LLM model=%s",
            job_id, doc_id, len(redacted_text), MODEL,
        )

        result = extract_from_redacted(redacted_text)

        meta = result.pop("_meta", {})
        meta["prompt_version"] = PROMPT_VERSION
        meta["model"] = MODEL
        meta["doc_id"] = doc_id
        meta["job_id"] = job_id

        extraction_bytes = json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8")
        extraction_key = f"extracted/{doc_id}/extraction.json"
        minio.put_object(
            MINIO_BUCKET, extraction_key, io.BytesIO(extraction_bytes),
            length=len(extraction_bytes), content_type="application/json",
        )

        meta_bytes = json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8")
        meta_key = f"extracted/{doc_id}/extraction_meta.json"
        minio.put_object(
            MINIO_BUCKET, meta_key, io.BytesIO(meta_bytes),
            length=len(meta_bytes), content_type="application/json",
        )
        log.info(
            "job_id=%s stored extraction meta key=%s input_tokens=%s output_tokens=%s",
            job_id, meta_key, meta.get("input_tokens"), meta.get("output_tokens"),
        )

        # Quality checkpoint: persist confidence score to Postgres
        confidence = result.get("confidence_score")
        from sqlalchemy import func as sqlfunc2
        job.confidence_score = confidence
        job.updated_at       = sqlfunc2.now()
        db.commit()

        # Route to NEEDS_REVIEW if confidence < 0.80 or document not authentic
        if confidence is not None and confidence < 0.80:
            log.warning("job_id=%s confidence=%.2f below threshold → NEEDS_REVIEW", job_id, confidence)
            _set_job_status(db, job, JobStatus.NEEDS_REVIEW)
            record_job_outcome("needs_review")
        elif job.authentic is False:
            log.warning("job_id=%s authentic=False → NEEDS_REVIEW", job_id)
            _set_job_status(db, job, JobStatus.NEEDS_REVIEW)
            record_job_outcome("needs_review")
        else:
            _set_job_status(db, job, JobStatus.SUCCEEDED)
            record_job_outcome("succeeded")
        record_step("extract", time.time() - start)
        log.info("job_id=%s status=%s confidence=%s", job_id, job.status, confidence)

    except Exception as exc:
        log.exception("job_id=%s extract failed: %s", job_id, exc)
        record_step("extract", time.time() - start)
        record_step_failure("extract")
        record_job_outcome("failed")
        if isinstance(exc, ValueError) and "PII scan" in str(exc):
            record_pii_leak_block()
        try:
            job = db.get(Job, job_id)
            if job:
                _set_job_status(db, job, JobStatus.FAILED, error_message=str(exc)[:1024])
        except Exception:
            pass
        # PII scan failures are hard stops — don't retry, don't risk propagation.
        # JSON parse errors and API timeouts are transient — retry those.
        is_pii_block = isinstance(exc, ValueError) and "PII scan" in str(exc)
        if not is_pii_block:
            raise self.retry(exc=exc)
        raise
    finally:
        db.close()
