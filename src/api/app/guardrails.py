from __future__ import annotations

import pdfplumber
from fastapi import HTTPException, UploadFile

FINANCIAL_KEYWORDS = {
    "balance", "account", "deposit", "withdrawal", "statement",
    "payroll", "routing", "transaction", "income", "payment",
}
MIN_SIZE = 1 * 1024          # 1 KB
MAX_SIZE = 50 * 1024 * 1024  # 50 MB


def validate_upload(file: UploadFile) -> None:
    # 1. File type check: content-type header + magic bytes
    if file.content_type != "application/pdf":
        raise HTTPException(
            status_code=422,
            detail="GUARDRAIL_REJECTED: File must be a PDF (invalid content type)",
        )
    magic = file.file.read(4)
    file.file.seek(0)
    if magic != b"%PDF":
        raise HTTPException(
            status_code=422,
            detail="GUARDRAIL_REJECTED: File must be a PDF (invalid magic bytes)",
        )

    # 2. File size check: between 1 KB and 50 MB
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)
    if size < MIN_SIZE:
        raise HTTPException(
            status_code=422,
            detail="GUARDRAIL_REJECTED: File is too small (minimum 1 KB)",
        )
    if size > MAX_SIZE:
        raise HTTPException(
            status_code=422,
            detail="GUARDRAIL_REJECTED: File is too large (maximum 50 MB)",
        )

    # 3. Text extraction check: page 1 must contain extractable text
    try:
        with pdfplumber.open(file.file) as pdf:
            first_page = pdf.pages[0] if pdf.pages else None
            text = first_page.extract_text() if first_page else None
    except Exception:
        text = None
    finally:
        file.file.seek(0)

    if not text or not text.strip():
        raise HTTPException(
            status_code=422,
            detail="GUARDRAIL_REJECTED: No extractable text found on page 1",
        )

    # 4. Financial keyword check: at least 2 keywords must be present
    lower_text = text.lower()
    found = {kw for kw in FINANCIAL_KEYWORDS if kw in lower_text}
    if len(found) < 2:
        raise HTTPException(
            status_code=422,
            detail="GUARDRAIL_REJECTED: Document does not appear to be a financial statement (insufficient keywords)",
        )
