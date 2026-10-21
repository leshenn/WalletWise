from __future__ import annotations

from .repositories import DatasetRepository
from .review_grouping import ReviewGroupingService


class ReviewService:
    """Own the business rules for a human review confirmation.

    The frontend only sends the user's choices. This service decides confidence,
    reasons, verification state, and whether the transaction still belongs in a
    review bucket.
    """

    VALID_ROLES = {"expense", "income", "transfer", "refund"}

    def __init__(self, dataset_repository: DatasetRepository):
        self.dataset_repository = dataset_repository

    def confirm(
        self,
        *,
        transaction_ids: list[str],
        financial_role: str,
        category: str | None,
        display_name: str | None,
    ) -> dict:
        if financial_role not in self.VALID_ROLES:
            raise ValueError("Choose Expense, Income, Transfer, or Refund before confirming.")

        cleaned_category = (category or "Uncategorised").strip() or "Uncategorised"
        cleaned_name = (display_name or "").strip()
        grouped = len(set(transaction_ids)) > 1

        updates = {
            "financialRole": financial_role,
            "roleConfidence": "high",
            "roleReason": (
                "Financial role confirmed by user from grouped review"
                if grouped
                else "Financial role confirmed by user"
            ),
            "category": cleaned_category,
            "categoryConfidence": "high" if cleaned_category != "Uncategorised" else "unknown",
            "categoryReason": (
                "Category confirmed by user"
                if cleaned_category != "Uncategorised"
                else "Financial role confirmed; category still needs review"
            ),
            "namingConfidence": "high",
            "nameReason": (
                "Confirmed by user from grouped review"
                if grouped
                else "Confirmed by user"
            ),
            "humanVerified": True,
        }
        if cleaned_name:
            updates["displayName"] = cleaned_name

        updated = self.dataset_repository.update_transactions(transaction_ids, updates)

        statuses = [ReviewGroupingService.review_status(item) for item in updated]
        remaining = [status for status in statuses if status is not None]
        next_bucket = ReviewGroupingService.highest_priority_bucket(remaining)

        return {
            "updatedTransactions": updated,
            "reviewComplete": not remaining,
            "nextReviewBucket": next_bucket,
            "remainingReviewTypes": remaining,
            "message": self._message(next_bucket),
        }

    @staticmethod
    def _message(next_bucket: str | None) -> str:
        if next_bucket == "uncategorised":
            return "Money movement confirmed. Choose a category to finish this transaction."
        if next_bucket == "categoryReview":
            return "Saved. The category still needs a quick verification."
        if next_bucket == "nameReview":
            return "Saved. Only the name/extraction review remains."
        if next_bucket == "roleReview":
            return "The financial role still needs confirmation."
        return "Confirmed. This transaction no longer needs review."
