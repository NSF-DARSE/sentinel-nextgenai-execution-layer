"""
aggregator.py — Combine per-document extractions into a single application profile.

A loan application is the unit of decision, not an individual document. A bank
statement should not be penalized for INCOME_MISSING when a paystub in the same
batch already supplies $2,000/mo. This module merges the per-document
extractions into one synthetic profile so compute_score() can produce one
defensible recommendation for the application.

Income source priority (most authoritative first):
    paystub → w2 → tax_return → bank_statement (deposits as fallback)

Risk signals (NSF, overdrafts, behavioral flags) come only from bank statements;
paystubs and W-2s do not carry them.

Authenticity is conservative: the application is authentic only if every
underlying document is authentic; combined confidence is the minimum.
"""
from __future__ import annotations

from typing import Any


_INCOME_SOURCE_PRIORITY = ("paystub", "w2", "tax_return", "bank_statement")

_DEFAULT_RISK_FLAGS: dict[str, Any] = {
    "overdraft_occurrences": 0,
    "nsf_fee_occurrences": 0,
    "large_cash_withdrawals": False,
    "gambling_transactions": False,
    "irregular_large_deposits": False,
    "document_integrity_flag": False,
    "notes": None,
}


def merge_extractions(extractions: list[dict]) -> dict:
    """
    Merge per-document extractions into a single application profile.

    The returned dict has the same shape as a single extraction so it can be
    fed directly into compute_score(). The synthetic document_type is set to
    "bank_statement" when one is present (the most discriminating type for
    scoring), otherwise to whatever single document was uploaded.
    """
    if not extractions:
        return {}

    # Best income source — first available, in priority order.
    best_income: dict | None = None
    income_source_type: str | None = None
    for priority_type in _INCOME_SOURCE_PRIORITY:
        for ex in extractions:
            if ex.get("document_type") != priority_type:
                continue
            income = ex.get("income") or {}
            if income.get("monthly_net_estimated") is not None:
                best_income = income
                income_source_type = priority_type
                break
        if best_income is not None:
            break
    if best_income is None:
        # Fall back to whatever the first extraction reported, even if null.
        best_income = (extractions[0].get("income") or {}) if extractions else {}
        income_source_type = extractions[0].get("document_type") if extractions else None

    # Bank statement carries account_summary and behavioral risk flags.
    bank_ex = next(
        (ex for ex in extractions if ex.get("document_type") == "bank_statement"),
        None,
    )

    doc_types_present = [
        ex.get("document_type") for ex in extractions if ex.get("document_type")
    ]
    if "bank_statement" in doc_types_present:
        combined_doc_type = "bank_statement"
    elif extractions:
        combined_doc_type = extractions[0].get("document_type") or "unknown"
    else:
        combined_doc_type = "unknown"

    return {
        "document_type": combined_doc_type,
        "documents_received": doc_types_present,
        "income_source_document": income_source_type,
        "income": best_income or {},
        "account_summary": (bank_ex or {}).get("account_summary") or {},
        "risk_flags": (bank_ex or {}).get("risk_flags") or dict(_DEFAULT_RISK_FLAGS),
    }


def merge_auth_reports(reports: list[dict]) -> dict:
    """
    Combined authenticity verdict.

    The application passes only if every document passes; combined confidence
    is the minimum across documents (a forged paystub poisons the application
    even when the bank statement looks clean).
    """
    if not reports:
        return {"authentic": None, "confidence": 0.0, "flags": [], "document_type": "application"}

    all_authentic = all(r.get("authentic") is True for r in reports)
    confidences = [float(r.get("confidence") or 0.0) for r in reports]
    min_confidence = min(confidences) if confidences else 0.0

    combined_flags: list[str] = []
    for r in reports:
        for f in r.get("flags") or []:
            combined_flags.append(f)

    return {
        "authentic": all_authentic,
        "confidence": min_confidence,
        "flags": combined_flags,
        "document_type": "application",
    }


def completeness_signals(profile: dict) -> list[str]:
    """
    Return reason codes describing the document set the applicant submitted.

    These are informational — they're added to the score breakdown so the
    customer and reviewer can see what evidence was provided.
    """
    received = profile.get("documents_received") or []
    has_income_doc = any(d in received for d in ("paystub", "w2", "tax_return"))
    has_bank = "bank_statement" in received

    signals: list[str] = []
    if has_income_doc and has_bank:
        signals.append("DOC_SET_COMPLETE")
    elif has_income_doc and not has_bank:
        signals.append("DOC_SET_MISSING_BANK_STATEMENT")
    elif has_bank and not has_income_doc:
        signals.append("DOC_SET_MISSING_INCOME_DOCUMENT")
    else:
        signals.append("DOC_SET_INCOMPLETE")

    return signals
