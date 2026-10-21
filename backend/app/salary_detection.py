from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from statistics import mean, pstdev


class SalaryDetectionService:
    """Suggest and remember salary sources without guessing silently.

    WalletWise may *suggest* likely salary payments from recurring incoming
    transactions. A source only becomes trusted after the user confirms it.
    Once confirmed, matching historical and future incoming payments can be
    marked as salary anchors automatically.
    """

    SALARY_WORDS = re.compile(
        r"\b(?:salary|payroll|wages?|remuneration|monthly\s+pay|nett?\s+pay)\b",
        flags=re.IGNORECASE,
    )
    MONTH_WORDS = {
        "jan", "january", "feb", "february", "mar", "march", "apr", "april",
        "may", "jun", "june", "jul", "july", "aug", "august", "sep", "sept",
        "september", "oct", "october", "nov", "november", "dec", "december",
    }
    STOP_WORDS = {
        "fnb", "app", "payment", "paid", "from", "credit", "eft", "rtc", "cash",
        "deposit", "received", "reference", "ref", "salary", "payroll", "wage",
        "wages", "remuneration", "monthly", "pay", "bank", "online", "transfer",
        "internet", "banking", "incoming", "cr",
    }

    @classmethod
    def source_key(cls, transaction: dict) -> str:
        raw = transaction.get("rawDescription") or transaction.get("displayName") or ""
        text = raw.casefold()
        text = re.sub(r"[^a-z0-9\s]", " ", text)
        tokens = []
        for token in text.split():
            if token in cls.STOP_WORDS or token in cls.MONTH_WORDS:
                continue
            if any(character.isdigit() for character in token):
                continue
            if len(token) <= 1:
                continue
            tokens.append(token)

        # Keep enough identity to distinguish employers while avoiding volatile
        # reference/date fragments. The first few stable words are usually the
        # employer/source on FNB descriptions.
        key = " ".join(tokens[:8]).strip()
        if key:
            return key

        fallback = (transaction.get("displayName") or raw).casefold()
        fallback = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", fallback)).strip()
        return fallback

    @classmethod
    def _is_income(cls, transaction: dict) -> bool:
        return (
            transaction.get("financialRole") == "income"
            and float(transaction.get("amount") or 0) > 0
            and transaction.get("transactionType") != "closing_balance"
        )

    @staticmethod
    def _month(value: str | None) -> str | None:
        if not value:
            return None
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            return None
        return parsed.strftime("%Y-%m")

    @staticmethod
    def _day(value: str | None) -> date | None:
        if not value:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None

    @classmethod
    def matches_source(cls, transaction: dict, match_text: str) -> bool:
        return cls._is_income(transaction) and cls.source_key(transaction) == match_text

    @classmethod
    def build_income_overview(cls, transactions: list[dict], saved_sources: list[dict]) -> dict:
        income = [item for item in transactions if cls._is_income(item)]
        saved_by_key = {item["matchText"]: item for item in saved_sources if item.get("active", True)}

        grouped: dict[str, list[dict]] = defaultdict(list)
        for item in income:
            grouped[cls.source_key(item)].append(item)

        suggestions = []
        confirmed = []
        other_income = []

        for key, items in grouped.items():
            items = sorted(items, key=lambda item: item.get("date") or "")
            active_items = [item for item in items if not item.get("salaryExcluded")]
            dismissed_as_salary = bool(items) and not active_items
            scoring_items = active_items or items
            amounts = [float(item.get("amount") or 0) for item in scoring_items]
            months = sorted({month for month in (cls._month(item.get("date")) for item in scoring_items) if month})
            days = [day for day in (cls._day(item.get("date")) for item in scoring_items) if day]
            gaps = [(days[index] - days[index - 1]).days for index in range(1, len(days))]

            raw_text = " ".join(
                str(part or "")
                for item in scoring_items
                for part in (item.get("rawDescription"), item.get("displayName"))
            )
            keyword_hit = bool(cls.SALARY_WORDS.search(raw_text))
            category_hit = any(item.get("category") == "Salary" for item in scoring_items)
            recurring = len(months) >= 2
            monthly_cadence = any(20 <= gap <= 40 for gap in gaps)
            avg_amount = round(mean(amounts), 2) if amounts else 0.0
            consistency = 0.0
            if len(amounts) >= 2 and avg_amount > 0:
                consistency = pstdev(amounts) / avg_amount

            saved = saved_by_key.get(key)
            score = 0
            reasons = []
            if saved:
                score = 100
                reasons.append("You previously confirmed this salary source.")
            elif dismissed_as_salary:
                score = 0
                reasons.append("You marked this income source as not salary. You can add it back manually at any time.")
            else:
                if keyword_hit:
                    score += 45
                    reasons.append("The description contains salary/payroll wording.")
                if category_hit:
                    score += 25
                    reasons.append("WalletWise already categorised one or more payments as Salary.")
                if recurring:
                    score += 25
                    reasons.append(f"The same income source appears across {len(months)} months.")
                if monthly_cadence:
                    score += 15
                    reasons.append("Payments recur roughly monthly.")
                if len(amounts) >= 2 and consistency <= 0.15:
                    score += 15
                    reasons.append("The payment amount is fairly consistent.")
                if len(scoring_items) >= 3:
                    score += 10
                    reasons.append("This source has repeated at least three times.")
                score = min(score, 100)

            if score >= 75:
                confidence = "high"
            elif score >= 45:
                confidence = "medium"
            else:
                confidence = "low"

            latest = items[-1]
            all_months = sorted({month for month in (cls._month(item.get("date")) for item in items) if month})
            entry = {
                "sourceKey": key,
                "sourceId": saved.get("id") if saved else None,
                "label": saved.get("label") if saved else (latest.get("displayName") or latest.get("rawDescription") or "Income"),
                "confidence": "confirmed" if saved else confidence,
                "score": score,
                "reasons": reasons or ["Incoming money was detected, but there is not enough evidence to call it salary."],
                "occurrences": len(items),
                "months": len(all_months),
                "dismissedAsSalary": dismissed_as_salary,
                "averageAmount": avg_amount,
                "latestAmount": round(float(latest.get("amount") or 0), 2),
                "latestDate": latest.get("date"),
                "latestTransactionId": latest.get("id"),
                "transactions": [
                    {
                        "id": item.get("id"),
                        "date": item.get("date"),
                        "amount": item.get("amount"),
                        "displayName": item.get("displayName"),
                        "rawDescription": item.get("rawDescription"),
                        "isSalary": bool(item.get("isSalary")),
                        "salaryExcluded": bool(item.get("salaryExcluded")),
                    }
                    for item in reversed(items)
                ],
            }

            if saved:
                confirmed.append(entry)
            elif score >= 45:
                suggestions.append(entry)
            else:
                other_income.append(entry)

        suggestions.sort(key=lambda item: (-item["score"], item["label"].casefold()))
        confirmed.sort(key=lambda item: item["label"].casefold())
        other_income.sort(key=lambda item: (-item["latestAmount"], item["label"].casefold()))

        return {
            "confirmedSalarySources": confirmed,
            "salarySuggestions": suggestions,
            "otherIncome": other_income,
            "incomeTransactionCount": len(income),
        }
