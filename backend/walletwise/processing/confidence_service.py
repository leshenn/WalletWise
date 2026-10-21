from walletwise.models import Transaction


class ConfidenceService:
    """Set extraction confidence independently from naming confidence.

    Row colour answers: "Did WalletWise extract this transaction reliably?"
    Name-cell colour answers: "Is WalletWise sure about the friendly name?"
    """

    @staticmethod
    def level(transaction: Transaction) -> str:
        if transaction.transaction_type == "closing_balance":
            if transaction.balance is None:
                return "low"
            if transaction.ocr_used or transaction.review_reasons:
                return "medium"
            return "high"

        # Red = essential extracted transaction data is missing.
        if (
            not transaction.raw_description
            or transaction.amount is None
            or transaction.balance is None
        ):
            return "low"

        # Orange = usable transaction, but its extraction deserves review.
        if transaction.ocr_used or transaction.review_reasons:
            return "medium"

        return "high"

    def evaluate(self, transactions: list[Transaction]):
        for transaction in transactions:
            transaction.confidence = self.level(transaction)
        return transactions
