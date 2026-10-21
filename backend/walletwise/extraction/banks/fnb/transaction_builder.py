from walletwise.extraction.banks.fnb.models import FNBRawRow, FNBTransactionDraft
from walletwise.utils.date_utils import DateResolver, parse_day_month
from walletwise.utils.text_utils import clean_description, clean_text


class FNBTransactionBuilder:
    """Turn FNB visual rows into logical FNB drafts before standardisation."""

    def __init__(self, config, table_detector):
        self.config = config
        self.table_detector = table_detector

    @staticmethod
    def _make_draft(
        row: FNBRawRow,
        date,
        raw_date: str,
        record_type: str = "transaction",
    ) -> FNBTransactionDraft:
        return FNBTransactionDraft(
            date=date,
            raw_date=raw_date,
            description=row.description,
            amount=row.amount,
            balance=row.balance,
            charges=row.charges,
            page=row.page,
            y0=row.y0,
            y1=row.y1,
            ocr_used=row.ocr_used,
            record_type=record_type,
        )

    @staticmethod
    def _vertical_gap(transaction: FNBTransactionDraft, row: FNBRawRow) -> float:
        if transaction.page == row.page:
            return row.y0 - transaction.y1
        if row.page == transaction.page + 1 and row.y0 < 40:
            return 0
        return float("inf")

    @staticmethod
    def _merge_into_transaction(transaction: FNBTransactionDraft, row: FNBRawRow):
        if row.description:
            transaction.description = clean_description(
                f"{transaction.description} {row.description}"
            )

        for field in ("amount", "balance", "charges"):
            if not getattr(transaction, field) and getattr(row, field):
                setattr(transaction, field, getattr(row, field))

        transaction.y1 = max(transaction.y1, row.y1)
        transaction.ocr_used = transaction.ocr_used or row.ocr_used

    def build(self, rows: list[FNBRawRow], start_year: int):
        resolver = DateResolver(start_year, self.config.date_order)
        transactions = []
        orphan_rows = []

        current_transaction = None
        last_resolved_date = None

        for row in rows:
            raw = clean_text(
                f"{row.date} {row.description} {row.amount} {row.balance} {row.charges}"
            )

            if "closing balance" in raw.lower():
                if last_resolved_date is not None:
                    closing = self._make_draft(
                        row,
                        last_resolved_date,
                        row.date,
                        record_type="closing_balance",
                    )
                    closing.description = "Closing Balance"
                    transactions.append(closing)
                    current_transaction = closing
                else:
                    orphan_rows.append(
                        (row, "Closing Balance found before any valid transaction date")
                    )
                continue

            has_explicit_date = parse_day_month(row.date) is not None
            has_money = self.table_detector.row_has_money(row)

            if has_explicit_date:
                resolved_date = resolver.resolve(row.date)
                if resolved_date is None:
                    orphan_rows.append((row, "Invalid transaction date"))
                    continue

                transaction = self._make_draft(row, resolved_date, row.date)
                transactions.append(transaction)
                current_transaction = transaction
                last_resolved_date = resolved_date
                continue

            if has_money:
                if current_transaction is not None:
                    gap = self._vertical_gap(current_transaction, row)
                    current_has_no_money = not any((
                        current_transaction.amount,
                        current_transaction.balance,
                        current_transaction.charges,
                    ))

                    if 0 <= gap <= self.config.split_row_max_gap and current_has_no_money:
                        self._merge_into_transaction(current_transaction, row)
                        continue

                # FNB may omit a repeated date for another transaction on the same day.
                if last_resolved_date is not None:
                    transaction = self._make_draft(row, last_resolved_date, "")
                    transaction.review_reasons.append(
                        "Date carried forward because this row had no explicit date"
                    )
                    transactions.append(transaction)
                    current_transaction = transaction
                    continue

                orphan_rows.append(
                    (row, "Financial row appeared before any valid transaction date")
                )
                continue

            # Description-only continuation line.
            if row.description and current_transaction is not None:
                gap = self._vertical_gap(current_transaction, row)
                if 0 <= gap <= self.config.description_continuation_max_gap:
                    self._merge_into_transaction(current_transaction, row)

        return transactions, orphan_rows
