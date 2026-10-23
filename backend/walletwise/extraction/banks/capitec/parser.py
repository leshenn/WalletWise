"""Capitec layout/parser core, ported from the verified standalone test.

Run: python Capitec_Statement_Test.py /path/to/capitec.pdf --output output_folder
Requires: pymupdf (pip install pymupdf)
Optional: artifact_tool (generates XLSX; CSV/JSON/PDF always generated)

Called by the production bank adapter; never writes to the database itself.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pymupdf

DATE_PATTERN = re.compile(r"^\d{2}/\d{2}/\d{4}$")
FIELDS = ("date", "description", "category", "money_in", "money_out", "fee", "balance")
# Capitec's PDF-coordinate columns, observed in the 445.92 pt account statement layout.
# Prefer the column headers as anchors and adapt proportionally to page width.
BASE_PAGE_WIDTH = 445.92
COLUMN_EDGES = (22.0, 59.5, 218.0, 278.0, 319.5, 369.5, 393.0, 425.0)
MONEY_PATTERN = re.compile(r"^[+-]?(?:R)?\s*\d[\d\s,]*(?:\.\d{1,2})?\*?$", re.I)


def squish(s):
    return " ".join(str(s or "").split())


def money(raw):
    """Parse Capitec monetary cells (spaces as thousands separators; optional * footnote)."""
    s = re.sub(r"\s+", "", str(raw or "")).replace(",", "").replace("*", "").replace("R", "")
    if not s:
        return None
    if not re.fullmatch(r"[+-]?\d+(?:\.\d{1,2})?", s):
        return None
    try:
        return Decimal(s).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def number(v):
    return float(v) if v is not None else None


def rand(v):
    return f"R{v:,.2f}" if v is not None else "n/a"


def extract_header(pdf):
    if not pdf:
        raise ValueError("PDF is empty")
    text = pdf[0].get_text(sort=True)
    if "Main Account Statement" not in text or "Capitec" not in text:
        raise ValueError("Not a recognised Capitec Main Account Statement.")
    found = {}
    for label, pattern in {
        "start_date": r"From\s+Date:\s*(\d{2}/\d{2}/\d{4})",
        "end_date": r"To\s+Date:\s*(\d{2}/\d{2}/\d{4})",
        "opening_balance": r"Opening\s+Balance:\s*(R\s*[\d\s,]+\.\d{2})",
        "closing_balance": r"Closing\s+Balance:\s*(R\s*[\d\s,]+\.\d{2})",
        "money_in_summary": r"Money\s+In\s+Summary\s*(R\s*[\d\s,]+\.\d{2})",
        "money_out_summary": r"Money\s+Out\s+Summary\s*(-?R\s*[\d\s,]+\.\d{2})",
        "fee_summary": r"Total\s+Fees\s*(-?R\s*[\d\s,]+\.\d{2})",
    }.items():
        match = re.search(pattern, text, flags=re.I)
        if match:
            found[label] = match.group(1)
    required = ("start_date", "end_date", "opening_balance", "closing_balance")
    missing = [k for k in required if k not in found]
    if missing:
        raise ValueError("Could not verify statement fields: " + ", ".join(missing))
    start = datetime.strptime(found["start_date"], "%d/%m/%Y").date()
    end = datetime.strptime(found["end_date"], "%d/%m/%Y").date()
    if end < start:
        raise ValueError("Statement ends before it starts")
    parsed = {k: money(found[k]) for k in ("opening_balance", "closing_balance", "money_in_summary", "money_out_summary", "fee_summary") if k in found}
    return {"bank": "Capitec", "start_date": start.isoformat(), "end_date": end.isoformat(),
            "raw_header": found, **parsed, "page_count": len(pdf)}


def get_table_region(page, page_number):
    """Only transaction history is eligible; never scheduled payments or pending transactions."""
    text = page.get_text(sort=True)
    if "Money In" not in text or "Money Out" not in text or "Fee*" not in text or "Balance" not in text:
        return None
    words = page.get_text("words", sort=False)
    heading = [w for w in words if w[4] == "Date" and w[0] < 60]
    if not heading:
        return None
    header = min(heading, key=lambda w: w[1])
    # Date/Description/Money In/Money Out/Fee/Balance on the same heading line.
    vicinity = [w[4] for w in words if abs(w[1] - header[1]) < 3 and w[0] < 430]
    if not {"Description", "Category", "Balance", "Fee*"}.issubset(set(vicinity)):
        return None
    start_y = header[3] - 0.2
    end_y = 581.0
    end_reason = "end of transaction table"
    for needle in ("Pending Card Transactions", "* Includes VAT", "24hr Client Care Centre"):
        # fit text segments based on page text search - note PDF header/footers are different spans
        matches = page.search_for(needle)
        matches = [r for r in matches if r.y0 > start_y]
        if matches:
            candidate = min(r.y0 for r in matches) - 1.8
            if candidate < end_y:
                end_y, end_reason = candidate, needle
    return {"start_y": start_y, "end_y": max(start_y, end_y), "end_reason": end_reason, "page": page_number}


def group_lines(page, bounds):
    width_factor = page.rect.width / BASE_PAGE_WIDTH
    edges = [x * width_factor for x in COLUMN_EDGES]
    words = [w for w in page.get_text("words", sort=False)
             if bounds["start_y"] < (w[1] + w[3]) / 2 < bounds["end_y"]
             and edges[0] <= (w[0] + w[2]) / 2 <= edges[-1]]
    words.sort(key=lambda w: (w[1], w[0]))
    lines = []
    for w in words:
        if lines and abs(w[1] - lines[-1]["y0"]) < 2.2:
            lines[-1]["words"].append(w)
            lines[-1]["y1"] = max(lines[-1]["y1"], w[3])
        else:
            lines.append({"y0": w[1], "y1": w[3], "words": [w]})
    for line in lines:
        cells = {f: [] for f in FIELDS}
        for w in sorted(line["words"], key=lambda w: w[0]):
            x = (w[0] + w[2]) / 2
            for i, field in enumerate(FIELDS):
                if edges[i] <= x < edges[i + 1]:
                    cells[field].append(w[4]); break
        line["cells"] = {k: squish(" ".join(v)) for k, v in cells.items()}
        del line["words"]
    return lines


@dataclass
class Transaction:
    page: int
    y0: float
    y1: float
    raw_date: str
    date: str
    description: str = ""
    bank_category: str = ""
    raw_money_in: str = ""
    raw_money_out: str = ""
    raw_fee: str = ""
    raw_balance: str = ""
    money_in: float = 0.0
    money_out: float = 0.0
    fee: float = 0.0
    balance: float | None = None
    principal: float = 0.0
    net_movement: float = 0.0
    reporting_amount: float = 0.0
    flow_type: str = ""
    savings_direction: str = "not_savings"
    confidence: str = "high"
    issues: list[str] = field(default_factory=list)
    balance_difference: float | None = None


def recognise_savings(tx):
    """Only explicit Capitec Live Better transfers are identified; other transfers stay neutral."""
    desc = tx.description.lower()
    if "live better round-up transfer" in desc or "live better interest sweep" in desc:
        return "into_savings" if tx.principal < 0 else "review"
    if "from live better savings account" in desc:
        return "from_savings" if tx.principal > 0 else "review"
    return "not_savings"


def build_transaction(raw, metadata):
    issues = []
    date = datetime.strptime(raw["date"], "%d/%m/%Y").date()
    if not metadata["start_date"] <= date.isoformat() <= metadata["end_date"]:
        issues.append("outside_verified_statement_period")
    vals = {}
    for field in ("money_in", "money_out", "fee", "balance"):
        raw_txt = raw[field]
        value = money(raw_txt)
        if raw_txt and value is None:
            issues.append("invalid_" + field)
        vals[field] = value
    if vals["balance"] is None:
        issues.append("missing_balance")
    if vals["money_in"] is None and vals["money_out"] is None and vals["fee"] is None:
        issues.append("missing_financial_movement")
    principal = (vals["money_in"] or Decimal(0)) + (vals["money_out"] or Decimal(0))
    fee = vals["fee"] or Decimal(0)
    if (vals["money_in"] or Decimal(0)) < 0 or (vals["money_out"] or Decimal(0)) > 0:
        issues.append("unexpected_money_column_sign")
    flow = "fee_only" if principal == 0 and fee != 0 else "income" if principal > 0 else "expense" if principal < 0 else "zero_value"
    # Fee-only transactions need a signed reporting amount so they are not lost in spending;
    # other transactions retain principal and fee separately to prevent double counting.
    report = principal if principal else fee
    t = Transaction(page=raw["page"], y0=round(raw["y0"], 2), y1=round(raw["y1"], 2),
                    raw_date=raw["date"], date=date.isoformat(), description=raw["description"],
                    bank_category=raw["category"], raw_money_in=raw["money_in"],
                    raw_money_out=raw["money_out"], raw_fee=raw["fee"], raw_balance=raw["balance"],
                    money_in=number(vals["money_in"] or Decimal(0)), money_out=number(vals["money_out"] or Decimal(0)),
                    fee=number(fee), balance=number(vals["balance"]), principal=number(principal),
                    net_movement=number(principal+fee), reporting_amount=number(report),
                    flow_type=flow, issues=issues)
    t.savings_direction = recognise_savings(t)
    if not t.description:
        t.issues.append("missing_description")
    if not t.bank_category:
        t.issues.append("missing_category")
    t.confidence = "review" if t.issues else "high"
    return t


def extract_transactions(pdf, metadata):
    transactions, skipped, bounds_by_page, counts = [], [], {}, defaultdict(int)
    for page_idx, page in enumerate(pdf):
        page_number = page_idx+1
        bounds = get_table_region(page, page_number)
        if bounds is None:
            skipped.append({"page": page_number, "reason": "not_transaction_history", "rows": 0})
            continue
        bounds_by_page[page_number] = bounds
        lines = group_lines(page, bounds)
        raw = None
        for line in lines:
            cells = line["cells"]
            current_date = cells["date"]
            if DATE_PATTERN.fullmatch(current_date):
                if raw is not None:
                    if raw["balance"] and any(raw[k] for k in ("money_in", "money_out", "fee")):
                        t = build_transaction(raw, metadata); transactions.append(t); counts[t.page]+=1
                    else:
                        skipped.append({"page": raw["page"], "date": raw["date"], "y0": raw["y0"],
                                        "description": raw["description"], "reason": "non_posted_or_incomplete_row",
                                        "raw_columns": {k: raw[k] for k in FIELDS}})
                raw = {**cells, "page": page_number, "y0": line["y0"], "y1": line["y1"]}
                continue
            # Text below transaction line may continue description or category. No free-standing transaction.
            if raw is not None and not current_date:
                if any(cells[x] for x in ("money_in", "money_out", "fee", "balance")):
                    # Rare continuation of financial numbers. Avoid losing values, mark for review when conflicting.
                    for x in ("money_in", "money_out", "fee", "balance"):
                        if cells[x] and not raw[x]: raw[x] = cells[x]
                for x in ("description", "category"):
                    if cells[x]: raw[x] = squish(raw[x] + " " + cells[x])
                raw["y1"] = line["y1"]
        if raw is not None:
            if raw["balance"] and any(raw[k] for k in ("money_in", "money_out", "fee")):
                t = build_transaction(raw, metadata); transactions.append(t); counts[t.page]+=1
            else:
                skipped.append({"page": raw["page"], "date": raw["date"], "y0": raw["y0"],
                                "description": raw["description"], "reason": "non_posted_or_incomplete_row",
                                "raw_columns": {k: raw[k] for k in FIELDS}})
    return transactions, skipped, bounds_by_page, counts


def validate(transactions, metadata):
    opening = metadata["opening_balance"]
    previous = opening
    anomalies = []
    category_counts = Counter()
    monthly_counts = Counter()
    for i, t in enumerate(transactions):
        category_counts[t.bank_category] += 1
        monthly_counts[t.date[:7]] += 1
        if t.balance is not None:
            expected = previous + Decimal(str(t.net_movement))
            diff = Decimal(str(t.balance)) - expected
            t.balance_difference = number(diff)
            if abs(diff) > Decimal("0.02"):
                t.issues.append("balance_mismatch")
                anomalies.append({"index": i+1, "page": t.page, "date": t.date,
                                  "difference": number(diff), "previous_balance": number(previous),
                                  "reported_balance": t.balance, "expected_balance": number(expected)})
            previous = Decimal(str(t.balance))
        t.confidence = "review" if t.issues else "high"
    gross_in = sum((Decimal(str(t.money_in)) for t in transactions), Decimal(0))
    gross_out = sum((Decimal(str(t.money_out)) for t in transactions), Decimal(0))
    fees = sum((Decimal(str(t.fee)) for t in transactions), Decimal(0))
    fee_refunds = sum((Decimal(str(t.fee)) for t in transactions if t.fee > 0), Decimal(0))
    charged_fees = sum((Decimal(str(t.fee)) for t in transactions if t.fee < 0), Decimal(0))
    # The bank puts positive fee corrections in gross Money In and negative fees
    # in gross Money Out; the separate Total Fees summary nets both together.
    gross_cash_in = gross_in + fee_refunds
    gross_cash_out = gross_out + charged_fees
    closing = metadata["closing_balance"]
    metrics = {"transactions": len(transactions), "first_date": transactions[0].date if transactions else None,
               "last_date": transactions[-1].date if transactions else None,
               "money_in_extracted": number(gross_cash_in), "money_out_extracted": number(gross_cash_out),
               "principal_in": number(gross_in), "principal_out": number(gross_out),
               "fee_refunds_in": number(fee_refunds), "fee_charges_out": number(charged_fees),
               "fees_extracted": number(fees), "net_movement_extracted": number(gross_in+gross_out+fees),
               "opening_balance": number(opening), "closing_balance": number(closing),
               "balance_change_expected": number(closing-opening),
               "closing_balance_difference": number(previous-closing),
               "money_in_summary_difference": number(gross_cash_in - metadata["money_in_summary"]) if "money_in_summary" in metadata else None,
               "money_out_summary_difference": number(gross_cash_out-metadata["money_out_summary"]) if "money_out_summary" in metadata else None,
               "fees_summary_difference": number(fees-metadata["fee_summary"]) if "fee_summary" in metadata else None,
               "balance_anomaly_count": len(anomalies),
               "needs_review_count": sum(t.confidence != "high" for t in transactions),
               "years": sorted({t.date[:4] for t in transactions}),
               "transactions_by_month": dict(sorted(monthly_counts.items())),
               "transactions_by_category": dict(category_counts.most_common()),
               "savings_directions": dict(Counter(t.savings_direction for t in transactions)),
               "fee_only_rows": sum(t.flow_type == "fee_only" for t in transactions)}
    return metrics, anomalies


def create_debug_pdf(pdf, transactions, skipped, regions, outfile):
    grouped = defaultdict(list)
    for t in transactions: grouped[t.page].append(t)
    bad = defaultdict(list)
    for s in skipped:
        if s.get("y0") is not None: bad[s["page"]].append(s)
    # Debug legend: CYAN table boundary; GREEN reconciled; ORANGE review; RED skipped; VIOLET period.
    first = pdf[0]
    for phrase in ("From Date:", "To Date:"):
        for rect in first.search_for(phrase):
            first.draw_rect(pymupdf.Rect(17, rect.y0-1, 210, rect.y1+2),
                            color=(0.62,0.25,0.8), width=1)
    for i, page in enumerate(pdf, start=1):
        info = regions.get(i)
        if info is None:
            continue
        page.draw_rect(pymupdf.Rect(22, info["start_y"], 425, info["end_y"]), color=(0,0.6,0.8), width=.9)
        for tx in grouped.get(i, []):
            hue = (0.07,0.58,0.25) if tx.confidence == "high" else (0.96,0.53,0.08)
            r = pymupdf.Rect(24.2, tx.y0-.5, 421.0, tx.y1+.5)
            ann = page.add_rect_annot(r)
            ann.set_colors(stroke=hue)
            ann.set_border(width=.7)
            ann.set_info(title="Capitec parsed transaction", content=(
                f"{tx.date} | {tx.bank_category} | movement {rand(tx.net_movement)}\n"
                f"Principal: {rand(tx.principal)}; fee: {rand(tx.fee)}\n"
                f"Confidence: {tx.confidence}; Issues: {', '.join(tx.issues) or 'none'}"))
            ann.update()
        for item in bad.get(i, []):
            r = pymupdf.Rect(24.0, item["y0"]-.3, 421.0, item["y0"]+10)
            ann = page.add_rect_annot(r)
            ann.set_colors(stroke=(0.9,0.15,0.13))
            ann.set_border(width=1)
            ann.set_info(title="Ignored/unposted row", content=item.get("reason", "skipped"))
            ann.update()
    pdf.save(str(outfile), garbage=4, deflate=True)

