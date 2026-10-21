from walletwise.extraction.banks.fnb.config import FNBConfig
from walletwise.extraction.banks.fnb.models import FNBRawRow
from walletwise.utils.money_utils import clean_money_parts
from walletwise.utils.text_utils import clean_description, clean_text, word_center_x


class FNBRowParser:
    """Map PyMuPDF words into the fixed FNB statement columns."""

    def __init__(self, config: FNBConfig):
        self.config = config

    def get_column(self, word):
        x = word_center_x(word)
        c = self.config

        if c.date_x0 <= x < c.date_x1:
            return "date"
        if c.description_x0 <= x < c.description_x1:
            return "description"
        if c.amount_x0 <= x < c.amount_x1:
            return "amount"
        if c.balance_x0 <= x < c.balance_x1:
            return "balance"
        if c.charges_x0 <= x <= c.charges_x1:
            return "charges"
        return None

    def line_to_row(self, line, page_number: int) -> FNBRawRow:
        cells = {
            "date": [],
            "description": [],
            "amount": [],
            "balance": [],
            "charges": [],
        }

        previous_column = None
        previous_x1 = None

        for word in sorted(line, key=lambda item: item[0]):
            text = clean_text(word[4])
            if not text:
                continue

            column = self.get_column(word)
            if column is None:
                continue

            x0 = word[0]
            x1 = word[2]

            # FNB occasionally splits Cr / Dr into tiny PDF text fragments.
            if (
                text.lower() in {"cr", "dr", "c", "r"}
                and previous_column in {"amount", "balance"}
                and previous_x1 is not None
                and x0 - previous_x1 <= 10
            ):
                column = previous_column

            cells[column].append(text)
            previous_column = column
            previous_x1 = x1

        return FNBRawRow(
            date=clean_text(" ".join(cells["date"])),
            description=clean_description(" ".join(cells["description"])),
            amount=clean_money_parts(cells["amount"]),
            balance=clean_money_parts(cells["balance"]),
            charges=clean_money_parts(cells["charges"]),
            page=page_number,
            y0=min(word[1] for word in line),
            y1=max(word[3] for word in line),
            ocr_used=False,
        )
