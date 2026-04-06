"""
Unit tests for app/authenticator.py

Run from repo root (no Docker needed — pure Python logic, no external deps except pypdf):
    pip install pypdf
    python tests/test_authenticator.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src/api"))

from app.authenticator import authenticate_document, _classify_document, _reconcile_balance

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


# ── Document type classification ──────────────────────────────────────────────

section("Document type — bank statement")
text = "Routing Number: 021000089 Account Number: 1234 Opening Balance $4,218.77 Total Deposits $4,922.56 Total Withdrawals $3,500.00 Closing Balance $5,641.33 Statement Period March 2025"
doc_type, conf = _classify_document(text)
check("Classified as bank_statement", doc_type == "bank_statement", f"got: {doc_type} ({conf:.0%})")
check("Confidence >= 0.5", conf >= 0.5, f"got: {conf}")

section("Document type — paystub")
text = "Gross Pay: $3,200.00  Net Pay: $2,461.28  YTD Earnings: $9,600.00  Federal Income Tax: $480.00  FICA: $244.80  Pay Period: 03/01 - 03/15"
doc_type, conf = _classify_document(text)
check("Classified as paystub", doc_type == "paystub", f"got: {doc_type} ({conf:.0%})")
check("Confidence >= 0.5", conf >= 0.5, f"got: {conf}")

section("Document type — W2")
text = "Wages, Tips: $52,000  Federal Income Tax Withheld: $6,240  Social Security Wages: $52,000  Medicare Wages: $52,000  Employer Identification Number: 12-3456789  W-2 Wage and Tax Statement"
doc_type, conf = _classify_document(text)
check("Classified as w2", doc_type == "w2", f"got: {doc_type} ({conf:.0%})")
check("Confidence >= 0.5", conf >= 0.5, f"got: {conf}")

section("Document type — tax return")
text = "Form 1040 U.S. Individual Income Tax Return  Adjusted Gross Income: $68,500  Standard Deduction: $13,850  Taxable Income: $54,650  Tax Liability: $7,200  Filing Status: Single"
doc_type, conf = _classify_document(text)
check("Classified as tax_return", doc_type == "tax_return", f"got: {doc_type} ({conf:.0%})")

section("Document type — unknown (restaurant receipt)")
text = "TGI Fridays  Table 12  Server: Alex  Subtotal: $45.00  Tax: $3.60  Tip: $9.00  Total: $57.60  Thank you for dining with us!"
doc_type, conf = _classify_document(text)
check("Not classified as financial doc", doc_type == "unknown" or conf < 0.3, f"got: {doc_type} ({conf:.0%})")


# ── Balance reconciliation ────────────────────────────────────────────────────

section("Balance reconciliation — numbers add up")
text = "Opening Balance $4,218.77  Total Deposits $4,922.56  Total Withdrawals $3,500.00  Closing Balance $5,641.33"
result = _reconcile_balance(text)
check("Reconciled = True",  result["reconciled"] is True,  f"delta: {result.get('delta')}")
check("Delta < $1.00",      result.get("delta") is not None and result["delta"] < 1.0, f"delta: {result.get('delta')}")

section("Balance reconciliation — numbers don't add up (tampered)")
text = "Opening Balance $4,218.77  Total Deposits $4,922.56  Total Withdrawals $3,500.00  Closing Balance $9,999.99"
result = _reconcile_balance(text)
check("Reconciled = False", result["reconciled"] is False, f"delta: {result.get('delta')}, note: {result.get('note')}")
check("Delta > $1.00",      (result.get("delta") or 0) > 1.0, f"delta: {result.get('delta')}")

section("Balance reconciliation — missing fields")
text = "Some bank statement without clear balance fields  Deposits this month: various  Withdrawals: various"
result = _reconcile_balance(text)
check("Reconciled = None (can't determine)", result["reconciled"] is None, f"got: {result.get('reconciled')}")


# ── Full authenticate_document function ───────────────────────────────────────

section("Full auth — clean bank statement (no PDF bytes, metadata skipped)")
text = (
    "First National Bank  Routing Number: 021000089  Account Number: **** 4408  "
    "Statement Period: March 1–31, 2025  "
    "Opening Balance $4,218.77  Total Deposits $4,922.56  Total Withdrawals $3,500.00  Closing Balance $5,641.33  "
    "Transaction History  03/03 Direct Deposit ACME Corp 2,461.28"
)
report = authenticate_document(text, b"")  # empty bytes = metadata skipped gracefully
check("authentic = True",        report["authentic"] is True,             f"confidence: {report['confidence']}, flags: {report['flags']}")
check("document_type correct",   report["document_type"] == "bank_statement", f"got: {report['document_type']}")
check("balance reconciled",      report["balance"]["reconciled"] is True, f"balance: {report['balance']}")

section("Full auth — tampered bank statement (balance mismatch)")
text = (
    "First National Bank  Routing Number: 021000089  Account Number: **** 4408  "
    "Statement Period: March 2025  "
    "Opening Balance $4,218.77  Total Deposits $4,922.56  Total Withdrawals $3,500.00  Closing Balance $9,999.99"
)
report = authenticate_document(text, b"")
check("authentic = False",        report["authentic"] is False,           f"confidence: {report['confidence']}")
check("balance mismatch flagged", any("mismatch" in f.lower() for f in report["flags"]), f"flags: {report['flags']}")
check("confidence reduced",       report["confidence"] < 0.7,            f"confidence: {report['confidence']}")

section("Full auth — unknown document type")
text = "TGI Fridays  Server: Alex  Subtotal: $45.00  Tax: $3.60  Total: $57.60"
report = authenticate_document(text, b"")
check("authentic = False",   report["authentic"] is False,  f"confidence: {report['confidence']}")
check("type = unknown",      report["document_type"] == "unknown", f"got: {report['document_type']}")
check("flag present",        len(report["flags"]) > 0,      f"flags: {report['flags']}")

section("Audit report structure")
text = "Routing Number: 021000089  Opening Balance $1,000  Total Deposits $500  Total Withdrawals $200  Closing Balance $1,300"
report = authenticate_document(text, b"")
required_keys = {"authentic", "confidence", "flags", "document_type", "type_confidence", "balance", "pdf_metadata"}
check("All required keys present", required_keys.issubset(report.keys()), f"keys: {list(report.keys())}")
check("pdf_metadata is dict",      isinstance(report["pdf_metadata"], dict), f"got: {type(report['pdf_metadata'])}")


# ── Summary ───────────────────────────────────────────────────────────────────

section("Summary")
passed = sum(results)
total  = len(results)
print(f"\n{passed}/{total} tests passed")
if passed < total:
    sys.exit(1)
else:
    print("\nAll tests pass ✅")
