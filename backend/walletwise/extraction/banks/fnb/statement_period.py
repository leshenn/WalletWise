"""FNB statement-period detection and optional local-only debugging.

Do not guess from arbitrary year references such as invoice, statement date or footer.
"""
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
import json
import re

import pymupdf

from walletwise.utils.date_utils import MONTHS
from walletwise.utils.text_utils import clean_text

PERIOD_SCAN_PAGES = 3
PERIOD_PATTERN = re.compile(
    r"\bStatement\s+Period\s*[:\-]?\s*"
    r"(?P<start_day>\d{1,2})\s+(?P<start_month>[A-Za-z]{3,9})\s+(?P<start_year>\d{4})"
    r"\s+(?:to|through|–|—|-)\s+"
    r"(?P<end_day>\d{1,2})\s+(?P<end_month>[A-Za-z]{3,9})\s+(?P<end_year>\d{4})\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class StatementPeriod:
    start: datetime
    end: datetime
    page: int
    source: str
    matched_text: str

    def as_dict(self) -> dict:
        return {
            "start": self.start.date().isoformat(),
            "end": self.end.date().isoformat(),
            "start_year": self.start.year,
            "end_year": self.end.year,
            "page": self.page,
            "source": self.source,
            "matched_header": self.matched_text,
            "verified": True,
        }


def parse_statement_period_text(text: str, page_number: int = 1, source: str = "PDF selectable text") -> StatementPeriod | None:
    match = PERIOD_PATTERN.search(clean_text(text))
    if not match:
        return None
    parts = match.groupdict()
    start_month = MONTHS.get(parts["start_month"].lower())
    end_month = MONTHS.get(parts["end_month"].lower())
    if not start_month or not end_month:
        raise ValueError("Unrecognised month in FNB Statement Period: " + match.group(0))
    try:
        start = datetime(int(parts["start_year"]), start_month, int(parts["start_day"]))
        end = datetime(int(parts["end_year"]), end_month, int(parts["end_day"]))
    except ValueError as exc:
        raise ValueError("Invalid FNB Statement Period: " + match.group(0)) from exc
    if end < start:
        raise ValueError("FNB Statement Period end precedes start: " + match.group(0))
    return StatementPeriod(start, end, page_number, source, match.group(0))


def detect_statement_period(pdf, *, ocr_enabled: bool = True, tesseract_cmd: str | None = None) -> StatementPeriod | None:
    """Read PDF selectable header first; only OCR the header if necessary."""
    found = []
    for page_index in range(min(len(pdf), PERIOD_SCAN_PAGES)):
        page = pdf[page_index]
        for sort in (True, False):
            candidate = parse_statement_period_text(page.get_text("text", sort=sort), page_index + 1)
            if candidate:
                found.append(candidate)
                break
    if found:
        if len({(item.start, item.end) for item in found}) != 1:
            raise ValueError("Conflicting FNB Statement Period headers found in the PDF.")
        return found[0]

    if ocr_enabled:
        try:
            import pytesseract
            from PIL import Image
            if tesseract_cmd and Path(tesseract_cmd).is_file():
                pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
            candidates = []
            for page_index in range(min(len(pdf), PERIOD_SCAN_PAGES)):
                page = pdf[page_index]
                region = pymupdf.Rect(0, 0, page.rect.width, page.rect.height * .48)
                pix = page.get_pixmap(dpi=220, clip=region, alpha=False)
                img = Image.open(BytesIO(pix.tobytes("png")))
                value = pytesseract.image_to_string(img, config="--psm 6")
                candidate = parse_statement_period_text(value, page_index + 1, "Header OCR")
                if candidate:
                    candidates.append(candidate)
            if candidates:
                if len({(item.start, item.end) for item in candidates}) != 1:
                    raise ValueError("Conflicting OCR FNB Statement Period headers found in PDF.")
                return candidates[0]
        except (ImportError, RuntimeError, OSError) as exc:
            print(f"[PERIOD] Header OCR unavailable: {type(exc).__name__}")
        except Exception as exc:
            # OCR engine may not be installed/configured; never guess the year.
            if isinstance(exc, ValueError):
                raise
            print(f"[PERIOD] Header OCR could not read PDF: {type(exc).__name__}")
    return None


def write_period_debug_pdf(pdf_file: str, period: StatementPeriod, output_file: str) -> None:
    with pymupdf.open(pdf_file) as pdf:
        page = pdf[period.page - 1]
        matches = page.search_for(period.matched_text) or page.search_for("Statement Period")
        if matches:
            anchor = matches[0]
            rect = pymupdf.Rect(
                max(0, anchor.x0 - 5), max(0, anchor.y0 - 3),
                min(page.rect.width, anchor.x1 + (5 if page.search_for(period.matched_text) else 410)),
                min(page.rect.height, anchor.y1 + 3),
            )
        else:
            rect = pymupdf.Rect(0, 0, page.rect.width, page.rect.height * .48)
        annotation = page.add_rect_annot(rect)
        annotation.set_colors(stroke=(.62, .20, .84))
        annotation.set_border(width=2)
        annotation.set_info(
            title="WalletWise FNB statement-period detection",
            content=f"{period.start:%Y-%m-%d} to {period.end:%Y-%m-%d}\nSource: {period.source}\nHeader: {period.matched_text}",
        )
        annotation.update()
        pdf.save(output_file)


def write_period_debug_json(period: StatementPeriod, drafts, orphans, output_file: str) -> None:
    payload = {
        "statement_period": period.as_dict(),
        "accepted_dates": [
            {"raw_date": item.raw_date, "date": item.date.date().isoformat(), "page": item.page}
            for item in drafts if item.record_type != "closing_balance"
        ],
        "rejected_dates": [
            {"raw_date": row.date, "page": row.page, "reason": reason}
            for row, reason in orphans if row.date
        ],
        "accepted_count": sum(item.record_type != "closing_balance" for item in drafts),
        "rejected_count": len(orphans),
    }
    Path(output_file).write_text(json.dumps(payload, indent=2), encoding="utf-8")
