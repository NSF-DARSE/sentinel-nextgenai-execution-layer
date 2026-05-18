"""
generate_applicants.py — produce 5 applicant fixture pairs (paystub + bank statement)
that each exercise a distinct path through the Sentinel scoring pipeline.

Run:
    python tests/generate_applicants.py

Outputs into tests/fixtures/applicants/<name>/ — one paystub + one bank statement
per applicant. Each applicant tests a different decision branch:

    1. clean_approval        → should auto-approve
    2. nsf_flag              → NEEDS_REVIEW (NSF fees)
    3. insufficient_history  → NEEDS_REVIEW (1-month bank statement)
    4. gambling_flag         → NEEDS_REVIEW (gambling transactions)
    5. high_income_clean     → should auto-approve (larger income)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


OUT_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "applicants")


@dataclass
class Paystub:
    employer: str
    employee: str
    pay_period: str
    pay_date: str
    gross: float
    net: float
    federal_tax: float
    state_tax: float
    fica: float


@dataclass
class BankStatement:
    bank: str
    holder: str
    period: str
    period_months: int
    opening: float
    closing: float
    transactions: list[tuple[str, str, float]] = field(default_factory=list)
    nsf_count: int = 0
    overdraft_count: int = 0


def _draw_paystub(path: str, p: Paystub) -> None:
    c = canvas.Canvas(path, pagesize=letter)
    width, height = letter
    y = height - 60

    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, y, f"{p.employer}")
    y -= 22
    c.setFont("Helvetica", 11)
    c.drawString(72, y, "Earnings Statement (Paystub)")
    y -= 30

    c.drawString(72, y, f"Employee: {p.employee}")
    y -= 16
    c.drawString(72, y, f"Pay Period: {p.pay_period}")
    y -= 16
    c.drawString(72, y, f"Pay Date: {p.pay_date}")
    y -= 30

    c.setFont("Helvetica-Bold", 11)
    c.drawString(72, y, "Earnings")
    y -= 18
    c.setFont("Helvetica", 11)
    c.drawString(90, y, f"Gross Pay: ${p.gross:,.2f}")
    y -= 16

    c.setFont("Helvetica-Bold", 11)
    c.drawString(72, y, "Deductions")
    y -= 18
    c.setFont("Helvetica", 11)
    c.drawString(90, y, f"Federal Tax: ${p.federal_tax:,.2f}")
    y -= 14
    c.drawString(90, y, f"State Tax:   ${p.state_tax:,.2f}")
    y -= 14
    c.drawString(90, y, f"FICA:        ${p.fica:,.2f}")
    y -= 24

    c.setFont("Helvetica-Bold", 12)
    c.drawString(72, y, f"Net Pay: ${p.net:,.2f}")
    y -= 30

    c.setFont("Helvetica-Oblique", 9)
    c.drawString(72, y, "This document is a test fixture generated for the Sentinel demo pipeline.")

    c.showPage()
    c.save()


def _draw_bank_statement(path: str, s: BankStatement) -> None:
    c = canvas.Canvas(path, pagesize=letter)
    width, height = letter
    y = height - 60

    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, y, s.bank)
    y -= 22
    c.setFont("Helvetica", 11)
    c.drawString(72, y, "Checking Account Statement")
    y -= 30

    c.drawString(72, y, f"Account Holder: {s.holder}")
    y -= 16
    c.drawString(72, y, f"Statement Period: {s.period}  ({s.period_months} months)")
    y -= 24

    c.setFont("Helvetica-Bold", 11)
    c.drawString(72, y, "Account Summary")
    y -= 18
    c.setFont("Helvetica", 11)
    c.drawString(90, y, f"Opening Balance: ${s.opening:,.2f}")
    y -= 14
    c.drawString(90, y, f"Closing Balance: ${s.closing:,.2f}")
    y -= 14
    c.drawString(90, y, f"NSF Fee Occurrences: {s.nsf_count}")
    y -= 14
    c.drawString(90, y, f"Overdraft Occurrences: {s.overdraft_count}")
    y -= 24

    c.setFont("Helvetica-Bold", 11)
    c.drawString(72, y, "Transaction History")
    y -= 18
    c.setFont("Helvetica", 10)
    for date, desc, amount in s.transactions:
        if y < 80:
            c.showPage()
            y = height - 60
            c.setFont("Helvetica", 10)
        sign = "-" if amount < 0 else "+"
        c.drawString(90, y, f"{date}   {desc:<32}   {sign}${abs(amount):,.2f}")
        y -= 13

    c.showPage()
    c.save()


# ── Applicant fixtures ─────────────────────────────────────────────────────────

APPLICANTS = [
    # 1. Clean approval — 3-month bank statement, decent income, no flags
    (
        "01_clean_approval",
        Paystub(
            employer="Northwind Trading Co.",
            employee="Alex Johnson",
            pay_period="2026-04-01 to 2026-04-15",
            pay_date="2026-04-16",
            gross=2800.00, net=2150.00,
            federal_tax=420.00, state_tax=140.00, fica=90.00,
        ),
        BankStatement(
            bank="Heritage National Bank",
            holder="Alex Johnson",
            period="February 1 2026 – April 30 2026",
            period_months=3,
            opening=4200.00, closing=4850.00,
            transactions=[
                ("2026-02-03", "Direct Deposit - Northwind",  2150.00),
                ("2026-02-15", "Rent Payment",               -1450.00),
                ("2026-02-17", "Direct Deposit - Northwind",  2150.00),
                ("2026-03-01", "Grocery Purchase",             -180.55),
                ("2026-03-12", "Utility Bill",                 -120.00),
                ("2026-03-16", "Direct Deposit - Northwind",  2150.00),
                ("2026-04-01", "Rent Payment",               -1450.00),
                ("2026-04-16", "Direct Deposit - Northwind",  2150.00),
            ],
        ),
    ),

    # 2. NSF flag — 3-month statement, income fine, but NSF events present
    (
        "02_nsf_flag",
        Paystub(
            employer="Riverbend Logistics LLC",
            employee="Maria Lopez",
            pay_period="2026-04-08 to 2026-04-22",
            pay_date="2026-04-23",
            gross=2400.00, net=1850.00,
            federal_tax=350.00, state_tax=120.00, fica=80.00,
        ),
        BankStatement(
            bank="Cascade Federal Credit Union",
            holder="Maria Lopez",
            period="February 1 2026 – April 30 2026",
            period_months=3,
            opening=850.00, closing=420.00,
            nsf_count=3, overdraft_count=2,
            transactions=[
                ("2026-02-09", "Direct Deposit - Riverbend",  1850.00),
                ("2026-02-14", "NSF Fee",                       -35.00),
                ("2026-02-22", "Overdraft Fee",                 -30.00),
                ("2026-03-05", "Direct Deposit - Riverbend",  1850.00),
                ("2026-03-11", "NSF Fee",                       -35.00),
                ("2026-03-19", "Direct Deposit - Riverbend",  1850.00),
                ("2026-04-02", "NSF Fee",                       -35.00),
                ("2026-04-10", "Overdraft Fee",                 -30.00),
            ],
        ),
    ),

    # 3. Insufficient history — only 1 month of bank data
    (
        "03_insufficient_history",
        Paystub(
            employer="Atlas Software Inc.",
            employee="Devon Pierce",
            pay_period="2026-04-15 to 2026-04-29",
            pay_date="2026-04-30",
            gross=3600.00, net=2700.00,
            federal_tax=540.00, state_tax=180.00, fica=180.00,
        ),
        BankStatement(
            bank="Lakeshore Bank & Trust",
            holder="Devon Pierce",
            period="April 1 2026 – April 30 2026",
            period_months=1,
            opening=5200.00, closing=5980.00,
            transactions=[
                ("2026-04-02", "Direct Deposit - Atlas",      2700.00),
                ("2026-04-05", "Rent Payment",               -1800.00),
                ("2026-04-12", "Grocery Purchase",             -210.00),
                ("2026-04-16", "Direct Deposit - Atlas",      2700.00),
                ("2026-04-22", "Utility Bill",                 -110.00),
            ],
        ),
    ),

    # 4. Gambling flag — clean income, but bank shows casino/sportsbook activity
    (
        "04_gambling_flag",
        Paystub(
            employer="Pinewood Construction Group",
            employee="Sam Patel",
            pay_period="2026-04-01 to 2026-04-15",
            pay_date="2026-04-16",
            gross=3100.00, net=2350.00,
            federal_tax=460.00, state_tax=160.00, fica=130.00,
        ),
        BankStatement(
            bank="First Plains Bank",
            holder="Sam Patel",
            period="February 1 2026 – April 30 2026",
            period_months=3,
            opening=2400.00, closing=1100.00,
            transactions=[
                ("2026-02-04", "Direct Deposit - Pinewood",  2350.00),
                ("2026-02-11", "DraftKings Sportsbook Bet",  -200.00),
                ("2026-02-19", "BetMGM Casino Withdrawal",   -350.00),
                ("2026-03-04", "Direct Deposit - Pinewood",  2350.00),
                ("2026-03-14", "FanDuel Sportsbook Bet",     -250.00),
                ("2026-03-28", "Pokerstars Deposit",         -300.00),
                ("2026-04-03", "Direct Deposit - Pinewood",  2350.00),
                ("2026-04-18", "DraftKings Sportsbook Bet",  -180.00),
            ],
        ),
    ),

    # 5. High income clean — 6-month statement with comfortable cushion
    (
        "05_high_income_clean",
        Paystub(
            employer="Meridian Financial Partners",
            employee="Priya Anand",
            pay_period="2026-04-01 to 2026-04-15",
            pay_date="2026-04-16",
            gross=6800.00, net=4950.00,
            federal_tax=1200.00, state_tax=380.00, fica=270.00,
        ),
        BankStatement(
            bank="Coastline Trust Bank",
            holder="Priya Anand",
            period="November 1 2025 – April 30 2026",
            period_months=6,
            opening=18400.00, closing=22650.00,
            transactions=[
                ("2025-11-15", "Direct Deposit - Meridian",   4950.00),
                ("2025-12-15", "Direct Deposit - Meridian",   4950.00),
                ("2026-01-15", "Direct Deposit - Meridian",   4950.00),
                ("2026-02-15", "Direct Deposit - Meridian",   4950.00),
                ("2026-03-15", "Direct Deposit - Meridian",   4950.00),
                ("2026-04-16", "Direct Deposit - Meridian",   4950.00),
                ("2026-01-10", "Mortgage Payment",           -2400.00),
                ("2026-02-10", "Mortgage Payment",           -2400.00),
                ("2026-03-10", "Mortgage Payment",           -2400.00),
                ("2026-04-10", "Mortgage Payment",           -2400.00),
                ("2026-03-22", "529 Plan Contribution",       -500.00),
            ],
        ),
    ),
]


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    for name, paystub, statement in APPLICANTS:
        applicant_dir = os.path.join(OUT_DIR, name)
        os.makedirs(applicant_dir, exist_ok=True)
        _draw_paystub(os.path.join(applicant_dir, "paystub.pdf"), paystub)
        _draw_bank_statement(os.path.join(applicant_dir, "bank_statement.pdf"), statement)
        print(f"  ✓ {name}/")
    print(f"\nGenerated {len(APPLICANTS)} applicant pairs in {OUT_DIR}")


if __name__ == "__main__":
    main()
