from collections import defaultdict

import pymupdf

from walletwise.utils.pdf_utils import group_words_into_lines
from walletwise.utils.text_utils import word_center_x, word_center_y


class FNBPDFExtractor:
    """Extract FNB-specific visual rows. This layer does not create common transactions yet."""

    def __init__(self, config, row_parser, table_detector):
        self.config = config
        self.row_parser = row_parser
        self.table_detector = table_detector

    def extract_rows(self, pdf_file: str):
        pdf = pymupdf.open(pdf_file)
        rows = []
        page_counts = defaultdict(int)
        c = self.config

        try:
            for page_index, page in enumerate(pdf):
                page_number = page_index + 1
                words = page.get_text("words", sort=False)

                table_start_y, table_end_y, has_closing_balance = (
                    self.table_detector.get_page_table_bounds(words, page, page_number)
                )

                print(
                    f"Page {page_number}: table Y={table_start_y:.2f} to {table_end_y:.2f}"
                    + (" [Closing Balance found]" if has_closing_balance else "")
                )

                usable_words = [
                    word for word in words
                    if c.table_x0 <= word_center_x(word) <= c.table_x1
                    and table_start_y <= word_center_y(word) <= table_end_y
                ]

                for line in group_words_into_lines(usable_words, c.row_y_tolerance):
                    row = self.row_parser.line_to_row(line, page_number)

                    if not any((row.date, row.description, row.amount, row.balance, row.charges)):
                        continue
                    if self.table_detector.is_noise(row):
                        continue

                    rows.append(row)
                    page_counts[page_number] += 1

                if has_closing_balance:
                    break
        finally:
            pdf.close()

        return rows, dict(page_counts)
