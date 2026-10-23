import re
from datetime import datetime

from .text_utils import clean_text


MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


def parse_day_month(text):
    text = clean_text(text)

    match = re.fullmatch(
        r"(\d{1,2})\s+([A-Za-z]{3,9})(?:\s+(\d{4}))?",
        text,
    )
    if match:
        day = int(match.group(1))
        month = MONTHS.get(match.group(2).lower())
        year = int(match.group(3)) if match.group(3) else None
        if month is not None and 1 <= day <= 31:
            return day, month, year

    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", text)
    if match:
        day = int(match.group(1))
        month = int(match.group(2))
        year_text = match.group(3)
        year = None

        if year_text:
            year = int(year_text)
            if year < 100:
                year += 2000

        if 1 <= day <= 31 and 1 <= month <= 12:
            return day, month, year

    return None


def detect_statement_year(pdf) -> int:
    """Legacy helper: use the verified FNB header START year, never arbitrary 20xx."""
    from walletwise.extraction.banks.fnb.statement_period import detect_statement_period
    period = detect_statement_period(pdf)
    if period is None:
        raise ValueError("Statement Period not found; cannot safely detect the year")
    return period.start.year


class DateResolver:
    """Resolve headerless dates against a verified statement period when supplied."""

    def __init__(self, start_year: int, order: str = "ascending", statement_period=None):
        self.current_year = start_year
        self.last_month = None
        self.last_date = None
        self.order = order
        self.period = statement_period
        self.last_error = None
        self.trace = []

    def resolve(self, text):
        self.last_error = None
        parsed = parse_day_month(text)
        if parsed is None:
            self.last_error = f"Invalid transaction date {text!r}"
            return None
        day, month, explicit_year = parsed
        if self.period is not None:
            years = [explicit_year] if explicit_year is not None else range(self.period.start.year, self.period.end.year + 1)
            candidates = []
            for year in years:
                try:
                    candidate = datetime(year, month, day)
                except ValueError:
                    continue
                if self.period.start <= candidate <= self.period.end:
                    candidates.append(candidate)
            if not candidates:
                self.last_error = (f"Date {text!r} has no valid year inside the Statement Period "
                                   f"{self.period.start:%d %b %Y} to {self.period.end:%d %b %Y}")
                return None
            candidates.sort()
            if self.last_date is not None and len(candidates) > 1:
                aligned = [item for item in candidates if (
                    item >= self.last_date if self.order == "ascending" else item <= self.last_date
                )]
                if not aligned:
                    self.last_error = f"Cannot place {text!r} chronologically in the Statement Period"
                    return None
                result = aligned[0] if self.order == "ascending" else aligned[-1]
            else:
                result = candidates[0] if self.order == "ascending" else candidates[-1]
            if self.last_date is not None and (
                (self.order == "ascending" and result < self.last_date) or
                (self.order == "descending" and result > self.last_date)
            ):
                self.last_error = f"Date {text!r} is out of transaction order"
                return None
            self.current_year = result.year
        else:
            # Only used for an EXPLICIT developer-supplied year, never a guessed PDF year.
            if explicit_year is not None:
                self.current_year = explicit_year
            elif self.last_month is not None:
                if self.order == "ascending" and self.last_month >= 10 and month <= 3:
                    self.current_year += 1
                elif self.order == "descending" and self.last_month <= 3 and month >= 10:
                    self.current_year -= 1
            try:
                result = datetime(self.current_year, month, day)
            except ValueError:
                self.last_error = f"Invalid calendar date {text!r}"
                return None
        self.last_month = month
        self.last_date = result
        self.trace.append({"raw_date": text, "resolved_date": result.date().isoformat()})
        return result
