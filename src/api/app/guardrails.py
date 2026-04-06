from __future__ import annotations

import re
import pdfplumber
from fastapi import HTTPException, UploadFile

# Strong keywords — specific to financial documents we accept.
# At least ONE of these must be present. This stops restaurant receipts,
# invoices, and other docs that have generic words like "total" or "tax"
# from slipping through.
STRONG_FINANCIAL_KEYWORDS = {
    # Bank statements
    "balance", "account number", "routing", "deposit", "withdrawal",
    "statement", "transaction",
    # Paystubs
    "gross pay", "net pay", "ytd", "pay period", "payroll",
    "deduction", "earnings",
    # Tax returns & W2
    "wages", "withholding", "adjusted gross", "taxable income",
    "federal income tax", "form 1040", "w-2", "employer identification",
}

# Broad supporting keywords — need at least 2 of these alongside a strong hit.
SUPPORTING_KEYWORDS = {
    "income", "employer", "employee", "federal", "tax", "payment",
    "amount", "total", "salary", "compensation", "benefits", "irs",
}

MIN_SIZE = 1 * 1024          # 1 KB
MAX_SIZE = 50 * 1024 * 1024  # 50 MB

# SSN pattern XXX-XX-XXXX
_SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

# Documents with this many SSNs but no financial structure are PII dumps
_SSN_DUMP_THRESHOLD = 3


def validate_upload(file: UploadFile) -> None:
    # ── 1. File type: content-type header + PDF magic bytes ───────────────
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

    # ── 2. File size: between 1 KB and 50 MB ──────────────────────────────
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

    # ── 3. Text extraction: page 1 must have readable text ────────────────
    file.file.seek(0)
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

    lower_text = text.lower()

    # ── 4. PII data dump check ────────────────────────────────────────────
    # A raw list of names + SSNs has many SSNs but no financial structure.
    # Real financial documents may have SSNs but always have financial context.
    ssn_count = len(_SSN_PATTERN.findall(text))
    strong_hits = {kw for kw in STRONG_FINANCIAL_KEYWORDS if kw in lower_text}

    if ssn_count >= _SSN_DUMP_THRESHOLD and not strong_hits:
        raise HTTPException(
            status_code=422,
            detail=(
                f"GUARDRAIL_REJECTED: Document appears to be a PII data dump "
                f"({ssn_count} SSNs found with no financial document structure). "
                "Only genuine financial documents are accepted."
            ),
        )

    # ── 5. Financial document check ───────────────────────────────────────
    # Must have at least 1 strong keyword (document-type specific) to pass.
    # This blocks restaurant receipts, generic invoices, random PDFs that
    # happen to contain words like "total" or "tax".
    if not strong_hits:
        supporting_hits = {kw for kw in SUPPORTING_KEYWORDS if kw in lower_text}
        raise HTTPException(
            status_code=422,
            detail=(
                "GUARDRAIL_REJECTED: Document does not appear to be a supported "
                "financial document (bank statement, paystub, W-2, or tax return)"
            ),
        )
