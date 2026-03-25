"""
Tests for app/guardrails.py

Run from repo root:
    pip install pdfplumber reportlab pytest fastapi
    pytest tests/test_guardrails.py -v
"""

from __future__ import annotations

import io
import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src/api"))

from fastapi import HTTPException, UploadFile
from app.guardrails import validate_upload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_upload(data: bytes, content_type: str = "application/pdf", filename: str = "test.pdf") -> UploadFile:
    return UploadFile(filename=filename, file=io.BytesIO(data), headers={"content-type": content_type})


def build_pdf(text: str) -> bytes:
    """Build a minimal PDF containing *text* on page 1 using reportlab."""
    from reportlab.lib.pagesizes import LETTER
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    # Write text line by line so it fits on the page
    y = 700
    for line in text.splitlines():
        c.drawString(50, y, line)
        y -= 15
    c.save()
    buf.seek(0)
    return buf.read()


BANK_STATEMENT_TEXT = (
    "Bank Statement\n"
    "Account Number: **** **** **** 1234\n"
    "Routing: 021000089\n"
    "Opening Balance: 4,218.77\n"
    "Deposit: 2,461.28  Direct Deposit - Payroll\n"
    "Withdrawal: 120.00  Rent payment\n"
    "Transaction Date: 03/01/2025\n"
    "Income: 2,461.28\n"
)

RANDOM_TEXT = (
    "The quick brown fox jumps over the lazy dog.\n"
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit.\n"
    "Sed ut perspiciatis unde omnis iste natus error sit voluptatem.\n"
)


# ---------------------------------------------------------------------------
# Test 1: A valid bank statement PDF passes all guardrails
# ---------------------------------------------------------------------------

def test_valid_bank_statement_passes():
    pdf_bytes = build_pdf(BANK_STATEMENT_TEXT)
    upload = make_upload(pdf_bytes, content_type="application/pdf")
    # Should not raise
    result = validate_upload(upload)
    assert result is None


# ---------------------------------------------------------------------------
# Test 2: A non-PDF file is rejected (wrong content-type)
# ---------------------------------------------------------------------------

def test_non_pdf_content_type_rejected():
    data = b"This is just some plain text content that is large enough."
    upload = make_upload(data, content_type="text/plain", filename="doc.txt")
    with pytest.raises(HTTPException) as exc_info:
        validate_upload(upload)
    assert exc_info.value.status_code == 422
    assert "GUARDRAIL_REJECTED" in exc_info.value.detail
    assert "content type" in exc_info.value.detail.lower() or "pdf" in exc_info.value.detail.lower()


# ---------------------------------------------------------------------------
# Test 3: A file with PDF content-type but wrong magic bytes is rejected
# ---------------------------------------------------------------------------

def test_non_pdf_magic_bytes_rejected():
    # Correct content-type but not a real PDF (no %PDF header)
    data = b"\x89PNG" + b"\x00" * 2048
    upload = make_upload(data, content_type="application/pdf", filename="fake.pdf")
    with pytest.raises(HTTPException) as exc_info:
        validate_upload(upload)
    assert exc_info.value.status_code == 422
    assert "GUARDRAIL_REJECTED" in exc_info.value.detail


# ---------------------------------------------------------------------------
# Test 4: An empty (too-small) PDF is rejected
# ---------------------------------------------------------------------------

def test_empty_pdf_rejected():
    # Build a valid PDF magic header but keep it under 1 KB
    tiny_data = b"%PDF-1.4\n" + b" " * 10  # well under 1 KB
    upload = make_upload(tiny_data, content_type="application/pdf")
    with pytest.raises(HTTPException) as exc_info:
        validate_upload(upload)
    assert exc_info.value.status_code == 422
    assert "GUARDRAIL_REJECTED" in exc_info.value.detail
    # Could fail on size OR text extraction — both are correct rejections
    assert "GUARDRAIL_REJECTED" in exc_info.value.detail


# ---------------------------------------------------------------------------
# Test 5: A PDF with no financial keywords is rejected
# ---------------------------------------------------------------------------

def test_pdf_no_financial_keywords_rejected():
    pdf_bytes = build_pdf(RANDOM_TEXT)
    upload = make_upload(pdf_bytes, content_type="application/pdf")
    with pytest.raises(HTTPException) as exc_info:
        validate_upload(upload)
    assert exc_info.value.status_code == 422
    assert "GUARDRAIL_REJECTED" in exc_info.value.detail
    assert "financial" in exc_info.value.detail.lower() or "keyword" in exc_info.value.detail.lower()
