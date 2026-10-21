import re
from collections import Counter
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
    years = []
    for page in list(pdf)[:2]:
        years.extend(int(year) for year in re.findall(r"\b20\d{2}\b", page.get_text()))

    if not years:
        return datetime.now().year

    return Counter(years).most_common(1)[0][0]


class DateResolver:
    def __init__(self, start_year: int, order: str = "ascending"):
        self.current_year = start_year
        self.last_month = None
        self.order = order

    def resolve(self, text):
        parsed = parse_day_month(text)
        if parsed is None:
            return None

        day, month, explicit_year = parsed

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
            return None

        self.last_month = month
        return result
