import re

from walletwise.extraction.banks.fnb.config import FNBConfig
from walletwise.extraction.banks.fnb.models import FNBRawRow
from walletwise.utils.date_utils import parse_day_month
from walletwise.utils.money_utils import looks_like_money
from walletwise.utils.pdf_utils import group_words_into_lines
from walletwise.utils.text_utils import clean_text, line_text, word_center_x, word_center_y


class FNBTableDetector:
    """Detect the FNB transaction table start/end and filter statement noise."""

    NOISE_PHRASES = (
        "customer vat registration",
        "branch number",
        "account number",
        "transactions in rand",
        "first national bank",
        "company registration",
        "www.fnb",
        "terms and conditions",
    )

    def __init__(self, config: FNBConfig, row_parser):
        self.config = config
        self.row_parser = row_parser

    def is_noise(self, row: FNBRawRow) -> bool:
        combined = clean_text(
            f"{row.date} {row.description} {row.amount} {row.balance} {row.charges}"
        ).lower()
        return any(phrase in combined for phrase in self.NOISE_PHRASES)

    @staticmethod
    def is_closing_balance_row(row: FNBRawRow, raw_line_text: str = "") -> bool:
        text = clean_text(f"{raw_line_text} {row.description}").lower()
        return "closing balance" in text

    @staticmethod
    def is_opening_balance_row(row: FNBRawRow, raw_line_text: str = "") -> bool:
        text = clean_text(f"{raw_line_text} {row.description}").lower()
        return "opening balance" in text

    @staticmethod
    def row_has_money(row: FNBRawRow) -> bool:
        return any(looks_like_money(value) for value in (row.amount, row.balance, row.charges))

    def find_table_header_bottom(self, words):
        for word in words:
            if clean_text(word[4]).lower() != "description":
                continue

            y = word_center_y(word)
            nearby = [other for other in words if abs(word_center_y(other) - y) <= 7]
            nearby_text = {clean_text(other[4]).lower() for other in nearby}

            if {"date", "description", "amount", "balance"}.issubset(nearby_text):
                return max(other[3] for other in nearby) + 2

        return None

    def line_looks_like_table_data(self, line, page_number: int = 0) -> bool:
        row = self.row_parser.line_to_row(line, page_number)
        text = line_text(line)

        if self.is_noise(row):
            return False
        if parse_day_month(row.date) is not None:
            return True
        if self.is_closing_balance_row(row, text) or self.is_opening_balance_row(row, text):
            return True
        if self.row_has_money(row) and re.search(r"[A-Za-z]", row.description):
            return True
        return False

    def find_first_table_row_top(self, words, page_number: int):
        c = self.config
        table_words = [
            word for word in words
            if c.table_x0 <= word_center_x(word) <= c.table_x1
        ]

        for line in group_words_into_lines(table_words, c.row_y_tolerance):
            if self.line_looks_like_table_data(line, page_number):
                return max(0, min(word[1] for word in line) - 2)
        return 0

    def find_closing_balance_bottom(self, words, page_number: int, start_y: float):
        c = self.config
        table_words = [
            word for word in words
            if c.table_x0 <= word_center_x(word) <= c.table_x1
            and word_center_y(word) >= start_y
        ]

        for line in group_words_into_lines(table_words, c.row_y_tolerance):
            row = self.row_parser.line_to_row(line, page_number)
            raw_text = line_text(line)

            if not self.is_closing_balance_row(row, raw_text):
                continue

            # Require money on the actual table row so a page-1 summary label does not stop parsing.
            if self.row_has_money(row):
                return max(word[3] for word in line) + 2

        return None

    def find_last_table_row_bottom(
        self,
        words,
        page_number: int,
        start_y: float,
        page_height: float,
    ):
        c = self.config
        table_words = [
            word for word in words
            if c.table_x0 <= word_center_x(word) <= c.table_x1
            and word_center_y(word) >= start_y
        ]

        lines = group_words_into_lines(table_words, c.row_y_tolerance)
        last_bottom = None
        previous_bottom = None

        for line in lines:
            row = self.row_parser.line_to_row(line, page_number)
            y0 = min(word[1] for word in line)
            y1 = max(word[3] for word in line)

            if self.is_noise(row):
                continue

            if self.line_looks_like_table_data(line, page_number):
                last_bottom = y1
                previous_bottom = y1
                continue

            if (
                row.description
                and previous_bottom is not None
                and 0 <= y0 - previous_bottom <= c.description_continuation_max_gap
            ):
                last_bottom = y1
                previous_bottom = y1

        if last_bottom is None:
            return page_height
        return min(page_height, last_bottom + 2)

    def get_page_table_bounds(self, words, page, page_number: int):
        header_bottom = self.find_table_header_bottom(words)

        if header_bottom is not None:
            start_y = header_bottom
        else:
            start_y = self.find_first_table_row_top(words, page_number)

        closing_bottom = self.find_closing_balance_bottom(words, page_number, start_y)

        if closing_bottom is not None:
            end_y = closing_bottom
            has_closing_balance = True
        else:
            end_y = self.find_last_table_row_bottom(
                words,
                page_number,
                start_y,
                page.rect.height,
            )
            has_closing_balance = False

        if end_y <= start_y:
            end_y = page.rect.height

        return start_y, end_y, has_closing_balance
