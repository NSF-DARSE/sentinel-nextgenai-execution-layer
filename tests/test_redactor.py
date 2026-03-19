"""
Unit tests for app/redactor.py

Run from repo root (no Docker needed):
    pip install presidio-analyzer presidio-anonymizer spacy
    python -m spacy download en_core_web_lg
    python tests/test_redactor.py

Each test prints PASS or FAIL with a clear reason so you can see
exactly what the redactor catches, misses, and gets wrong.
"""

import sys
import os

# Allow running from repo root without installing the package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src/api"))

from app.redactor import redact_text

PASS = "✅ PASS"
FAIL = "❌ FAIL"
results = []


def check(name: str, condition: bool, detail: str = ""):
    status = PASS if condition else FAIL
    msg = f"{status}  {name}"
    if detail:
        msg += f"\n        → {detail}"
    print(msg)
    results.append(condition)


def section(title: str):
    print(f"\n── {title} {'─' * (50 - len(title))}")


# ─────────────────────────────────────────────────────────────────────────────
section("PERSON detection")

text = "James R. Whitfield submitted the application."
redacted, audit = redact_text(text)
check("Full name redacted",
      "James R. Whitfield" not in redacted,
      f"got: {redacted.strip()}")
check("Placeholder present",
      "[PERSON]" in redacted,
      f"got: {redacted.strip()}")

text = "VENMO PAYMENT - T. Nguyen 120.00"
redacted, audit = redact_text(text)
check("Short name 'T. Nguyen' redacted (initial + surname)",
      "T. Nguyen" not in redacted,
      f"got: {redacted.strip()}")

text = "Zelle Transfer - Maria G. 350.00"
redacted, audit = redact_text(text)
check("Short name 'Maria G.' redacted (firstname + initial)",
      "Maria G." not in redacted,
      f"got: {redacted.strip()}")

# ─────────────────────────────────────────────────────────────────────────────
section("SSN detection")

text = "SSN (last 4): 7291"
redacted, audit = redact_text(text)
check("SSN last-4 digits redacted",
      "7291" not in redacted,
      f"got: {redacted.strip()}")

text = "Social Security Number: 123-45-6789"
redacted, audit = redact_text(text)
check("Full SSN redacted",
      "123-45-6789" not in redacted,
      f"got: {redacted.strip()}")

# ─────────────────────────────────────────────────────────────────────────────
section("ROUTING NUMBER detection (custom recognizer)")

text = "ROUTING NUMBER: 021000089"
redacted, audit = redact_text(text)
check("Routing number redacted",
      "021000089" not in redacted,
      f"got: {redacted.strip()}")
check("Correct placeholder",
      "[ROUTING_NUMBER]" in redacted,
      f"got: {redacted.strip()}")

# ─────────────────────────────────────────────────────────────────────────────
section("ACCOUNT NUMBER detection (custom recognizer)")

text = "Account: **** **** **** 4408"
redacted, audit = redact_text(text)
check("Masked account number redacted",
      "**** **** **** 4408" not in redacted,
      f"got: {redacted.strip()}")
check("Correct placeholder",
      "[ACCOUNT_NUMBER]" in redacted,
      f"got: {redacted.strip()}")

# ─────────────────────────────────────────────────────────────────────────────
section("ADDRESS detection")

text = "4821 Elmwood Drive, Apt 3B, Austin, TX 78701"
redacted, audit = redact_text(text)
check("Street address redacted",
      "4821 Elmwood Drive" not in redacted,
      f"got: {redacted.strip()}")

# ─────────────────────────────────────────────────────────────────────────────
section("PHONE NUMBER — should catch real phones, NOT store IDs")

text = "Call us at 1-800-555-0192 for help."
redacted, audit = redact_text(text)
check("Real phone number redacted",
      "1-800-555-0192" not in redacted,
      f"got: {redacted.strip()}")

# These are store IDs — they should NOT be caught as phone numbers
text = "CHEVRON #00204511 62.40"
redacted, audit = redact_text(text)
check("Store ID #00204511 NOT redacted as phone",
      "[PHONE_NUMBER]" not in redacted,
      f"got: {redacted.strip()}")

text = "SHELL OIL 12345678 54.20"
redacted, audit = redact_text(text)
check("Bare store number 12345678 NOT redacted as phone",
      "[PHONE_NUMBER]" not in redacted,
      f"got: {redacted.strip()}")

text = "STARBUCKS #09812 18.75"
redacted, audit = redact_text(text)
check("Store ID #09812 NOT redacted as phone",
      "[PHONE_NUMBER]" not in redacted,
      f"got: {redacted.strip()}")

# ─────────────────────────────────────────────────────────────────────────────
section("DATE_TIME — transaction dates should NOT be redacted")

text = "03/01/2025 Opening Balance 4,218.77"
redacted, audit = redact_text(text)
check("Transaction date 03/01/2025 NOT redacted",
      "[DATE_TIME]" not in redacted,
      f"got: {redacted.strip()}")

text = "03/15/2025 Direct Deposit - ACME Corp Payroll 2,461.28"
redacted, audit = redact_text(text)
check("Transaction date 03/15/2025 NOT redacted",
      "[DATE_TIME]" not in redacted,
      f"got: {redacted.strip()}")

# ─────────────────────────────────────────────────────────────────────────────
section("TABULAR NAMES — customer record rows (ID prefix context)")

# C001 James Whitfield — 3-digit suffix, plain First Last
text = "C001 James Whitfield 321-54-9876"
redacted, audit = redact_text(text)
check("Tabular name 'James Whitfield' after C001 redacted",
      "James Whitfield" not in redacted,
      f"got: {redacted.strip()}")

# C004 Sandra L. Patel — 3-digit suffix, First M. Last
text = "C004 Sandra L. Patel 456-78-9012"
redacted, audit = redact_text(text)
check("Tabular name 'Sandra L. Patel' after C004 redacted",
      "Sandra" not in redacted or "Patel" not in redacted,
      f"got: {redacted.strip()}")

# C006 Emily Nguyen — 3-digit suffix, two-syllable last name
text = "C006 Emily Nguyen 789-01-2345"
redacted, audit = redact_text(text)
check("Tabular name 'Emily Nguyen' after C006 redacted",
      "Emily Nguyen" not in redacted,
      f"got: {redacted.strip()}")

# C010 Priya Sharma — 3-digit suffix, South-Asian name
text = "C010 Priya Sharma 567-89-0123"
redacted, audit = redact_text(text)
check("Tabular name 'Priya Sharma' after C010 redacted",
      "Priya Sharma" not in redacted,
      f"got: {redacted.strip()}")

# ─────────────────────────────────────────────────────────────────────────────
section("Audit trail output")

text = "James R. Whitfield, routing: 021000089, account: **** **** **** 4408"
redacted, audit = redact_text(text)
check("Audit trail is not empty",
      len(audit) > 0,
      f"got {len(audit)} entries")
check("Audit entries have required keys",
      all("entity_type" in e and "original_value" in e for e in audit),
      f"keys: {[list(e.keys()) for e in audit]}")
print(f"        → Entities found: {[e['entity_type'] for e in audit]}")
print(f"        → Redacted: {redacted.strip()}")

# ─────────────────────────────────────────────────────────────────────────────
section("Summary")

passed = sum(results)
total = len(results)
print(f"\n{passed}/{total} tests passed")

if passed < total:
    print("\nFailing tests show exactly what needs tuning in redactor.py")
    sys.exit(1)
else:
    print("\nAll tests pass — safe to rebuild Docker and run smoke test")
