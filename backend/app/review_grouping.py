from __future__ import annotations

import re
from collections import Counter
from difflib import SequenceMatcher


class ReviewGroupingService:
    """Group review work by priority without merging database records.

    Priority:
      1. financial role needs confirmation
      2. role is known, but category is still unknown
      3. category exists, but needs verification
      4. only the friendly name/extraction still needs attention
    """

    SIMILARITY_THRESHOLD = 0.90
    TOKEN_OVERLAP_THRESHOLD = 0.60

    GENERIC_WORDS = {
        "pos", "purchase", "card", "payment", "paid", "debit", "credit",
        "order", "eft", "fnb", "app", "online", "transfer", "to", "from",
        "transaction", "ref", "reference", "bank", "banking", "the",
    }

    VALID_ROLES = {"expense", "income", "transfer", "refund", "balance"}

    @classmethod
    def review_buckets(cls, transactions: list[dict]) -> dict:
        items = [
            transaction
            for transaction in transactions
            if transaction.get("transactionType") != "closing_balance"
            and transaction.get("financialRole") != "balance"
        ]

        role_review = [item for item in items if cls.review_status(item) == "roleReview"]
        uncategorised = [item for item in items if cls.review_status(item) == "uncategorised"]
        category_review = [item for item in items if cls.review_status(item) == "categoryReview"]
        name_review = [item for item in items if cls.review_status(item) == "nameReview"]

        return {
            "roleReview": cls._groups_for_items(role_review, "roleReview"),
            "uncategorised": cls._groups_for_items(uncategorised, "uncategorised"),
            "categoryReview": cls._groups_for_items(category_review, "categoryReview"),
            "nameReview": cls._groups_for_items(name_review, "nameReview"),
            "counts": {
                "roleReview": len(role_review),
                "uncategorised": len(uncategorised),
                "categoryReview": len(category_review),
                "nameReview": len(name_review),
            },
            "totalCount": len(role_review) + len(uncategorised) + len(category_review) + len(name_review),
            "readyCount": len(items) - (len(role_review) + len(uncategorised) + len(category_review) + len(name_review)),
        }

    @classmethod
    def review_status(cls, transaction: dict) -> str | None:
        """Return the single highest-priority review bucket for a transaction."""
        if transaction.get("transactionType") == "closing_balance" or transaction.get("financialRole") == "balance":
            return None

        if (
            transaction.get("financialRole") not in cls.VALID_ROLES
            or transaction.get("roleConfidence") != "high"
        ):
            return "roleReview"

        if not transaction.get("category") or transaction.get("category") == "Uncategorised":
            return "uncategorised"

        if transaction.get("categoryConfidence") != "high":
            return "categoryReview"

        if transaction.get("namingConfidence") != "high":
            return "nameReview"

        extraction = transaction.get("extractionConfidence") or "unknown"
        # Low-confidence extraction can indicate missing/incorrect essential data,
        # so it remains reviewable. Medium OCR extraction is considered complete
        # after a person explicitly verifies the transaction.
        if extraction == "low":
            return "nameReview"
        if extraction != "high" and not transaction.get("humanVerified"):
            return "nameReview"

        return None

    @staticmethod
    def highest_priority_bucket(statuses: list[str]) -> str | None:
        priority = ("roleReview", "uncategorised", "categoryReview", "nameReview")
        for bucket in priority:
            if bucket in statuses:
                return bucket
        return None

    @classmethod
    def _groups_for_items(cls, review_items: list[dict], review_type: str) -> list[dict]:
        if not review_items:
            return []

        clusters: list[dict] = []

        for transaction in review_items:
            fingerprint = cls._fingerprint(transaction)
            best_cluster = None
            best_score = 0.0
            best_basis = "single"

            for cluster in clusters:
                score, basis = cls._similarity(fingerprint, cluster["fingerprint"])
                if score > best_score:
                    best_score = score
                    best_cluster = cluster
                    best_basis = basis

            if best_cluster is not None and best_score >= cls.SIMILARITY_THRESHOLD:
                best_cluster["transactions"].append(transaction)
                best_cluster["scores"].append(best_score)
                if best_basis == "exact":
                    best_cluster["exactMatches"] += 1
            else:
                clusters.append(
                    {
                        "fingerprint": fingerprint,
                        "transactions": [transaction],
                        "scores": [1.0],
                        "exactMatches": 1,
                    }
                )

        result = []
        for index, cluster in enumerate(clusters, start=1):
            items = sorted(
                cluster["transactions"],
                key=lambda item: (item.get("date") or "", item.get("id") or ""),
            )
            suggested_name = cls._most_common_value(
                [item.get("displayName") for item in items],
                fallback=items[0].get("rawDescription") or "Unknown transaction",
            )
            suggested_category = cls._most_common_value(
                [
                    item.get("category")
                    for item in items
                    if item.get("category")
                    and item.get("category") != "Uncategorised"
                ],
                fallback="Uncategorised",
            )
            suggested_role = cls._most_common_value(
                [
                    item.get("financialRole")
                    for item in items
                    if item.get("financialRole")
                    and item.get("financialRole") != "unknown"
                ],
                fallback="unknown",
            )

            all_exact = len(items) > 1 and cluster["exactMatches"] == len(items)
            average_score = sum(cluster["scores"]) / len(cluster["scores"])

            if len(items) == 1:
                grouping_confidence = "single"
                reason = "No sufficiently similar review transaction was found."
            elif all_exact:
                grouping_confidence = "high"
                reason = "Grouped because the normalised transaction names match."
            else:
                grouping_confidence = "medium"
                reason = (
                    "Grouped because the transaction names are very similar. "
                    "Expand the group to verify each item."
                )

            result.append(
                {
                    "groupId": f"{review_type}-group-{index}",
                    "reviewType": review_type,
                    "size": len(items),
                    "suggestedDisplayName": suggested_name,
                    "suggestedCategory": suggested_category,
                    "suggestedFinancialRole": suggested_role,
                    "groupingConfidence": grouping_confidence,
                    "groupingScore": round(average_score, 3),
                    "groupingReason": reason,
                    "transactions": items,
                }
            )

        result.sort(
            key=lambda group: (-group["size"], group["suggestedDisplayName"].casefold())
        )
        return result

    @classmethod
    def _fingerprint(cls, transaction: dict) -> str:
        source = (
            transaction.get("displayName")
            or transaction.get("rawDescription")
            or ""
        ).casefold()
        source = re.sub(r"\b\d{1,4}[/-]\d{1,2}(?:[/-]\d{2,4})?\b", " ", source)
        source = re.sub(r"\b\d{4,}\b", " ", source)
        source = re.sub(r"[^a-z0-9]+", " ", source)
        tokens = [
            token
            for token in source.split()
            if token not in cls.GENERIC_WORDS and len(token) > 1
        ]
        if not tokens:
            tokens = [token for token in source.split() if len(token) > 1]
        return " ".join(tokens).strip()

    @classmethod
    def _similarity(cls, left: str, right: str) -> tuple[float, str]:
        if not left or not right:
            return 0.0, "none"
        if left == right:
            return 1.0, "exact"

        left_tokens = left.split()
        right_tokens = right.split()
        if len(left_tokens) == 1 or len(right_tokens) == 1:
            return 0.0, "none"

        sequence_score = SequenceMatcher(None, left, right).ratio()
        left_set = set(left_tokens)
        right_set = set(right_tokens)
        union = left_set | right_set
        token_overlap = len(left_set & right_set) / len(union) if union else 0.0

        if token_overlap < cls.TOKEN_OVERLAP_THRESHOLD:
            return 0.0, "none"

        score = (sequence_score * 0.7) + (token_overlap * 0.3)
        return score, "similar"

    @staticmethod
    def _most_common_value(values: list[str | None], fallback: str) -> str:
        cleaned = [value.strip() for value in values if value and value.strip()]
        if not cleaned:
            return fallback
        return Counter(cleaned).most_common(1)[0][0]
