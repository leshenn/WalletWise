"""Capitec account statements -> bank-independent WalletWise movements.

This bank adapter alone understands Capitec columns. Main WalletWise services
see ordinary signed transactions, not Capitec-specific money in/out cells.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import pymupdf

from walletwise.extraction.base import BankStatementExtractor
from walletwise.models import ExtractionResult, Transaction, UnassignedRow
from . import parser

# Bank-provided categories are *hints*, not trusted financial-role declarations.
CATEGORY_MAP = {
    "Fees": "Bank Fees", "Groceries": "Groceries", "Fuel": "Fuel",
    "Online Store": "Shopping", "Clothing & Shoes": "Shopping",
    "Restaurants": "Eating Out", "Takeaways": "Eating Out",
    "Card Subscriptions": "Subscriptions", "Digital Subscriptions": "Subscriptions",
    "Cellphone": "Mobile & Internet", "Telephone": "Mobile & Internet",
    "Digital Payments": "Other", "Other Personal & Family": "Other",
    "Cash Withdrawal": "Transfer", "Transfer": "Transfer",
    "Salary": "Salary", "Other Income": "Income", "Allowance": "Income",
    "Cash Deposit": "Transfer", "Refunds": "Refund / Reimbursement",
    "Interest": "Income", "Electricity": "Utilities", "Uncategorised": "Uncategorised",
    "Home Maintenance": "Housing", "Vehicle Maintenance": "Transport",
    "Other Transport": "Transport", "Sport & Hobbies": "Entertainment",
    "Software/Games": "Entertainment", "Betting/Lottery": "Entertainment",
    "Children & Dependants": "Family Support", "Alcohol": "Shopping",
    "Tax": "Other", "Prepaid": "Mobile & Internet",
}


def normalized_transaction(row, *, amount, description, category, fee=False, balance=None):
    dt = datetime.strptime(row.date, "%Y-%m-%d")
    t = Transaction(
        date=dt,
        raw_description=description,
        display_name="",
        category=category,
        category_confidence="high" if category != "Uncategorised" else "unknown",
        category_reason="Capitec statement category" if not fee else "Capitec Fee column",
        amount=round(float(amount), 2),
        transaction_type="credit" if amount > 0 else "debit" if amount < 0 else "unknown",
        balance=round(float(balance if balance is not None else row.balance), 2),
        # Fees are actual signed amounts in separate transactions; bank_charges=0
        # avoids charging them a second time in the Wally bank-fees path.
        bank_charges=0.0,
        bank="Capitec",
        source_page=row.page,
        raw_date=row.raw_date,
        raw_amount=f"{float(amount):.2f}",
        raw_balance=row.raw_balance,
        raw_charges=row.raw_fee if fee else "",
        confidence="medium" if row.issues else "high",
        review_reasons=list(row.issues),
    )
    if fee:
        t.financial_role = "expense" if amount < 0 else "refund"
        t.role_confidence = "high"
        t.role_reason = "Actual posted Capitec fee or fee correction."
    elif row.savings_direction in {"into_savings", "from_savings"}:
        t.financial_role = "transfer"
        t.role_confidence = "high"
        t.role_reason = "Explicit Capitec Live Better savings movement."
        t.savings_direction = "into" if row.savings_direction == "into_savings" else "out"
    elif row.bank_category.strip().casefold() == 'transfer':
        t.financial_role = "transfer"
        t.role_confidence = "high"
        t.role_reason = "Capitec categorised this as an ordinary transfer."
        t.savings_direction = "none"  # Never infer savings from a generic transfer.
    elif row.bank_category.strip().casefold() == 'cash deposit':
        # Physical deposits may represent real money received, not necessarily
        # transfers between user's accounts; flag for review.
        t.review_reasons.append("Review whether cash deposit is income or an own-account transfer")
    return t


class CapitecStatementExtractor(BankStatementExtractor):
    bank_name = "Capitec"

    def __init__(self, app_config):
        self.config = app_config

    def extract(self, pdf_file):
        with pymupdf.open(pdf_file) as pdf:
            header = parser.extract_header(pdf)
            rows, skipped, regions, page_counts = parser.extract_transactions(pdf, header)
            if not rows:
                raise ValueError("No posted transactions found in this Capitec statement.")
            metrics, anomalies = parser.validate(rows, header)
            # Strict integrity: never import financial records with broken ledger totals.
            issues = [name for name in (
                "closing_balance_difference", "money_in_summary_difference",
                "money_out_summary_difference", "fees_summary_difference",
            ) if metrics.get(name) is not None and abs(metrics[name]) > 0.02]
            if anomalies or issues:
                raise ValueError(f"Capitec reconciliation failed: {len(anomalies)} balance mismatches; summary mismatches {issues}. Import cancelled; inspect test parser output.")
            transactions = []
            for row in rows:
                if row.issues and ("outside_verified_statement_period" in row.issues or "invalid_balance" in row.issues):
                    raise ValueError(f"Unverified Capitec transaction on page {row.page}.")
                category = CATEGORY_MAP.get(row.bank_category.strip(), "Uncategorised")
                principal = Decimal(str(row.principal))
                fee = Decimal(str(row.fee))
                # Each posted row has a final balance AFTER both principal and fee.
                # Give principal the intermediate balance and fee the bank balance.
                if principal != 0:
                    transactions.append(normalized_transaction(
                        row, amount=principal, description=row.description,
                        category=category, balance=Decimal(str(row.balance))-fee,
                    ))
                if fee != 0:
                    transactions.append(normalized_transaction(
                        row, amount=fee,
                        description=("Capitec fee correction: " if fee > 0 else "Capitec bank fee: ")+row.description,
                        category="Bank Fees", fee=True,
                    ))
                if principal == 0 and fee == 0:
                    # Some statements contain a legitimate R0 movement; keep it for audit.
                    transactions.append(normalized_transaction(row, amount=0, description=row.description, category=category))
            debug_files = []
            if self.config.generate_debug_pdfs:
                prefix = Path(self.config.debug_prefix)
                debug_path = Path(str(prefix)+"_Capitec_Import_Debug.json")
                debug_path.parent.mkdir(parents=True, exist_ok=True)
                debug_path.write_text(json.dumps({"header": {
                    k:(float(v) if isinstance(v,Decimal) else v) for k,v in header.items() if k != 'raw_header'},
                    "metrics": metrics, "balance_anomalies": anomalies,
                    "unassigned": skipped,
                    "standardized_count": len(transactions),
                    "extraction_rows": [asdict(row) for row in rows],
                }, indent=2, default=str), encoding="utf-8")
                debug_files.append(str(debug_path))
                debug_pdf_path = Path(str(prefix)+"_Capitec_Import_Debug.pdf")
                with pymupdf.open(pdf_file) as debug_pdf:
                    parser.create_debug_pdf(debug_pdf, rows, skipped, regions, debug_pdf_path)
                debug_files.append(str(debug_pdf_path))
            return ExtractionResult(
                bank=self.bank_name, transactions=transactions,
                page_counts=dict(page_counts),
                unassigned_rows=[UnassignedRow(bank=self.bank_name, page=s.get("page",0), raw_date=s.get("date", ""),
                                               raw_description=s.get("description", ""), reason=s.get("reason", "")) for s in skipped if s.get("reason") != "not_transaction_history"],
                debug_files=debug_files,
                statement_period={"verified":True,"start":header["start_date"],"end":header["end_date"],
                                  "source":"Capitec PDF From Date / To Date", "matched_header":"Verified From Date and To Date (PDF page 1)",
                                  "page":1},
            )
