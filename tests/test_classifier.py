"""
Tests for the document relevance classifier (_classify_text) in worker.py.

Run from repo root:
    pytest tests/test_classifier.py -v

No Docker, no MinIO, no Redis required — pure unit tests of the classification logic.
"""
from __future__ import annotations

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src/api"))

from app.worker import _classify_text, _MIN_MATCHES

# ---------------------------------------------------------------------------
# Helpers — minimal synthetic document text
# ---------------------------------------------------------------------------

BANK_STATEMENT = """
Chase Bank — Account Statement
Statement Period: January 1 – January 31, 2025
Checking Account: ****1234
Routing Number: 021000021

Beginning Balance: $4,250.00
Direct Deposit — Payroll         +$3,200.00
ATM Withdrawal                    -$200.00
Overdraft fee                      -$35.00
Ending Balance: $7,215.00
Available Balance: $7,215.00
"""

PAYSTUB = """
Acme Corp — Earnings Statement
Pay Period: Jan 1 – Jan 15, 2025     Pay Date: Jan 20, 2025
Employee: John Smith

Gross Pay:            $3,500.00
Federal Income Tax:    -$420.00
Social Security:       -$217.00
Medicare:               -$50.75
Deductions:            -$150.00
Net Pay:              $2,662.25

Year To Date (YTD) Gross: $7,000.00
Hours Worked: 80
"""

W2_FORM = """
Form W-2  Wage and Tax Statement  2024
Employer Identification Number: 12-3456789
Employee SSN: ***-**-6789

Wages, Tips, Other Compensation:   $52,000.00
Federal Income Tax Withheld:        $6,240.00
Social Security Wages:             $52,000.00
Medicare Wages:                    $52,000.00
Social Security Tax Withheld:       $3,224.00
"""

TAX_RETURN = """
Form 1040 — U.S. Individual Income Tax Return  2024
Filing Status: Married Filing Jointly

Adjusted Gross Income:      $95,000.00
Standard Deduction:         $29,200.00
Taxable Income:             $65,800.00
Tax Refund:                  $1,450.00
"""

RESTAURANT_RECEIPT = """
The Spaghetti House
123 Main St, Wilmington, DE
Table 7   Server: Maria   Date: 2025-01-15

Pasta Carbonara        $18.00
House Salad             $9.00
Sparkling Water         $4.00
Subtotal               $31.00
Tax (8%)                $2.48
Tip                     $6.00
Total                  $39.48

Thank you for dining with us!
"""

LEASE_AGREEMENT = """
RESIDENTIAL LEASE AGREEMENT

This lease is entered into between Landlord Jane Doe and Tenant John Smith
for the property located at 456 Oak Ave, Newark, DE 19711.

Term: February 1, 2025 to January 31, 2026
Monthly Rent: $1,400.00
Security Deposit: $1,400.00

Tenant agrees to pay rent on the 1st of each month. Late fees apply after
the 5th. Landlord may terminate lease with 30 days written notice.
"""

EMPTY_DOC = ""

BARELY_FINANCIAL = "account balance is important"  # only 1 keyword match


# ---------------------------------------------------------------------------
# Tests — documents that should be accepted
# ---------------------------------------------------------------------------

class TestFinancialDocumentsAccepted:

    def test_bank_statement_accepted(self):
        is_fin, category = _classify_text(BANK_STATEMENT)
        assert is_fin is True
        assert category == "bank_statement"

    def test_paystub_accepted(self):
        is_fin, category = _classify_text(PAYSTUB)
        assert is_fin is True
        assert category == "paystub"

    def test_w2_accepted(self):
        is_fin, category = _classify_text(W2_FORM)
        assert is_fin is True
        assert category == "w2"

    def test_tax_return_accepted(self):
        is_fin, category = _classify_text(TAX_RETURN)
        assert is_fin is True
        assert category == "tax_return"

    def test_case_insensitive_matching(self):
        # Uppercase version of the bank statement should still match
        is_fin, category = _classify_text(BANK_STATEMENT.upper())
        assert is_fin is True
        assert category == "bank_statement"


# ---------------------------------------------------------------------------
# Tests — documents that should be rejected
# ---------------------------------------------------------------------------

class TestNonFinancialDocumentsRejected:

    def test_restaurant_receipt_rejected(self):
        is_fin, category = _classify_text(RESTAURANT_RECEIPT)
        assert is_fin is False
        assert category == "unknown"

    def test_lease_agreement_rejected(self):
        is_fin, category = _classify_text(LEASE_AGREEMENT)
        assert is_fin is False
        assert category == "unknown"

    def test_empty_document_rejected(self):
        is_fin, category = _classify_text(EMPTY_DOC)
        assert is_fin is False
        assert category == "unknown"

    def test_single_keyword_match_rejected(self):
        # One keyword hit is not enough — threshold is _MIN_MATCHES
        is_fin, category = _classify_text(BARELY_FINANCIAL)
        assert is_fin is False
        assert category == "unknown"

    def test_rejected_category_is_always_unknown(self):
        # Even if a document has 1 bank keyword, rejected label must be "unknown"
        text = "routing number is somewhere in this document but that's it"
        is_fin, category = _classify_text(text)
        assert is_fin is False
        assert category == "unknown"


# ---------------------------------------------------------------------------
# Tests — threshold boundary
# ---------------------------------------------------------------------------

class TestThreshold:

    def test_exactly_min_matches_accepted(self):
        # Build text that contains exactly _MIN_MATCHES bank_statement keywords
        keywords = list(__import__("app.worker", fromlist=["_FINANCIAL_KEYWORDS"])
                        ._FINANCIAL_KEYWORDS["bank_statement"])[:_MIN_MATCHES]
        text = " ".join(keywords)
        is_fin, category = _classify_text(text)
        assert is_fin is True
        assert category == "bank_statement"

    def test_one_below_threshold_rejected(self):
        keywords = list(__import__("app.worker", fromlist=["_FINANCIAL_KEYWORDS"])
                        ._FINANCIAL_KEYWORDS["bank_statement"])[:_MIN_MATCHES - 1]
        text = " ".join(keywords)
        is_fin, _ = _classify_text(text)
        assert is_fin is False


# ---------------------------------------------------------------------------
# Tests — multi-category text picks best match
# ---------------------------------------------------------------------------

class TestCategorySelection:

    def test_best_category_wins(self):
        # Mix: paystub has more matching keywords than bank_statement
        mixed = PAYSTUB + "\naccount balance\n"
        is_fin, category = _classify_text(mixed)
        assert is_fin is True
        assert category == "paystub"
