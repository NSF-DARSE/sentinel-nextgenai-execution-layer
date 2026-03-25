"""
extractor.py — LLM-based structured extraction from redacted financial text.

Contract:
  - Input:  redacted text (PII already replaced with typed placeholders).
  - Output: structured JSON risk profile.
  - Guarantee: the LLM NEVER receives raw PII.  Redaction runs before this
               module is called — enforced by the pipeline, not by trust.

Current backend: Google Gemini via Google AI Studio (free tier).
  - Get your key at https://aistudio.google.com/app/apikey
  - Set GOOGLE_API_KEY in your .env / docker-compose.yml
  - When you move to GCP, swap MODEL to any Vertex AI model name and
    update auth to use a service account — nothing else changes.

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

import google.generativeai as genai

log = logging.getLogger(__name__)

# ── Versioning ────────────────────────────────────────────────────────────────
# Bump PROMPT_VERSION when the prompt or schema changes so the audit trail
# records which version produced each extraction result.
PROMPT_VERSION = "v1.0"

# gemini-2.5-flash — free tier on Google AI Studio, fast, strong at structured
# extraction.  When you get GCP access, swap to "gemini-1.5-pro" or any
# Vertex AI model name here — nothing else in this file needs to change.
MODEL = "gemini-2.5-flash"

# ── Output schema (included in the prompt) ───────────────────────────────────
# The schema is intentionally narrow: no field can hold a name, raw account
# number, or SSN.  The schema itself acts as a guardrail — Gemini cannot
# reproduce PII that the schema has no slot for.
# Replace your current _SCHEMA = """...""" with this:
_SCHEMA = {
    "type": "object",
    "properties": {
        "document_type": {"type": "string", "enum": ["bank_statement", "paystub", "unknown"]},
        "analysis_period": {"type": "string", "nullable": True},
        "income": {
            "type": "object",
            "properties": {
                "monthly_net_estimated": {"type": "number", "nullable": True},
                "sources": {"type": "array", "items": {"type": "string"}},
                "frequency": {"type": "string", "enum": ["weekly", "biweekly", "monthly", "irregular", "unknown"]}
            },
            "required": ["monthly_net_estimated", "sources", "frequency"]
        },
        "account_summary": {
            "type": "object",
            "properties": {
                "opening_balance": {"type": "number", "nullable": True},
                "closing_balance": {"type": "number", "nullable": True},
                "average_daily_balance_estimated": {"type": "number", "nullable": True}
            }
        },
        "risk_flags": {
            "type": "object",
            "properties": {
                "overdraft_occurrences": {"type": "integer"},
                "nsf_fee_occurrences": {"type": "integer"},
                "large_cash_withdrawals": {"type": "boolean"},
                "gambling_transactions": {"type": "boolean"},
                "irregular_large_deposits": {"type": "boolean"},
                "notes": {"type": "string", "nullable": True}
            },
            "required": ["overdraft_occurrences", "nsf_fee_occurrences", "large_cash_withdrawals", "gambling_transactions", "irregular_large_deposits"]
        },
        "recurring_expenses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "average_monthly_amount": {"type": "number", "nullable": True},
                    "frequency": {"type": "string", "enum": ["weekly", "biweekly", "monthly", "irregular"]}
                }
            }
        },
        "confidence_score": {"type": "number"}
    },
    "required": ["document_type", "income", "account_summary", "risk_flags", "confidence_score"]
}

# ── System prompt ─────────────────────────────────────────────────────────────
_SYSTEM_PROMPT = f"""\
You are a financial document analysis engine operating inside a compliance pipeline.

The text you receive has already been processed by a PII redaction system.
Personal identifiers — names, account numbers, SSNs, addresses, phone numbers — \
have been replaced with typed placeholders such as [PERSON], [US_SSN], \
[ACCOUNT_NUMBER], [LOCATION], [PHONE_NUMBER].

Your task: extract a structured financial risk profile from the redacted text.

Rules you must follow:
1. Return ONLY valid JSON matching the schema below. No commentary, no markdown \
fences, no explanation — raw JSON only.
2. Do NOT reproduce, infer, or attempt to reconstruct any placeholder value. \
If you see [PERSON], treat it as an anonymous individual. Do not guess a name.
3. Use null for any field where the document does not provide enough information.
4. All monetary values are in USD.
5. If you cannot determine the document type, set document_type to "unknown" \
and confidence_score below 0.5.
"""

# ── Output PII scan patterns ──────────────────────────────────────────────────
# After Gemini responds, these patterns check the raw response string for
# structured PII that should never appear in an extraction output.
# Names are not checked here (hard to regex reliably) — that protection comes
# from the schema design (no name fields) and the redactor upstream.
_OUTPUT_PII_PATTERNS: list[tuple[str, str]] = [
    ("SSN",            r"\b\d{3}-\d{2}-\d{4}\b"),
    ("EMAIL",          r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    ("PHONE_HYPHENS",  r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b"),
    ("ACCOUNT_MASKED", r"\*{4}\s\*{4}\s\*{4}\s\d{4}"),
    ("ROUTING",        r"(?i)(?:routing|aba|rtn)[^\d]{0,10}\d{9}\b"),
]


def _scan_output_for_pii(response_text: str) -> list[str]:
    """
    Scan Gemini's raw response for structured PII patterns.
    Returns a list of warning strings (empty list means clean).
    """
    warnings: list[str] = []
    for label, pattern in _OUTPUT_PII_PATTERNS:
        match = re.search(pattern, response_text)
        if match:
            warnings.append(
                f"Potential {label} found in LLM output: '{match.group()[:30]}'"
            )
    return warnings


def extract_from_redacted(redacted_text: str) -> dict[str, Any]:
    """
    Send *redacted_text* to Gemini and return a parsed risk profile dict.

    Raises:
        RuntimeError — if GOOGLE_API_KEY is not set.
        ValueError   — if the output PII scan finds leaked structured PII.
        ValueError   — if Gemini's response is not valid JSON.
        RuntimeError — on API errors.

    Migration note: when moving to Vertex AI on GCP, replace genai.configure()
    with vertexai.init(project=..., location=...) and update the model call to
    use the vertexai.generative_models.GenerativeModel interface.  The prompt,
    schema, and PII scan logic are identical.
    """
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GOOGLE_API_KEY environment variable is not set.  "
            "Get a free key at https://aistudio.google.com/app/apikey "
            "and add it to your .env file."
        )

    genai.configure(api_key=api_key)

    model = genai.GenerativeModel(
        model_name=MODEL,
        system_instruction=_SYSTEM_PROMPT,
        generation_config=genai.types.GenerationConfig(
            response_mime_type="application/json",
            response_schema=_SCHEMA,
            temperature=0,
            max_output_tokens=2048,
        ),
    )

    log.info("Calling Gemini model=%s prompt_version=%s", MODEL, PROMPT_VERSION)

    response = model.generate_content(
        f"Extract the financial risk profile from the following redacted document.\n\n"
        f"{redacted_text}"
    )

    raw_response = response.text
    usage = response.usage_metadata
    log.info(
        "Gemini responded: model=%s input_tokens=%d output_tokens=%d",
        MODEL,
        usage.prompt_token_count if usage else -1,
        usage.candidates_token_count if usage else -1,
    )

    # ── Output PII scan ───────────────────────────────────────────────────────
    pii_warnings = _scan_output_for_pii(raw_response)
    if pii_warnings:
        log.error(
            "OUTPUT PII SCAN FAILED — aborting extraction. Warnings: %s",
            pii_warnings,
        )
        raise ValueError(
            f"LLM output PII scan detected potential data leak: {pii_warnings}. "
            "Extraction aborted to prevent downstream propagation."
        )

    # ── Parse JSON ────────────────────────────────────────────────────────────
    # response_mime_type="application/json" means Gemini returns raw JSON, but
    # strip fences defensively in case the model ignores the mime type hint.
    try:
        clean = raw_response.strip()
        if clean.startswith("```"):
            clean = re.sub(r"^```[a-z]*\n?", "", clean)
            clean = re.sub(r"\n?```$", "", clean)
        result: dict[str, Any] = json.loads(clean)
    except json.JSONDecodeError as exc:
        log.error("Gemini returned non-JSON response: %s", raw_response[:500])
        raise ValueError(f"Gemini response was not valid JSON: {exc}") from exc

    # Stamp versioning metadata — stored separately in extraction_meta.json.
    result["_meta"] = {
        "model": MODEL,
        "prompt_version": PROMPT_VERSION,
        "input_tokens": usage.prompt_token_count if usage else None,
        "output_tokens": usage.candidates_token_count if usage else None,
    }

    return result
