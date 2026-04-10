"""
scorer.py — Deterministic confidence scorer for financial document extraction.

Design principle: the LLM reads the document and identifies findings (risk flags,
field values, integrity issues). This module turns those findings into a score
using fixed rules and weights. Every point deduction has a reason code.

This is what the stakeholders see. Not "the AI said 0.74" but:
  "NSF_FEES_HIGH: 3 NSF events detected → -10 pts
   OVERDRAFTS_LOW: 2 overdrafts → -5 pts
   FULL_FIELD_COVERAGE: all fields populated → +20 pts"

Score structure (100 pts total):
  Layer 1 — Document Completeness   25 pts  (are the required fields there?)
  Layer 2 — Integrity & Auth        25 pts  (does the document pass math + metadata checks?)
  Layer 3 — Risk Signals            30 pts  (what behavioral risk is present?)
  Layer 4 — LLM Field Coverage      20 pts  (how completely did the LLM populate the schema?)

Threshold default: 0.80 (configurable via CONFIDENCE_THRESHOLD env var)
"""
from __future__ import annotations

import os
import logging
from typing import Any

log = logging.getLogger(__name__)

THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.80"))

SEVERITY_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def compute_score(extraction: dict[str, Any], auth_report: dict[str, Any]) -> dict[str, Any]:
    """
    Compute a deterministic confidence score from extraction results and auth report.

    Args:
        extraction:   structured output from extractor.py (Gemini findings)
        auth_report:  output from authenticator.py (deterministic fraud checks)

    Returns a dict with:
        score          float 0.0–1.0
        total_earned   int
        total_possible int
        recommendation "PASS" | "NEEDS_REVIEW"
        flags          list of reason codes for deductions
        breakdown      list of dicts — one per check, with points and detail
        threshold      float — the threshold used
    """
    breakdown: list[dict] = []
    total_earned = 0
    total_possible = 0
    flags: list[str] = []

    doc_type   = extraction.get("document_type", "unknown")
    income     = extraction.get("income") or {}
    account    = extraction.get("account_summary") or {}
    risk_flags = extraction.get("risk_flags") or {}

    # ── Layer 1: Document Completeness (25 pts) ───────────────────────────────

    # 1a. Document type identified (5 pts)
    total_possible += 5
    if doc_type != "unknown":
        breakdown.append(_check(
            "DOC_TYPE_IDENTIFIED", "completeness", 5, 5, "none",
            f"Document classified as {doc_type}",
        ))
        total_earned += 5
    else:
        breakdown.append(_check(
            "DOC_TYPE_UNKNOWN", "completeness", 0, 5, "high",
            "Document type could not be determined — classification failed",
        ))
        flags.append("DOC_TYPE_UNKNOWN")

    # 1b. Income present and positive (10 pts)
    total_possible += 10
    monthly_net = income.get("monthly_net_estimated")
    if monthly_net is not None and monthly_net > 0:
        breakdown.append(_check(
            "INCOME_VERIFIED", "completeness", 10, 10, "none",
            f"Monthly net income present: ${monthly_net:,.2f}",
        ))
        total_earned += 10
    elif monthly_net == 0:
        breakdown.append(_check(
            "INCOME_ZERO", "completeness", 4, 10, "medium",
            "Income field present but reported as $0 — verify with source",
        ))
        total_earned += 4
        flags.append("INCOME_ZERO")
    else:
        breakdown.append(_check(
            "INCOME_MISSING", "completeness", 0, 10, "high",
            "Monthly net income could not be extracted from document",
        ))
        flags.append("INCOME_MISSING")

    # 1c. Account balance data (10 pts) — required for bank statements only
    total_possible += 10
    if doc_type == "bank_statement":
        opening = account.get("opening_balance")
        closing = account.get("closing_balance")
        if opening is not None and closing is not None:
            breakdown.append(_check(
                "BALANCE_DATA_PRESENT", "completeness", 10, 10, "none",
                f"Opening ${opening:,.2f} → Closing ${closing:,.2f}",
            ))
            total_earned += 10
        else:
            breakdown.append(_check(
                "BALANCE_DATA_MISSING", "completeness", 0, 10, "medium",
                "Opening or closing balance not found in bank statement",
            ))
            flags.append("BALANCE_DATA_MISSING")
    else:
        breakdown.append(_check(
            "BALANCE_DATA_NA", "completeness", 10, 10, "none",
            f"Balance data not applicable for {doc_type}",
        ))
        total_earned += 10

    # ── Layer 2: Integrity & Authentication (25 pts) ──────────────────────────

    # 2a. Authenticity check (15 pts) — from deterministic authenticator
    total_possible += 15
    authentic      = auth_report.get("authentic")
    auth_confidence = float(auth_report.get("confidence") or 0)
    auth_flags     = auth_report.get("flags") or []

    if authentic is True and auth_confidence >= 0.80:
        breakdown.append(_check(
            "AUTH_PASSED", "integrity", 15, 15, "none",
            f"Passed all authenticity checks (auth confidence: {auth_confidence:.2f})",
        ))
        total_earned += 15
    elif authentic is True and auth_confidence >= 0.60:
        breakdown.append(_check(
            "AUTH_LOW_CONFIDENCE", "integrity", 8, 15, "medium",
            f"Passed checks but auth confidence low ({auth_confidence:.2f}): "
            + ("; ".join(auth_flags[:2]) or "minor anomalies"),
        ))
        total_earned += 8
        flags.append("AUTH_LOW_CONFIDENCE")
    else:
        breakdown.append(_check(
            "AUTH_FAILED", "integrity", 0, 15, "high",
            "Authentication failed: " + ("; ".join(auth_flags[:3]) or "unknown reason"),
        ))
        flags.append("AUTH_FAILED")

    # 2b. Document integrity (LLM math check) (10 pts)
    total_possible += 10
    integrity_fail = bool(risk_flags.get("document_integrity_flag"))
    if not integrity_fail:
        breakdown.append(_check(
            "INTEGRITY_OK", "integrity", 10, 10, "none",
            "No mathematical inconsistencies or self-contradictions detected",
        ))
        total_earned += 10
    else:
        notes = risk_flags.get("notes") or "figures are self-contradictory"
        breakdown.append(_check(
            "INTEGRITY_FAIL", "integrity", 0, 10, "critical",
            f"Document integrity issue: {notes}",
        ))
        flags.append("INTEGRITY_FAIL")

    # ── Layer 3: Risk Signals (30 pts) ───────────────────────────────────────

    # 3a. NSF fees (10 pts)
    total_possible += 10
    nsf = int(risk_flags.get("nsf_fee_occurrences") or 0)
    if nsf == 0:
        breakdown.append(_check("NO_NSF_FEES", "risk", 10, 10, "none", "No NSF fee events"))
        total_earned += 10
    elif nsf <= 2:
        breakdown.append(_check(
            "NSF_FEES_LOW", "risk", 5, 10, "medium",
            f"{nsf} NSF fee event(s) — moderate risk signal",
        ))
        total_earned += 5
        flags.append("NSF_FEES")
    else:
        breakdown.append(_check(
            "NSF_FEES_HIGH", "risk", 0, 10, "high",
            f"{nsf} NSF fee events — elevated risk",
        ))
        flags.append("NSF_FEES_HIGH")

    # 3b. Overdrafts (10 pts)
    total_possible += 10
    overdrafts = int(risk_flags.get("overdraft_occurrences") or 0)
    if overdrafts == 0:
        breakdown.append(_check("NO_OVERDRAFTS", "risk", 10, 10, "none", "No overdraft events"))
        total_earned += 10
    elif overdrafts <= 2:
        breakdown.append(_check(
            "OVERDRAFTS_LOW", "risk", 5, 10, "medium",
            f"{overdrafts} overdraft event(s)",
        ))
        total_earned += 5
        flags.append("OVERDRAFTS")
    else:
        breakdown.append(_check(
            "OVERDRAFTS_HIGH", "risk", 0, 10, "high",
            f"{overdrafts} overdraft events — elevated risk",
        ))
        flags.append("OVERDRAFTS_HIGH")

    # 3c. Behavioral flags: gambling, irregular deposits, large cash (10 pts)
    total_possible += 10
    deductions = 0
    behavioral_details = []

    if risk_flags.get("gambling_transactions"):
        deductions += 5
        flags.append("GAMBLING_TRANSACTIONS")
        behavioral_details.append("gambling transactions present")

    if risk_flags.get("irregular_large_deposits"):
        deductions += 3
        flags.append("IRREGULAR_DEPOSITS")
        behavioral_details.append("unexplained large deposits")

    if risk_flags.get("large_cash_withdrawals"):
        deductions += 2
        flags.append("LARGE_CASH_WITHDRAWALS")
        behavioral_details.append("large cash withdrawals (>$1,000)")

    behavioral_pts = max(0, 10 - deductions)
    if behavioral_details:
        breakdown.append(_check(
            "BEHAVIORAL_FLAGS", "risk", behavioral_pts, 10,
            "high" if deductions >= 5 else "medium",
            "; ".join(behavioral_details).capitalize(),
        ))
    else:
        breakdown.append(_check(
            "BEHAVIORAL_CLEAN", "risk", 10, 10, "none",
            "No gambling, irregular deposits, or large cash withdrawals",
        ))
    total_earned += behavioral_pts

    # ── Layer 4: LLM Field Coverage (20 pts) ─────────────────────────────────
    # How many required schema fields did the LLM actually populate?
    # This is the only layer that directly measures LLM output quality.

    total_possible += 20
    checks = [
        income.get("monthly_net_estimated") is not None,
        income.get("frequency") not in (None, "unknown"),
        income.get("sources") is not None,
        risk_flags.get("overdraft_occurrences") is not None,
        risk_flags.get("document_integrity_flag") is not None,
    ]
    if doc_type == "bank_statement":
        checks += [
            account.get("opening_balance") is not None,
            account.get("closing_balance") is not None,
        ]
    if doc_type in ("w2", "tax_return"):
        checks.append(income.get("annual_gross") is not None)

    populated = sum(checks)
    total_fields = len(checks)
    ratio = populated / total_fields if total_fields else 1.0
    coverage_pts = round(20 * ratio)

    if ratio == 1.0:
        breakdown.append(_check(
            "FULL_FIELD_COVERAGE", "coverage", 20, 20, "none",
            f"All {total_fields} required fields populated by LLM",
        ))
    else:
        missing = total_fields - populated
        breakdown.append(_check(
            "PARTIAL_FIELD_COVERAGE", "coverage", coverage_pts, 20,
            "medium" if ratio >= 0.7 else "high",
            f"{missing} of {total_fields} required fields missing or null",
        ))
        if ratio < 0.7:
            flags.append("LOW_FIELD_COVERAGE")
    total_earned += coverage_pts

    # ── Final ─────────────────────────────────────────────────────────────────
    score = round(total_earned / total_possible, 4) if total_possible else 0.0

    # Hard stops: integrity failure or auth failure always trigger review
    # regardless of numeric score — a 0.95 with a forged document is still fraud.
    hard_stop = any(f in flags for f in ("INTEGRITY_FAIL", "AUTH_FAILED", "DOC_TYPE_UNKNOWN"))
    recommendation = "NEEDS_REVIEW" if (score < THRESHOLD or hard_stop) else "PASS"

    log.info(
        "score=%.4f earned=%d/%d flags=%s recommendation=%s",
        score, total_earned, total_possible, flags, recommendation,
    )

    return {
        "score": score,
        "total_earned": total_earned,
        "total_possible": total_possible,
        "recommendation": recommendation,
        "flags": flags,
        "breakdown": breakdown,
        "threshold": THRESHOLD,
    }


def _check(
    code: str,
    category: str,
    points_earned: int,
    points_possible: int,
    severity: str,
    detail: str,
) -> dict:
    return {
        "code": code,
        "category": category,
        "points_earned": points_earned,
        "points_possible": points_possible,
        "severity": severity,
        "detail": detail,
    }
