from walletwise.extraction.banks.fnb.models import FNBTransactionDraft
from walletwise.models import Transaction, UnassignedRow
from walletwise.utils.money_utils import parse_money


def _normalise_amount(value: str) -> float | None:
    """FNB amount: Cr means money in; otherwise transaction amount is money out."""
    parsed = parse_money(value)
    if parsed is None:
        return None

    number, suffix = parsed
    if suffix == "cr":
        return abs(number)
    if suffix == "dr":
        return -abs(number)
    return -abs(number)


def _normalise_balance(value: str) -> float | None:
    parsed = parse_money(value)
    if parsed is None:
        return None

    number, suffix = parsed
    if suffix == "cr":
        return abs(number)
    if suffix == "dr":
        return -abs(number)
    return number


def _normalise_optional_number(value: str) -> float | None:
    parsed = parse_money(value)
    if parsed is None:
        return None

    number, suffix = parsed
    if suffix == "dr":
        return -abs(number)
    if suffix == "cr":
        return abs(number)
    return number


class FNBNormalizer:
    """Convert FNB-specific drafts into WalletWise's common Transaction model."""

    bank_name = "FNB"

    def transactions(self, drafts: list[FNBTransactionDraft]) -> list[Transaction]:
        result = []

        for draft in drafts:
            if draft.record_type == "closing_balance":
                amount = None
                transaction_type = "closing_balance"
            else:
                amount = _normalise_amount(draft.amount)
                if amount is None:
                    transaction_type = "unknown"
                elif amount > 0:
                    transaction_type = "credit"
                elif amount < 0:
                    transaction_type = "debit"
                else:
                    transaction_type = "unknown"

            transaction = Transaction(
                date=draft.date,
                raw_description=draft.description,
                display_name=draft.description,
                category="Uncategorised",
                amount=amount,
                transaction_type=transaction_type,
                balance=_normalise_balance(draft.balance),
                bank_charges=_normalise_optional_number(draft.charges),
                bank=self.bank_name,
                source_page=draft.page,
                ocr_used=draft.ocr_used,
                raw_date=draft.raw_date,
                raw_amount=draft.amount,
                raw_balance=draft.balance,
                raw_charges=draft.charges,
                review_reasons=list(draft.review_reasons),
            )

            if draft.record_type != "closing_balance" and draft.amount and amount is None:
                transaction.review_reasons.append(
                    f"FNB amount could not be normalised: {draft.amount}"
                )
            if draft.balance and transaction.balance is None:
                transaction.review_reasons.append(
                    f"FNB balance could not be normalised: {draft.balance}"
                )

            result.append(transaction)

        return result

    def orphan_rows(self, orphan_rows) -> list[UnassignedRow]:
        result = []
        for row, reason in orphan_rows:
            result.append(
                UnassignedRow(
                    bank=self.bank_name,
                    page=row.page,
                    raw_date=row.date,
                    raw_description=row.description,
                    raw_amount=row.amount,
                    raw_balance=row.balance,
                    reason=reason,
                )
            )
        return result
