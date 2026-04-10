"""
extractor.py — LLM-based structured extraction from redacted financial text.

Contract:
  - Input:  redacted text (PII already replaced with typed placeholders).
  - Output: structured JSON risk profile.
  - Guarantee: the LLM NEVER receives raw PII.  Redaction runs before this
               module is called — enforced by the pipeline, not by trust.

Current backend: Google Gemini Flash via the Gemini API.
  - Set GOOGLE_API_KEY in your .env / docker-compose.yml
  - Structured output is enforced via response_mime_type="application/json"
    with a fixed response_schema — the model is constrained to return only
    the fields defined in the schema.

After Gemini responds, an output PII scan checks the raw response string for
structured PII patterns (SSN, email, phone, account numbers) before the result
is parsed or stored.  Any hit aborts extraction and fails the job — preventing
downstream propagation of a redaction miss.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from google import genai
from google.genai import types

log = logging.getLogger(__name__)

# ── Versioning ────────────────────────────────────────────────────────────────
# Bump PROMPT_VERSION when the prompt or schema changes so the audit trail
# records which version produced each extraction result.
PROMPT_VERSION = "v5.0"  # v5: removed LLM self-reported confidence — score is now deterministic (scorer.py)

MODEL = "gemini-2.5-flash"

# ── Response schema (enforces structured JSON output) ─────────────────────────
# The schema is intentionally narrow: no field can hold a name, raw account
# number, or SSN. The schema itself acts as a guardrail — Gemini cannot
# reproduce PII that the schema has no slot for.
#
# Note: nullable fields use {"type": "string", "nullable": true} (Gemini format)
# rather than {"type": ["string", "null"]} (JSON Schema format).
_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "document_type": {
            "type": "string",
            "enum": ["bank_statement", "paystub", "w2", "tax_return", "unknown"],
            "description": "Classified document type."
        },
        "analysis_period": {
            "type": "string",
            "nullable": True,
            "description": "Date range or tax year covered by the document."
        },
        "income": {
            "type": "object",
            "properties": {
                "monthly_net_estimated": {
                    "type": "number",
                    "nullable": True,
                    "description": "Estimated monthly net income in USD. For annual docs (w2/tax_return), divide annual figure by 12."
                },
                "annual_gross": {
                    "type": "number",
                    "nullable": True,
                    "description": "Annual gross income in USD. Populate for w2 and tax_return only."
                },
                "sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Income sources (employer name redacted to generic label if placeholder present)."
                },
                "frequency": {
                    "type": "string",
                    "enum": ["weekly", "biweekly", "monthly", "annual", "irregular", "unknown"]
                }
            },
            "required": ["monthly_net_estimated", "sources", "frequency"]
        },
        "account_summary": {
            "type": "object",
            "description": "Balance data. Populate only for bank_statement; set all fields to null for other types.",
            "properties": {
                "opening_balance": {"type": "number", "nullable": True},
                "closing_balance": {"type": "number", "nullable": True},
                "average_daily_balance_estimated": {"type": "number", "nullable": True}
            },
            "required": ["opening_balance", "closing_balance", "average_daily_balance_estimated"]
        },
        "risk_flags": {
            "type": "object",
            "properties": {
                "overdraft_occurrences": {
                    "type": "integer",
                    "description": "Number of overdraft events. Set 0 for non-bank-statement types."
                },
                "nsf_fee_occurrences": {
                    "type": "integer",
                    "description": "Number of NSF fee charges. Set 0 for non-bank-statement types."
                },
                "large_cash_withdrawals": {
                    "type": "boolean",
                    "description": "True if any single cash withdrawal exceeds $1000. False for non-bank-statement types."
                },
                "gambling_transactions": {
                    "type": "boolean",
                    "description": "True if gambling-related transactions are present. False for non-bank-statement types."
                },
                "irregular_large_deposits": {
                    "type": "boolean",
                    "description": "True if unexplained large deposits appear. False for non-bank-statement types."
                },
                "document_integrity_flag": {
                    "type": "boolean",
                    "description": "True if the document contains mathematical inconsistencies, self-labels as doctored/fraudulent, or has self-contradictory figures."
                },
                "notes": {
                    "type": "string",
                    "nullable": True,
                    "description": "Free-text explanation of any flags raised, or null if none."
                }
            },
            "required": [
                "overdraft_occurrences", "nsf_fee_occurrences",
                "large_cash_withdrawals", "gambling_transactions",
                "irregular_large_deposits", "document_integrity_flag"
            ]
        },
        "recurring_expenses": {
            "type": "array",
            "description": "Recurring monthly expenses visible in bank statement. Empty array for other doc types.",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "average_monthly_amount": {"type": "number", "nullable": True},
                    "frequency": {"type": "string", "enum": ["weekly", "biweekly", "monthly", "irregular"]}
                },
                "required": ["category", "frequency"]
            }
        },
    },
    "required": [
        "document_type", "income", "account_summary",
        "risk_flags", "recurring_expenses"
    ]
}

# ── System prompt ─────────────────────────────────────────────────────────────
_SYSTEM_PROMPT = """\
You are a financial document analysis engine operating inside a compliance pipeline.

The text you receive has already been processed by a PII redaction system.
Personal identifiers — names, account numbers, SSNs, addresses, phone numbers —
have been replaced with typed placeholders such as [PERSON], [US_SSN],
[ACCOUNT_NUMBER], [LOCATION], [PHONE_NUMBER].

Your task: return a structured JSON risk profile extracted from the document.
You must always return valid JSON matching the schema exactly — never respond in plain text.

DOCUMENT TYPE RULES:
- "bank_statement": monthly account activity with transaction history, opening/closing balances.
- "paystub": single pay period earnings statement showing gross/net pay and deductions.
- "w2": IRS W-2 — annual wages (Box 1), federal/state tax withheld, employer fields.
- "tax_return": IRS Form 1040 or state equivalent — AGI, total tax, refund/owed.
- "unknown": only if the document genuinely cannot be classified.

INCOME EXTRACTION:
- bank_statement/paystub: set monthly_net_estimated from the period shown; set frequency to the pay cadence.
- w2: annual_gross = Box 1 wages; monthly_net_estimated = annual_gross / 12; frequency = "annual".
- tax_return: annual_gross = AGI; monthly_net_estimated = annual_gross / 12; frequency = "annual".

ACCOUNT SUMMARY: populate for bank_statement only; set all fields to null for other types.

RISK FLAGS for non-bank-statement types: set all count/boolean flags to 0/false — they are not observable from this document.

DOCUMENT INTEGRITY — set document_integrity_flag=true AND confidence_score <= 0.20 if ANY apply:
- Document labels itself "doctored", "fraudulent", "test fraud", etc.
- Net pay exceeds gross pay minus deductions.
- W-2 Social Security wages exceed Box 1 total wages.
- Tax > income (mathematical impossibility).
- Any self-contradictory financial figures.

Do NOT reproduce, infer, or reconstruct placeholder values. [PERSON] is anonymous. All monetary values in USD.
"""

# ── Output PII scan patterns ──────────────────────────────────────────────────
_OUTPUT_PII_PATTERNS: list[tuple[str, str]] = [
    ("SSN",            r"\b\d{3}-\d{2}-\d{4}\b"),
    ("EMAIL",          r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    ("PHONE_HYPHENS",  r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b"),
    ("ACCOUNT_MASKED", r"\*{4}\s\*{4}\s\*{4}\s\d{4}"),
    ("ROUTING",        r"(?i)(?:routing|aba|rtn)[^\d]{0,10}\d{9}\b"),
]


def _scan_output_for_pii(response_text: str) -> list[str]:
    # re.findall scans the entire string — catches every match, not just the first.
    # A security scanner that stops at the first hit is only half a scanner.
    warnings: list[str] = []
    for label, pattern in _OUTPUT_PII_PATTERNS:
        matches = re.findall(pattern, response_text)
        for match in matches:
            warnings.append(
                f"Potential {label} found in LLM output: '{match[:30]}'"
            )
    return warnings


def extract_from_redacted(redacted_text: str) -> dict[str, Any]:
    """
    Send *redacted_text* to Gemini and return a parsed risk profile dict.

    Uses response_mime_type="application/json" with a fixed response_schema to
    enforce structured output — Gemini is constrained to populate only the
    fields defined in _RESPONSE_SCHEMA.

    Raises:
        RuntimeError — if GOOGLE_API_KEY is not set.
        ValueError   — if the output PII scan finds leaked structured PII.
        RuntimeError — on API errors or empty responses.
    """
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GOOGLE_API_KEY environment variable is not set. "
            "Add it to your .env file."
        )

    client = genai.Client(api_key=api_key)

    log.info("Calling Gemini model=%s prompt_version=%s", MODEL, PROMPT_VERSION)

    response = client.models.generate_content(
        model=MODEL,
        contents=(
            "Extract the financial risk profile from the following redacted document.\n\n"
            f"{redacted_text}"
        ),
        config=types.GenerateContentConfig(
            system_instruction=_SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=_RESPONSE_SCHEMA,
        ),
    )

    usage = response.usage_metadata
    log.info(
        "Gemini responded: model=%s input_tokens=%d output_tokens=%d",
        MODEL,
        usage.prompt_token_count if usage else -1,
        usage.candidates_token_count if usage else -1,
    )

    raw_json = response.text
    if not raw_json:
        raise RuntimeError("Gemini returned an empty response.")

    # ── Output PII scan ───────────────────────────────────────────────────────
    pii_warnings = _scan_output_for_pii(raw_json)
    if pii_warnings:
        log.error("OUTPUT PII SCAN FAILED — aborting extraction. Warnings: %s", pii_warnings)
        raise ValueError(
            f"LLM output PII scan detected potential data leak: {pii_warnings}. "
            "Extraction aborted to prevent downstream propagation."
        )

    result: dict[str, Any] = json.loads(raw_json)

    # Stamp versioning metadata — stored separately in extraction_meta.json.
    result["_meta"] = {
        "model": MODEL,
        "prompt_version": PROMPT_VERSION,
        "input_tokens": usage.prompt_token_count if usage else None,
        "output_tokens": usage.candidates_token_count if usage else None,
    }

    return result
