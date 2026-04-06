"""
authenticator.py — Deterministic document authenticity checks.

Runs AFTER parse (we need the extracted text) and BEFORE redaction
(we need the raw numbers — redaction would replace them with placeholders).

No AI/LLM is used here. Every check is pure logic and math.

Three things we verify:
  1. Document type    — is this actually a bank statement / paystub / W2 / tax return?
  2. Balance math     — for bank statements, do the numbers add up?
  3. PDF metadata     — does the file look like it came from a real system, or was it edited?

Output: a structured report dict that gets stored as authenticity_report.json in MinIO.
The pipeline continues regardless of the result — we flag, not block, at this stage.
Blocking happens downstream based on the confidence score.
"""
from __future__ import annotations

import re
import logging
from typing import Any

log = logging.getLogger(__name__)

# ── Document type keyword sets ────────────────────────────────────────────────
# Each type has "strong" keywords (specific identifiers) and a minimum hit count.
# We score every type and pick the winner.

_TYPE_KEYWORDS: dict[str, list[str]] = {
    "bank_statement": [
        "routing number", "account number", "opening balance", "closing balance",
        "statement period", "deposit", "withdrawal", "transaction", "routing",
        "beginning balance", "ending balance", "available balance",
    ],
    "paystub": [
        "gross pay", "net pay", "ytd", "year to date", "pay period",
        "deductions", "earnings", "hours worked", "pay date",
        "regular pay", "overtime", "federal income tax", "fica",
    ],
    "w2": [
        "wages, tips", "federal income tax withheld", "employer identification",
        "social security wages", "medicare wages", "w-2", "wage and tax statement",
        "employee's social security", "allocated tips",
    ],
    "tax_return": [
        "adjusted gross income", "taxable income", "form 1040", "schedule",
        "standard deduction", "itemized deductions", "tax liability",
        "total income", "agi", "filing status",
    ],
}

# ── Balance reconciliation patterns ──────────────────────────────────────────
# Tries multiple label variations banks use for the same concept.

_BALANCE_PATTERNS: dict[str, list[str]] = {
    "opening": [
        r"(?:opening|beginning|starting|prior)\s+balance[^\d\-]*\$?([\d,]+\.?\d*)",
    ],
    "closing": [
        r"(?:closing|ending|final)\s+balance[^\d\-]*\$?([\d,]+\.?\d*)",
    ],
    "deposits": [
        r"total\s+(?:deposits?|credits?)[^\d\-]*\$?([\d,]+\.?\d*)",
        r"(?:deposits?|credits?)\s+total[^\d\-]*\$?([\d,]+\.?\d*)",
    ],
    "withdrawals": [
        r"total\s+(?:withdrawals?|debits?|charges?)[^\d\-]*\$?([\d,]+\.?\d*)",
        r"(?:withdrawals?|debits?)\s+total[^\d\-]*\$?([\d,]+\.?\d*)",
    ],
}

# Rounding tolerance — banks sometimes round to nearest cent differently
_BALANCE_TOLERANCE = 1.00


def _parse_amount(raw: str) -> float:
    """'4,218.77' → 4218.77"""
    return float(raw.replace(",", ""))


def _find_amount(text: str, patterns: list[str]) -> float | None:
    """Try each pattern in order, return the first match as a float."""
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                return _parse_amount(m.group(1))
            except (ValueError, IndexError):
                continue
    return None


# ── Document type classification ─────────────────────────────────────────────

def _classify_document(text: str) -> tuple[str, float]:
    """
    Score the text against each document type's keyword list.
    Returns (document_type, confidence 0.0–1.0).
    Confidence = fraction of that type's keywords found, capped and scaled.
    """
    lower = text.lower()
    scores: dict[str, float] = {}

    for doc_type, keywords in _TYPE_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in lower)
        # Confidence: 3+ hits = high confidence, 1 hit = low
        scores[doc_type] = min(hits / 3.0, 1.0)

    best_type = max(scores, key=lambda t: scores[t])
    best_score = scores[best_type]

    if best_score < 0.15:  # fewer than 1 keyword hit out of 3 needed
        return "unknown", 0.0

    return best_type, round(best_score, 2)


# ── Balance reconciliation ────────────────────────────────────────────────────

def _reconcile_balance(text: str) -> dict[str, Any]:
    """
    For bank statements: check opening + deposits - withdrawals ≈ closing.
    Returns a dict with the found values and whether they reconcile.
    """
    opening     = _find_amount(text, _BALANCE_PATTERNS["opening"])
    closing     = _find_amount(text, _BALANCE_PATTERNS["closing"])
    deposits    = _find_amount(text, _BALANCE_PATTERNS["deposits"])
    withdrawals = _find_amount(text, _BALANCE_PATTERNS["withdrawals"])

    detail: dict[str, Any] = {
        "opening_balance":  opening,
        "closing_balance":  closing,
        "total_deposits":   deposits,
        "total_withdrawals": withdrawals,
    }

    # Need all four to reconcile
    if None in (opening, closing, deposits, withdrawals):
        detail["reconciled"] = None  # Cannot determine — not enough data
        detail["delta"] = None
        detail["note"] = "Could not find all four balance fields in document"
        return detail

    expected_closing = opening + deposits - withdrawals
    delta = abs(expected_closing - closing)
    reconciled = delta <= _BALANCE_TOLERANCE

    detail["reconciled"] = reconciled
    detail["delta"] = round(delta, 2)
    detail["note"] = (
        "Balances reconcile within rounding tolerance"
        if reconciled
        else f"Balance mismatch: expected {expected_closing:.2f}, found {closing:.2f} (delta ${delta:.2f})"
    )
    return detail


# ── PDF metadata inspection ───────────────────────────────────────────────────

# Tools that suggest a PDF was hand-edited (not generated by a bank/payroll system)
_SUSPICIOUS_PRODUCERS = [
    "adobe acrobat", "microsoft word", "libreoffice", "openoffice",
    "photoshop", "gimp", "canva", "inkscape", "paint", "preview",
    "google docs", "pages",
]

def _inspect_metadata(pdf_bytes: bytes) -> dict[str, Any]:
    """
    Read PDF metadata (/Producer, /Creator, /CreationDate, /ModDate).
    Flag if the file was produced by an editing tool or modified after creation.
    """
    try:
        import pypdf
        reader = pypdf.PdfReader(__import__("io").BytesIO(pdf_bytes))
        meta = reader.metadata or {}

        producer     = str(meta.get("/Producer", "") or "").strip()
        creator      = str(meta.get("/Creator",  "") or "").strip()
        creation_date = str(meta.get("/CreationDate", "") or "").strip()
        mod_date     = str(meta.get("/ModDate",  "") or "").strip()

        # Modified after creation?
        metadata_modified = bool(
            mod_date
            and creation_date
            and mod_date != creation_date
        )

        # Edited in a non-bank tool?
        producer_lower = producer.lower()
        suspicious_producer = any(s in producer_lower for s in _SUSPICIOUS_PRODUCERS)

        return {
            "producer":           producer or None,
            "creator":            creator  or None,
            "creation_date":      creation_date or None,
            "mod_date":           mod_date  or None,
            "metadata_modified":  metadata_modified,
            "suspicious_producer": suspicious_producer,
        }

    except Exception as exc:
        log.warning("PDF metadata inspection failed: %s", exc)
        return {
            "producer": None, "creator": None,
            "creation_date": None, "mod_date": None,
            "metadata_modified": False, "suspicious_producer": False,
        }


# ── Main entry point ──────────────────────────────────────────────────────────

def authenticate_document(text: str, pdf_bytes: bytes) -> dict[str, Any]:
    """
    Run all authenticity checks and return a structured report.

    Args:
        text:      Full extracted text from the PDF (pre-redaction).
        pdf_bytes: Raw PDF bytes (for metadata inspection).

    Returns a dict with:
        authentic        — bool: True if we're reasonably confident the doc is real
        confidence       — float 0.0–1.0: overall trust score
        flags            — list of warning strings (empty = clean)
        document_type    — "bank_statement" | "paystub" | "w2" | "tax_return" | "unknown"
        type_confidence  — float: how sure we are about the document type
        balance          — reconciliation result (bank statements only, else None)
        pdf_metadata     — metadata inspection result
    """
    flags: list[str] = []
    confidence = 1.0

    # ── Check 1: Document type ────────────────────────────────────────────
    doc_type, type_confidence = _classify_document(text)

    if doc_type == "unknown":
        flags.append("Document type could not be determined from content")
        confidence -= 0.45  # Unknown type = we can't trust this document
    elif type_confidence < 0.5:
        flags.append(f"Low confidence on document type ({doc_type}, {type_confidence:.0%})")
        confidence -= 0.1

    # ── Check 2: Balance reconciliation (bank statements only) ────────────
    balance_result: dict[str, Any] | None = None
    if doc_type == "bank_statement":
        balance_result = _reconcile_balance(text)
        if balance_result["reconciled"] is False:
            flags.append(f"Balance mismatch: {balance_result['note']}")
            confidence -= 0.45  # Hard deduction — math that doesn't add up is a major fraud signal
        elif balance_result["reconciled"] is None:
            flags.append("Balance fields not found — could not verify math")
            confidence -= 0.1

    # ── Check 3: PDF metadata ─────────────────────────────────────────────
    pdf_meta = _inspect_metadata(pdf_bytes)

    if pdf_meta["suspicious_producer"]:
        flags.append(
            f"PDF produced by editing tool: '{pdf_meta['producer']}' — "
            "legitimate financial documents come from bank/payroll systems"
        )
        confidence -= 0.30

    if pdf_meta["metadata_modified"]:
        flags.append("PDF was modified after creation (ModDate != CreationDate)")
        confidence -= 0.20

    # ── Final verdict ─────────────────────────────────────────────────────
    confidence = round(max(confidence, 0.0), 2)
    authentic  = confidence >= 0.6

    log.info(
        "authenticity check: type=%s confidence=%.2f authentic=%s flags=%d",
        doc_type, confidence, authentic, len(flags),
    )

    return {
        "authentic":       authentic,
        "confidence":      confidence,
        "flags":           flags,
        "document_type":   doc_type,
        "type_confidence": type_confidence,
        "balance":         balance_result,
        "pdf_metadata":    pdf_meta,
    }
