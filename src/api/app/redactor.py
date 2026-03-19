from __future__ import annotations

from typing import List, Tuple

import spacy
from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerRegistry, RecognizerResult
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

# spaCy model loaded once — used as an independent second-pass detector.
# Presidio already uses spaCy internally, but only surfaces hits it's confident
# about.  Running spaCy directly and merging results (the "ensemble") catches
# names that Presidio drops — especially in short transaction lines where
# surrounding context is sparse.
try:
    _nlp = spacy.load("en_core_web_lg")
except OSError:
    _nlp = None  # Graceful degradation: ensemble disabled if model not present.

# Entity types this pipeline detects and redacts.
# DATE_TIME intentionally excluded — transaction dates are not PII.
_ENTITIES: List[str] = [
    "PERSON",
    "LOCATION",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "US_SSN",
    "US_BANK_NUMBER",
    "CREDIT_CARD",
    "NRP",
    "ROUTING_NUMBER",
    "ACCOUNT_NUMBER",
    "SSN_LAST4",
]


def _build_analyzer() -> AnalyzerEngine:
    registry = RecognizerRegistry()
    registry.load_predefined_recognizers()

    # ── Remove default phone recognizer ───────────────────────────────────────
    # The default PhoneRecognizer is too aggressive — it tags store IDs like
    # #09812 or bare 8-digit numbers as phone numbers. We replace it with a
    # stricter custom pattern below that requires proper phone formatting.
    try:
        registry.remove_recognizer("PhoneRecognizer")
    except Exception:
        pass

    # ── Custom: US street address ─────────────────────────────────────────────
    # spaCy NER tags city names as LOCATION but misses full street addresses.
    # This pattern catches "4821 Elmwood Drive, Apt 3B" style addresses.
    registry.add_recognizer(PatternRecognizer(
        supported_entity="LOCATION",
        patterns=[Pattern(
            name="us_street_address",
            regex=(
                r"\b\d{3,5}\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*"
                r"\s+(?:Street|St|Avenue|Ave|Drive|Dr|Road|Rd|"
                r"Boulevard|Blvd|Lane|Ln|Way|Court|Ct|Place|Pl|Circle|Cir)"
                r"(?:\s*,\s*(?:Apt|Suite|Ste|Unit|#)\s*\w+)?\b"
            ),
            score=0.8,
        )],
    ))

    # ── Custom: Initial-style names ───────────────────────────────────────────
    # spaCy NER misses short names like "T. Nguyen" or "Maria G." in transaction
    # lines because there's not enough surrounding context to signal "person".
    # Two patterns cover both orderings:
    #   "T. Nguyen"  →  initial + period + surname
    #   "Maria G."   →  first name + initial + period
    registry.add_recognizer(PatternRecognizer(
        supported_entity="PERSON",
        patterns=[
            Pattern(
                name="initial_then_surname",
                regex=r"\b[A-Z]\.\s+[A-Z][a-z]{2,}\b",
                score=0.7,
            ),
            Pattern(
                name="firstname_then_initial",
                regex=r"\b[A-Z][a-z]{2,}\s+[A-Z]\.(?=\s|$|,)",
                score=0.7,
            ),
        ],
    ))

    # ── Custom: Tabular customer record names ─────────────────────────────────
    # spaCy NER misses names in table rows because a single line like
    # "C001 James Whitfield 321-54-9876" has no surrounding prose context.
    # These patterns use fixed-width lookbehinds for customer ID prefixes
    # (C + exactly 3 or 4 digits + space) so they only fire in that context
    # and never cause false positives in transaction description lines.
    #
    # Covers:
    #   "C001 James Whitfield"    →  First Last
    #   "C004 Sandra L. Patel"    →  First M. Last
    #   "C006 Emily Nguyen"       →  First Last
    registry.add_recognizer(PatternRecognizer(
        supported_entity="PERSON",
        patterns=[
            # Customer ID with 3-digit suffix: C001, C004, C006, C010
            Pattern(
                name="tabular_name_after_c3",
                regex=r"(?<=C\d\d\d )[A-Z][a-z]{2,}(?:\s+[A-Z]\.)?\s+[A-Z][a-z]{2,}",
                score=0.85,
            ),
            # Customer ID with 4-digit suffix: C0001, C0010 etc.
            Pattern(
                name="tabular_name_after_c4",
                regex=r"(?<=C\d\d\d\d )[A-Z][a-z]{2,}(?:\s+[A-Z]\.)?\s+[A-Z][a-z]{2,}",
                score=0.85,
            ),
        ],
    ))

    # ── Custom: Routing number ────────────────────────────────────────────────
    # Exactly 9 digits near financial context words.
    registry.add_recognizer(PatternRecognizer(
        supported_entity="ROUTING_NUMBER",
        patterns=[Pattern(name="routing_9digit", regex=r"\b\d{9}\b", score=0.6)],
        context=["routing", "aba", "rtn", "transit", "number"],
    ))

    # ── Custom: Masked account number ─────────────────────────────────────────
    # Matches "**** **** **** 4408" style — already partially masked by the bank.
    registry.add_recognizer(PatternRecognizer(
        supported_entity="ACCOUNT_NUMBER",
        patterns=[Pattern(
            name="masked_account",
            regex=r"\*{4}\s\*{4}\s\*{4}\s\d{4}",
            score=0.9,
        )],
    ))

    # ── Custom: SSN last-4 ────────────────────────────────────────────────────
    # Catches "SSN (last 4): 7291" — Presidio's built-in US_SSN only catches
    # full XXX-XX-XXXX format, missing this common bank statement pattern.
    registry.add_recognizer(PatternRecognizer(
        supported_entity="SSN_LAST4",
        patterns=[Pattern(
            name="ssn_last4_labeled",
            regex=r"(?i)ssn\s*\(last\s*4\)\s*[:\-]?\s*\d{4}",
            score=0.95,
        )],
    ))

    # ── Custom: Full SSN fallback ─────────────────────────────────────────────
    # Adds explicit high-confidence pattern for "Social Security Number: XXX-XX-XXXX"
    # phrasing that Presidio's default recognizer sometimes misses.
    registry.add_recognizer(PatternRecognizer(
        supported_entity="US_SSN",
        patterns=[
            Pattern(name="ssn_dashes",   regex=r"\b\d{3}-\d{2}-\d{4}\b", score=0.85),
            Pattern(name="ssn_nodashes", regex=r"\b\d{9}\b",              score=0.4),
        ],
        context=["ssn", "social security", "social security number"],
    ))

    # ── Tighten phone number detection ───────────────────────────────────────
    # Override default phone recognizer with patterns that require proper
    # phone formatting (hyphens, dots, parens, country codes).
    # This prevents store IDs like #00204511 or 12345678 being tagged as phones.
    registry.add_recognizer(PatternRecognizer(
        supported_entity="PHONE_NUMBER",
        patterns=[
            # 1-800-555-0192 or 800-555-0192
            Pattern(name="phone_hyphens",
                    regex=r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b",
                    score=0.75),
            # (800) 555-0192
            Pattern(name="phone_parens",
                    regex=r"\(\d{3}\)\s*\d{3}[-.\s]\d{4}",
                    score=0.75),
        ],
        deny_list=[],
    ))

    return AnalyzerEngine(registry=registry)


# Module-level singletons — loaded once per worker process.
_analyzer = _build_analyzer()
_anonymizer = AnonymizerEngine()

# One replace operator per entity type so each placeholder is clearly typed.
_OPERATORS: dict[str, OperatorConfig] = {
    entity: OperatorConfig("replace", {"new_value": f"[{entity}]"})
    for entity in _ENTITIES
}
_OPERATORS["DEFAULT"] = OperatorConfig("replace", {"new_value": "[REDACTED]"})


def _spacy_ensemble(text: str, presidio_results: List[RecognizerResult]) -> List[RecognizerResult]:
    """
    Run spaCy NER directly and return any PERSON/LOCATION hits that Presidio
    did not already cover.  Only non-overlapping spans are added so we don't
    produce duplicate placeholders in the anonymized output.

    spaCy entity labels we care about:
        PERSON  → PERSON
        GPE     → LOCATION  (geopolitical: cities, countries, states)
        LOC     → LOCATION  (physical locations: mountains, rivers, etc.)

    ORG is intentionally skipped — bank names and employers are not PII and
    we don't want "ACME Corp Payroll" turned into [LOCATION].
    """
    if _nlp is None:
        return presidio_results  # Model not available — return unchanged.

    _LABEL_MAP = {"PERSON": "PERSON", "GPE": "LOCATION", "LOC": "LOCATION"}

    doc = _nlp(text)
    merged = list(presidio_results)

    for ent in doc.ents:
        entity_type = _LABEL_MAP.get(ent.label_)
        if entity_type is None:
            continue  # Not an entity type we redact.

        # Skip if any existing result overlaps with this span.
        overlaps = any(
            r.start < ent.end_char and r.end > ent.start_char
            for r in presidio_results
        )
        if overlaps:
            continue

        merged.append(RecognizerResult(
            entity_type=entity_type,
            start=ent.start_char,
            end=ent.end_char,
            score=0.65,  # Moderate confidence — spaCy NER, no extra context signals.
        ))

    return merged


def redact_text(text: str) -> Tuple[str, List[dict]]:
    """
    Detect PII in *text*, replace each span with a typed placeholder, and
    return (redacted_text, audit_entries).

    Detection uses a two-pass ensemble:
      Pass 1 — Presidio (custom recognizers + spaCy-backed NER via Presidio)
      Pass 2 — spaCy NER directly (catches names Presidio drops in sparse context)
    The union of both passes is redacted.  False positives are acceptable here;
    false negatives (missed PII reaching the LLM) are not.

    Each audit entry is a dict with keys:
        entity_type    – e.g. "PERSON"
        start          – character offset in the ORIGINAL text
        end            – character offset in the ORIGINAL text
        original_value – the raw text that was redacted
        detector       – "presidio" | "spacy_ensemble"
    """
    presidio_results = _analyzer.analyze(text=text, entities=_ENTITIES, language="en")
    results = _spacy_ensemble(text, presidio_results)

    # Tag each audit entry with which detector caught it.
    presidio_spans = {(r.start, r.end) for r in presidio_results}

    audit: List[dict] = [
        {
            "entity_type": r.entity_type,
            "start": r.start,
            "end": r.end,
            "original_value": text[r.start: r.end],
            "detector": "presidio" if (r.start, r.end) in presidio_spans else "spacy_ensemble",
        }
        for r in sorted(results, key=lambda r: r.start)
    ]

    anonymized = _anonymizer.anonymize(
        text=text,
        analyzer_results=results,  # union of presidio + spacy_ensemble
        operators=_OPERATORS,
    )

    return anonymized.text, audit
