"""
generate_fixtures.py — Synthetic demo PDFs for Sentinel pipeline testing.

All data is completely fabricated. No real people, accounts, or institutions.
Routing numbers are deliberately invalid (fail ABA checksum).

Run from repo root:
    python tests/fixtures/generate_fixtures.py

Generates:
    bank_statement.pdf          — clean bank statement, pipeline should PASS
    bank_statement_flagged.pdf  — balance discrepancy, triggers INTEGRITY_FAIL → NEEDS_REVIEW
    paystub.pdf                 — clean paystub, pipeline should PASS
    non_financial.pdf           — restaurant receipt, rejected by relevance classifier
"""
from __future__ import annotations

import os
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

OUT_DIR = Path(__file__).parent
W, H = letter

STYLES = getSampleStyleSheet()

def _style(name, **kwargs):
    base = STYLES[name]
    return ParagraphStyle(name + "_custom", parent=base, **kwargs)

TITLE  = _style("Heading1", fontSize=14, spaceAfter=2)
H2     = _style("Heading2", fontSize=11, spaceAfter=2)
BODY   = _style("Normal",   fontSize=9,  spaceAfter=2)
SMALL  = _style("Normal",   fontSize=8,  textColor=colors.HexColor("#555555"))
MONO   = _style("Code",     fontSize=8,  spaceAfter=1)

def _doc(filename: str):
    path = OUT_DIR / filename
    return SimpleDocTemplate(
        str(path), pagesize=letter,
        rightMargin=0.75*inch, leftMargin=0.75*inch,
        topMargin=0.75*inch, bottomMargin=0.75*inch,
    )

def _hr():
    return HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc"), spaceAfter=6)

def _table(data, col_widths, header_bg=colors.HexColor("#1565c0")):
    t = Table(data, colWidths=col_widths)
    t.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, 0),  header_bg),
        ("TEXTCOLOR",   (0, 0), (-1, 0),  colors.white),
        ("FONTNAME",    (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",    (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
        ("GRID",        (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd")),
        ("TOPPADDING",  (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


# ── 1. bank_statement.pdf ─────────────────────────────────────────────────────

def make_bank_statement():
    story = []

    story += [
        Paragraph("FIRST DEMO BANK, N.A.", TITLE),
        Paragraph("Account Statement — March 1–31, 2025", H2),
        _hr(),
        Spacer(1, 6),
    ]

    # Account details — contains PII that will be redacted
    info = [
        ["Account Holder:", "Alex Johnson"],
        ["Address:",        "742 Evergreen Terrace, Springfield, IL 62701"],
        ["Checking Account:", "**** **** **** 2891"],
        ["Routing Number:", "099000999"],   # deliberately invalid ABA number
        ["Statement Period:", "March 1, 2025 – March 31, 2025"],
        ["SSN (last 4):", "7291"],
    ]
    t = Table(info, colWidths=[2*inch, 4.5*inch])
    t.setStyle(TableStyle([
        ("FONTSIZE",      (0, 0), (-1, -1), 9),
        ("FONTNAME",      (0, 0), (0, -1),  "Helvetica-Bold"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
    ]))
    story += [t, Spacer(1, 10), _hr(), Spacer(1, 6)]

    # Balance summary
    story += [Paragraph("Account Summary", H2)]
    summary = Table(
        [["Beginning Balance", "Total Deposits", "Total Withdrawals", "Ending Balance"],
         ["$4,250.00",         "$6,400.00",      "$2,617.16",         "$8,032.84"]],
        colWidths=[1.6*inch]*4,
    )
    summary.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, 0), colors.HexColor("#1565c0")),
        ("TEXTCOLOR",   (0, 0), (-1, 0), colors.white),
        ("FONTNAME",    (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME",    (0, 1), (-1, 1), "Helvetica-Bold"),
        ("FONTSIZE",    (0, 0), (-1, -1), 9),
        ("ALIGN",       (0, 0), (-1, -1), "CENTER"),
        ("GRID",        (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd")),
        ("TOPPADDING",  (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story += [summary, Spacer(1, 10), _hr(), Spacer(1, 6)]

    # Transactions — $4,250 + $3,200 + $3,200 - $1,400 - $87.43 - $142 - $500
    #              - $15.99 - $200 - $6.75 - $185 - $79.99 = $8,032.84  ✓
    story += [Paragraph("Transaction History", H2)]
    txns = [
        ["Date",  "Description",                        "Deposits",  "Withdrawals", "Balance"],
        ["03/01", "Direct Deposit – Payroll",            "$3,200.00", "",            "$7,450.00"],
        ["03/03", "Rent Payment – Oakview Properties",  "",          "$1,400.00",   "$6,050.00"],
        ["03/05", "City Grocery Market",                "",          "$87.43",      "$5,962.57"],
        ["03/08", "Midwest Electric & Gas",             "",          "$142.00",     "$5,820.57"],
        ["03/12", "Transfer to Savings",                "",          "$500.00",     "$5,320.57"],
        ["03/15", "StreamFlix Subscription",            "",          "$15.99",      "$5,304.58"],
        ["03/18", "ATM Withdrawal",                     "",          "$200.00",     "$5,104.58"],
        ["03/22", "Corner Coffee Co.",                  "",          "$6.75",       "$5,097.83"],
        ["03/25", "Direct Deposit – Payroll",            "$3,200.00", "",            "$8,297.83"],
        ["03/28", "Auto Insurance – SafeDrive Co.",     "",          "$185.00",     "$8,112.83"],
        ["03/30", "Broadband Internet Service",         "",          "$79.99",      "$8,032.84"],
    ]
    story += [
        _table(txns, [0.7*inch, 2.5*inch, 1.0*inch, 1.1*inch, 1.0*inch]),
        Spacer(1, 12),
        Paragraph("Available Balance: $8,032.84", _style("Normal", fontSize=10, fontName="Helvetica-Bold")),
        Spacer(1, 12),
        Paragraph(
            "This statement is provided for informational purposes. "
            "Please report discrepancies within 30 days.",
            SMALL,
        ),
    ]

    _doc("bank_statement.pdf").build(story)
    print("  ✓ bank_statement.pdf")


# ── 2. bank_statement_flagged.pdf ─────────────────────────────────────────────
# Intentional integrity issue: summary reports total withdrawals as $2,500.00
# but individual transactions sum to $2,617.16 — a $117.16 discrepancy.
# Gemini should set document_integrity_flag=True → INTEGRITY_FAIL → NEEDS_REVIEW.

def make_bank_statement_flagged():
    story = []

    story += [
        Paragraph("FIRST DEMO BANK, N.A.", TITLE),
        Paragraph("Account Statement — February 1–28, 2025", H2),
        _hr(), Spacer(1, 6),
    ]

    info = [
        ["Account Holder:", "Morgan Reyes"],
        ["Address:",        "88 Birchwood Lane, Lakewood, CO 80214"],
        ["Checking Account:", "**** **** **** 5507"],
        ["Routing Number:", "099000999"],
        ["Statement Period:", "February 1, 2025 – February 28, 2025"],
    ]
    t = Table(info, colWidths=[2*inch, 4.5*inch])
    t.setStyle(TableStyle([
        ("FONTSIZE",      (0, 0), (-1, -1), 9),
        ("FONTNAME",      (0, 0), (0, -1),  "Helvetica-Bold"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
    ]))
    story += [t, Spacer(1, 10), _hr(), Spacer(1, 6)]

    # Summary intentionally wrong: withdrawals listed as $2,500.00 but actual = $2,617.16
    story += [Paragraph("Account Summary", H2)]
    summary = Table(
        [["Beginning Balance", "Total Deposits", "Total Withdrawals", "Ending Balance"],
         ["$5,100.00",         "$3,200.00",      "$2,500.00",         "$5,783.00"]],
        colWidths=[1.6*inch]*4,
    )
    summary.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0),  colors.HexColor("#c62828")),
        ("TEXTCOLOR",     (0, 0), (-1, 0),  colors.white),
        ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTNAME",      (0, 1), (-1, 1),  "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, -1), 9),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("GRID",          (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd")),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story += [summary, Spacer(1, 10), _hr(), Spacer(1, 6)]

    # Actual withdrawals sum = $1,400 + $87.43 + $142 + $500 + $15.99 + $200 + $6.75 + $185 + $79.99 = $2,617.16
    # But summary says $2,500.00
    story += [Paragraph("Transaction History", H2)]
    txns = [
        ["Date",  "Description",                       "Deposits",  "Withdrawals", "Balance"],
        ["02/01", "Direct Deposit – Payroll",           "$3,200.00", "",            "$8,300.00"],
        ["02/03", "Rent Payment – Birchwood Mgmt",     "",          "$1,400.00",   "$6,900.00"],
        ["02/05", "City Grocery Market",               "",          "$87.43",      "$6,812.57"],
        ["02/08", "Midwest Electric & Gas",            "",          "$142.00",     "$6,670.57"],
        ["02/12", "Transfer to Savings",               "",          "$500.00",     "$6,170.57"],
        ["02/15", "StreamFlix Subscription",           "",          "$15.99",      "$6,154.58"],
        ["02/18", "ATM Withdrawal",                    "",          "$200.00",     "$5,954.58"],
        ["02/22", "Corner Coffee Co.",                 "",          "$6.75",       "$5,947.83"],
        ["02/26", "Auto Insurance – SafeDrive Co.",    "",          "$185.00",     "$5,762.83"],
        ["02/28", "Broadband Internet Service",        "",          "$79.99",      "$5,682.84"],
    ]
    story += [
        _table(txns, [0.7*inch, 2.5*inch, 1.0*inch, 1.1*inch, 1.0*inch]),
        Spacer(1, 12),
        Paragraph("Ending Balance: $5,783.00 | Available Balance: $5,783.00",
                  _style("Normal", fontSize=10, fontName="Helvetica-Bold")),
        Spacer(1, 6),
        Paragraph(
            "Note: The summary total withdrawals ($2,500.00) does not match "
            "the sum of individual withdrawal transactions ($2,617.16).",
            _style("Normal", fontSize=8, textColor=colors.HexColor("#c62828")),
        ),
    ]

    _doc("bank_statement_flagged.pdf").build(story)
    print("  ✓ bank_statement_flagged.pdf  (INTEGRITY_FAIL expected)")


# ── 3. paystub.pdf ────────────────────────────────────────────────────────────

def make_paystub():
    story = []

    story += [
        Paragraph("ACME FINANCIAL CORP", TITLE),
        Paragraph("Employee Earnings Statement", H2),
        _hr(), Spacer(1, 6),
    ]

    info = [
        ["Employee:",       "Sam Rivera"],
        ["Employee ID:",    "EMP-4821"],
        ["SSN:",            "***-**-8834"],
        ["Address:",        "310 Willow Creek Rd, Newark, DE 19711"],
        ["Pay Period:",     "March 1–15, 2025"],
        ["Pay Date:",       "March 20, 2025"],
        ["Department:",     "Loan Operations"],
        ["Pay Frequency:",  "Biweekly"],
    ]
    t = Table(info, colWidths=[2*inch, 4.5*inch])
    t.setStyle(TableStyle([
        ("FONTSIZE",      (0, 0), (-1, -1), 9),
        ("FONTNAME",      (0, 0), (0, -1),  "Helvetica-Bold"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
    ]))
    story += [t, Spacer(1, 10), _hr(), Spacer(1, 6)]

    story += [Paragraph("Earnings", H2)]
    earnings = [
        ["Description",          "Hours", "Rate",   "Current",    "YTD"],
        ["Regular Pay",          "80",    "$46.875", "$3,750.00",  "$7,500.00"],
        ["Gross Pay",            "",      "",        "$3,750.00",  "$7,500.00"],
    ]
    story += [
        _table(earnings, [2.5*inch, 0.6*inch, 0.8*inch, 1.1*inch, 1.2*inch]),
        Spacer(1, 8),
    ]

    story += [Paragraph("Deductions", H2)]
    deductions = [
        ["Description",              "Current",    "YTD"],
        ["Federal Income Tax",       "-$450.00",   "-$900.00"],
        ["State Income Tax",         "-$187.50",   "-$375.00"],
        ["Social Security (6.2%)",   "-$232.50",   "-$465.00"],
        ["Medicare (1.45%)",         "-$54.38",    "-$108.76"],
        ["Health Insurance",         "-$125.00",   "-$250.00"],
        ["401(k) Contribution (5%)", "-$187.50",   "-$375.00"],
        ["Total Deductions",         "-$1,236.88", "-$2,473.76"],
    ]
    story += [
        _table(deductions, [3.0*inch, 1.3*inch, 1.3*inch]),
        Spacer(1, 10), _hr(), Spacer(1, 6),
    ]

    net = Table(
        [["NET PAY", "$2,513.12"]],
        colWidths=[3.0*inch, 3.5*inch],
    )
    net.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), colors.HexColor("#e8f5e9")),
        ("FONTNAME",      (0, 0), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, -1), 12),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING",    (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("GRID",          (0, 0), (-1, -1), 0.5, colors.HexColor("#2e7d32")),
    ]))
    story += [
        net, Spacer(1, 6),
        Paragraph("YTD Gross: $7,500.00 | YTD Net: $5,026.24", SMALL),
        Spacer(1, 12),
        Paragraph(
            "Direct deposit to Checking Account **** **** **** 7743 | "
            "Routing Number: 099000999",
            SMALL,
        ),
    ]

    _doc("paystub.pdf").build(story)
    print("  ✓ paystub.pdf")


# ── 4. non_financial.pdf ─────────────────────────────────────────────────────
# Restaurant receipt — should be rejected by the relevance classifier.

def make_non_financial():
    story = []

    story += [
        Paragraph("THE CORNER BISTRO", TITLE),
        Paragraph("789 Oak Avenue, Wilmington, DE 19801", BODY),
        Paragraph("Tel: (302) 555-0187", BODY),
        _hr(), Spacer(1, 6),
    ]

    meta = [
        ["Date:",   "March 15, 2025"],
        ["Table:",  "12"],
        ["Server:", "Mike T."],
        ["Guests:", "2"],
    ]
    t = Table(meta, colWidths=[1.0*inch, 5.5*inch])
    t.setStyle(TableStyle([
        ("FONTSIZE",      (0, 0), (-1, -1), 9),
        ("FONTNAME",      (0, 0), (0, -1),  "Helvetica-Bold"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story += [t, Spacer(1, 10), _hr(), Spacer(1, 6)]

    items = [
        ["Item",                      "Qty", "Price"],
        ["Grilled Atlantic Salmon",   "1",   "$28.00"],
        ["House Garden Salad",        "1",   "$9.50"],
        ["Sparkling Mineral Water",   "2",   "$10.00"],
        ["Tiramisu",                  "1",   "$8.00"],
        ["Espresso",                  "2",   "$7.00"],
    ]
    story += [
        _table(items, [4.0*inch, 0.6*inch, 1.0*inch], header_bg=colors.HexColor("#37474f")),
        Spacer(1, 10),
    ]

    totals = [
        ["Subtotal",    "$62.50"],
        ["Tax (8%)",    "$5.00"],
        ["Total",       "$67.50"],
        ["Tip (20%)",   "$13.50"],
        ["Grand Total", "$81.00"],
    ]
    tot = Table(totals, colWidths=[5.0*inch, 1.5*inch])
    tot.setStyle(TableStyle([
        ("FONTSIZE",      (0, 0), (-1, -1), 9),
        ("FONTNAME",      (0, -1), (-1, -1), "Helvetica-Bold"),
        ("ALIGN",         (1, 0), (1, -1),  "RIGHT"),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story += [
        tot, Spacer(1, 12), _hr(), Spacer(1, 6),
        Paragraph("Thank you for dining with us! We hope to see you again soon.", SMALL),
        Paragraph("Please inform your server of any food allergies.", SMALL),
    ]

    _doc("non_financial.pdf").build(story)
    print("  ✓ non_financial.pdf  (relevance rejection expected)")


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"Generating fixtures in {OUT_DIR}/")
    make_bank_statement()
    make_bank_statement_flagged()
    make_paystub()
    make_non_financial()
    print("\nDone. All data is synthetic — no real PII.")
