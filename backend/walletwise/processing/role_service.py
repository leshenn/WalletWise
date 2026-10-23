import re

from walletwise.models import Transaction


class FinancialRoleService:
    """Classify what a transaction means financially.

    This is separate from merchant/category classification. A debit is not
    automatically "spending" forever: it can be a transfer to savings. A
    credit is not automatically "income": it can be a refund or money moved
    back from another account.

    The service deliberately prefers conservative rules. Explicit transfer,
    refund and salary wording wins first. Only then do we fall back to the sign
    of the amount.
    """

    TRANSFER_PATTERNS = (
        r"\btransfer\b",
        r"\bown\s+account\b",
        r"\bbetween\s+accounts\b",
        r"\bsavings?\b",
        r"\binvestment\b",
        r"\bnotice\s+deposit\b",
        r"\bmoney\s+market\b",
    )

    REFUND_PATTERNS = (
        r"\brefund\b",
        r"\breimbursement\b",
        r"\breversal\b",
        r"\breversed\b",
        r"\bchargeback\b",
        r"\bcashback\b",
    )

    INCOME_PATTERNS = (
        r"\bsalary\b",
        r"\bwages?\b",
        r"\bpayroll\b",
        r"\bremuneration\b",
        r"\bpension\b",
        r"\binterest\s+(?:paid|credit)\b",
    )

    EXPENSE_PATTERNS = (
        r"\bpos\s+purchase\b",
        r"\bcard\s+purchase\b",
        r"\bdebit\s+order\b",
        r"\bfee\b",
        r"\bairtime\b",
        r"\bdata\b",
        r"\buber\b",
        r"\bbolt\b",
        r"\bmr\s*d\b",
        r"\btakealot\b",
        r"\bamazon\b",
    )

    @staticmethod
    def _matches(patterns: tuple[str, ...], text: str) -> str | None:
        for pattern in patterns:
            if re.search(pattern, text, flags=re.IGNORECASE):
                return pattern
        return None

    def classify_transaction(self, transaction: Transaction) -> Transaction:
        # Some bank adapters have explicit financial roles from statement fields;
        # never replace a verified transfer or posted bank fee with sign heuristics.
        if transaction.role_confidence == "high" and transaction.financial_role in {"transfer", "expense", "refund"}:
            return transaction

        # Closing balance is not a real financial movement.
        if transaction.transaction_type == "closing_balance":
            transaction.financial_role = "balance"
            transaction.role_confidence = "high"
            transaction.role_reason = "Closing Balance is not income, spending, or a transfer."
            if transaction.category == "Uncategorised":
                transaction.category = "Balance"
                transaction.category_confidence = "high"
                transaction.category_reason = "Closing Balance row."
            return transaction

        searchable = " ".join(
            part for part in (transaction.raw_description, transaction.display_name) if part
        )

        # A savings-account bank charge isn't money saved. Likewise credited
        # interest is income, not a withdrawal from savings. These must be
        # resolved before the broad "savings" transfer wording check.
        if re.search(r'\bsavings?\b', searchable, flags=re.IGNORECASE):
            if re.search(r'\b(?:fees?|service\s+charges?|monthly\s+charges?)\b', searchable, flags=re.IGNORECASE) and transaction.amount is not None and transaction.amount < 0:
                transaction.financial_role = 'expense'
                transaction.role_confidence = 'high'
                transaction.role_reason = 'Savings account fee is an expense, not a savings transfer.'
                if transaction.category == 'Uncategorised':
                    transaction.category = 'Bank Fees'
                return transaction
            if re.search(r'\binterest\b', searchable, flags=re.IGNORECASE) and transaction.amount is not None and transaction.amount > 0:
                transaction.financial_role = 'income'
                transaction.role_confidence = 'medium'
                transaction.role_reason = 'Credited savings interest is income, not a transfer out of savings.'
                return transaction

        matched = self._matches(self.TRANSFER_PATTERNS, searchable)
        if matched:
            transaction.financial_role = "transfer"
            transaction.role_confidence = "medium"
            transaction.role_reason = (
                "Looks like money moving between accounts. Confirm that it is a transfer."
            )
            if transaction.category == "Uncategorised":
                transaction.category = "Transfer"
                transaction.category_confidence = "medium"
                transaction.category_reason = "Category suggested from transfer wording."
            return transaction

        matched = self._matches(self.REFUND_PATTERNS, searchable)
        if matched:
            transaction.financial_role = "refund"
            transaction.role_confidence = "high"
            transaction.role_reason = "Matched refund/reimbursement wording."
            if transaction.category == "Uncategorised":
                transaction.category = "Refund / Reimbursement"
                transaction.category_confidence = "high"
                transaction.category_reason = "Category follows the refund role."
            return transaction

        matched = self._matches(self.INCOME_PATTERNS, searchable)
        if matched:
            transaction.financial_role = "income"
            transaction.role_confidence = "high"
            transaction.role_reason = "Matched clear income wording such as salary/payroll."
            if transaction.category == "Uncategorised":
                salary_words = re.search(
                    r"\b(?:salary|wages?|payroll|remuneration)\b",
                    searchable,
                    flags=re.IGNORECASE,
                )
                transaction.category = "Salary" if salary_words else "Income"
                transaction.category_confidence = "high"
                transaction.category_reason = (
                    "Matched salary/payroll wording."
                    if salary_words
                    else "Category follows the income role."
                )
            return transaction

        matched = self._matches(self.EXPENSE_PATTERNS, searchable)
        if matched:
            transaction.financial_role = "expense"
            transaction.role_confidence = "high"
            transaction.role_reason = "Matched clear purchase/debit wording."
            return transaction

        # A negative movement is usually spending. We keep this high for MVP
        # efficiency, but explicit transfer wording above always wins.
        if transaction.amount is not None and transaction.amount < 0:
            transaction.financial_role = "expense"
            transaction.role_confidence = "high"
            transaction.role_reason = (
                "Money moved out and no transfer/refund wording was detected."
            )
            return transaction

        # Positive movements are more ambiguous: salary, money from another
        # account, reimbursements, gifts, etc. Put them in review unless a
        # stronger rule above recognised them.
        if transaction.amount is not None and transaction.amount > 0:
            transaction.financial_role = "income"
            transaction.role_confidence = "medium"
            transaction.role_reason = (
                "Money moved in, but WalletWise cannot yet tell whether it is income, a transfer, or a refund."
            )
            return transaction

        transaction.financial_role = "unknown"
        transaction.role_confidence = "unknown"
        transaction.role_reason = "WalletWise could not determine the financial role."
        return transaction

    def classify(self, transactions: list[Transaction]):
        for transaction in transactions:
            self.classify_transaction(transaction)
        return transactions
