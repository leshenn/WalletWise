import re

from walletwise.models import Transaction
from walletwise.processing.merchant_rules import MERCHANT_RULES


class TransactionNamer:
    """Create a useful, bank-independent display name for each transaction.

    Naming confidence is intentionally separate from extraction confidence.
    A transaction can be extracted perfectly while WalletWise is still unsure
    what merchant/payee the description represents.
    """

    # High-confidence transaction types that are meaningful names by themselves.
    SPECIAL_RULES = (
        (r"\bmonthly\s+account\s+fee\b", "Monthly Account Fee", "Bank Fees"),
        (r"\bservice\s+fees?\b", "Service Fees", "Bank Fees"),
        (r"\bbank\s+charges?\b", "Bank Charges", "Bank Fees"),
        (r"\binterest\s+(?:paid|credit)\b", "Interest Received", "Income"),
        (r"\bsalary\b", "Salary", "Income"),
    )

    # Bank/payment wording that can normally be removed without changing the payee.
    PREFIX_PATTERNS = (
        r"^pos\s+purchase\s+",
        r"^card\s+purchase\s+",
        r"^online\s+purchase\s+",
        r"^online\s+payment\s+",
        r"^eft\s+payment\s+",
        r"^debit\s+order\s+",
        r"^payment\s+to\s+",
        r"^payment\s+from\s+",
        r"^fnb\s+app\s+payment\s+to\s+",
        r"^fnb\s+app\s+payment\s+from\s+",
        r"^cash\s+withdrawal\s+",
    )

    def enrich(self, transactions: list[Transaction]):
        for transaction in transactions:
            self.enrich_transaction(transaction)
        return transactions

    def enrich_transaction(self, transaction: Transaction):
        raw = self._normalise_spaces(transaction.raw_description)

        if transaction.transaction_type == "closing_balance":
            self._set_name(
                transaction,
                name="Closing Balance",
                category="Balance",
                confidence="high",
                source="system",
                reason="WalletWise recognised the statement's Closing Balance row.",
            )
            return

        if not raw:
            self._set_name(
                transaction,
                name="",
                category=transaction.category or "Uncategorised",
                confidence="low",
                source="missing",
                reason="No transaction description was available to name.",
            )
            return

        # 1. Known special transaction types.
        for pattern, friendly_name, category in self.SPECIAL_RULES:
            if re.search(pattern, raw, flags=re.IGNORECASE):
                self._set_name(
                    transaction,
                    name=friendly_name,
                    category=category,
                    confidence="high",
                    source="transaction_rule",
                    reason=f"Matched a known transaction type: {friendly_name}.",
                )
                return

        # 2. Known merchants.  This is high confidence because the merchant rule
        #    matched the original bank description directly.
        merchant_rule = self._find_merchant(raw)
        if merchant_rule is not None:
            self._set_name(
                transaction,
                name=merchant_rule.name,
                category=merchant_rule.category,
                confidence="high",
                source="merchant_rule",
                reason=f"Matched known merchant pattern for {merchant_rule.name}.",
            )
            return

        # 3. Remove obvious transaction wording and references.  This usually
        #    gives a much nicer payee name, but WalletWise should ask the user to
        #    confirm it, so the Name cell is orange.
        cleaned = self._clean_generic_name(raw)

        if cleaned and cleaned.casefold() != raw.casefold():
            self._set_name(
                transaction,
                name=cleaned,
                category=transaction.category or "Uncategorised",
                confidence="medium",
                source="cleaned_description",
                reason="Removed transaction wording/reference data, but the merchant is not in WalletWise's known merchant rules.",
            )
            return

        # 4. We could not confidently interpret it.  Preserve the text instead
        #    of inventing a merchant name.  Low naming confidence is still orange
        #    in Excel; red is reserved for a missing/unusable name.
        self._set_name(
            transaction,
            name=raw,
            category=transaction.category or "Uncategorised",
            confidence="low",
            source="raw_description",
            reason="WalletWise could not confidently simplify this description.",
        )

    @staticmethod
    def _set_name(
        transaction: Transaction,
        *,
        name: str,
        category: str,
        confidence: str,
        source: str,
        reason: str,
    ):
        transaction.display_name = name
        transaction.category = category or "Uncategorised"
        transaction.naming_confidence = confidence
        transaction.name_source = source
        transaction.naming_reason = reason

        if transaction.category != "Uncategorised":
            transaction.category_confidence = "high" if confidence == "high" else "medium"
            transaction.category_reason = reason
        else:
            transaction.category_confidence = "unknown"
            transaction.category_reason = "No reliable category rule matched this transaction."

    @staticmethod
    def _find_merchant(description: str):
        for rule in MERCHANT_RULES:
            if rule.matches(description):
                return rule
        return None

    def _clean_generic_name(self, description: str) -> str:
        value = self._normalise_spaces(description)

        # Remove known transaction prefixes only from the start.
        for pattern in self.PREFIX_PATTERNS:
            updated = re.sub(pattern, "", value, flags=re.IGNORECASE).strip()
            if updated != value:
                value = updated
                break

        # Remove common trailing statement dates such as "13 May".
        value = re.sub(
            r"\s+\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\b\s*$",
            "",
            value,
            flags=re.IGNORECASE,
        )

        # Remove long standalone numerical references.  We deliberately require
        # at least six digits so ordinary store numbers are less likely to vanish.
        value = re.sub(r"\b\d{6,}\b", "", value)

        # Remove common masked/reference card fragments such as *1234 or ***1234
        # when they appear as standalone tokens.
        value = re.sub(r"(?<!\w)\*{1,4}\d{4,}(?!\w)", "", value)

        # Clean separators left behind after removing references.
        value = re.sub(r"\s+[|:/-]\s*$", "", value)
        value = self._normalise_spaces(value)

        return value.strip(" -|:/")

    @staticmethod
    def _normalise_spaces(value: str) -> str:
        return " ".join((value or "").split()).strip()
