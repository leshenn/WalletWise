import re

from walletwise.models import Transaction


class CategoryService:
    """Bank-independent fallback categorisation hints.

    Explicit database category rules run before these heuristics. These broad
    hints are intentionally MEDIUM confidence so the UI can separate them from
    uncategorised transactions and ask the user to verify them.
    """

    HEURISTICS = (
        (r"\b(?:restaurant|cafe|coffee|takeaway|grill|pizza|burger|chicken)\b", "Eating Out"),
        (r"\b(?:supermarket|grocery|market)\b", "Groceries"),
        (r"\b(?:petrol|fuel|service station|garage)\b", "Fuel"),
        (r"\b(?:pharmacy|chemist|medical|clinic)\b", "Health & Personal Care"),
        (r"\b(?:insurance|electricity|municipality|water|rates)\b", "Bills"),
    )

    def classify_transaction(self, transaction: Transaction) -> Transaction:
        # If naming already produced a useful category, keep it and attach a
        # confidence if one has not been set yet.
        if transaction.category and transaction.category != "Uncategorised":
            if transaction.category_confidence in {"", "unknown", None}:
                transaction.category_confidence = (
                    "high" if transaction.naming_confidence == "high" else "medium"
                )
                transaction.category_reason = (
                    transaction.category_reason
                    or "Category came from the recognised merchant/transaction rule."
                )
            return transaction

        searchable = " ".join(
            part for part in (transaction.raw_description, transaction.display_name) if part
        )

        for pattern, category in self.HEURISTICS:
            if re.search(pattern, searchable, flags=re.IGNORECASE):
                transaction.category = category
                transaction.category_confidence = "medium"
                transaction.category_reason = (
                    f"Suggested from a broad keyword pattern for {category}; please verify."
                )
                return transaction

        transaction.category = "Uncategorised"
        transaction.category_confidence = "unknown"
        transaction.category_reason = (
            "WalletWise does not yet know how to categorise this transaction."
        )
        return transaction

    def classify(self, transactions: list[Transaction]):
        for transaction in transactions:
            self.classify_transaction(transaction)
        return transactions
