from walletwise.models import Transaction


class TransactionValidator:
    """Validate the common WalletWise model, not a bank-specific statement format."""

    @staticmethod
    def _add_reason(transaction: Transaction, reason: str):
        if reason not in transaction.review_reasons:
            transaction.review_reasons.append(reason)

    def validate(self, transactions: list[Transaction]):
        previous_balance = None

        for transaction in transactions:
            if transaction.transaction_type == "closing_balance":
                if transaction.balance is None:
                    self._add_reason(transaction, "Closing Balance has no balance value")
                if transaction.ocr_used:
                    self._add_reason(transaction, "Description came from OCR; verify it")
                continue

            if not transaction.raw_description:
                self._add_reason(transaction, "Description is blank")
            if transaction.amount is None:
                self._add_reason(transaction, "Amount is missing or could not be normalised")
            if transaction.balance is None:
                self._add_reason(transaction, "Balance is missing or could not be normalised")
            if transaction.ocr_used:
                self._add_reason(transaction, "Description came from OCR; verify it")

            if (
                previous_balance is not None
                and transaction.balance is not None
                and transaction.amount is not None
            ):
                expected = previous_balance + transaction.amount
                difference = round(transaction.balance - expected, 2)

                if abs(difference) > 0.02:
                    self._add_reason(
                        transaction,
                        f"Balance does not reconcile ({difference:+.2f}); verify this row",
                    )

            if transaction.balance is not None:
                previous_balance = transaction.balance

        return transactions
