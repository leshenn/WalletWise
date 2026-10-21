from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime
from typing import Any

from .reporting import ReportingService


class AIContextBuilder:
    """Build a small, privacy-conscious context for WalletWise AI.

    The external model never receives the statement PDF, raw bank descriptions,
    account/card/reference numbers, or the complete transaction history.

    Context is intent-specific: an affordability question receives different
    data from a spending-analysis question. This keeps requests cheaper, easier
    to inspect, and less likely to confuse the model with irrelevant numbers.
    """

    MAX_PERIODS = 12
    MAX_TRANSACTIONS = 5
    MAX_CATEGORY_ITEMS = 12

    AFFORDABILITY_WORDS = (
        "afford", "can i buy", "can i get", "should i buy", "purchase",
    )
    SAVING_GOAL_WORDS = (
        "save up", "save for", "saving goal", "savings goal", "how long to save",
        "need to save", "goal of", "goal is", "in 1 year", "in one year",
        "in 2 years", "in two years", "in 3 years", "in three years",
    )
    SPENDING_WORDS = (
        "spend", "spending", "spent", "where is my money", "where did my money",
        "why did", "cut back", "reduce", "overspend", "expenses", "expense",
    )
    SAVINGS_ANALYSIS_WORDS = (
        "how much have i saved", "how much did i save", "savings rate",
        "my savings", "saving more", "saving less", "net savings",
    )
    BUDGET_WORDS = ("budget", "budgeted", "actual vs", "over budget", "under budget")

    @classmethod
    def build(
        cls,
        *,
        question: str,
        transactions: list[dict],
        settings: dict | None = None,
        coverage: dict | None = None,
        history: list[dict] | None = None,
        conversation_summary: dict[str, Any] | None = None,
        intent_override: str | None = None,
        entity_hints: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not transactions:
            raise ValueError(
                "Upload at least one bank statement before asking WalletWise AI a financial question."
            )

        settings = settings or {}
        coverage = coverage or {}
        history = history or []
        conversation_summary = conversation_summary or {}
        entity_hints = entity_hints or {}
        reports = ReportingService.build(transactions, settings, coverage)

        summary_text = " ".join(
            str(conversation_summary.get(key) or "")
            for key in ("topic", "latestQuestion", "latestSummary")
        )
        prior_user_text = " ".join(
            str(item.get("content") or "")
            for item in history[-4:]
            if item.get("role") == "user"
        )
        analysis_text = f"{summary_text} {prior_user_text} {question}".strip().casefold()
        matched_categories = cls._matched_categories(analysis_text, transactions)
        hinted_category = str(entity_hints.get("category") or "").strip()
        if hinted_category and hinted_category.casefold() not in {item.casefold() for item in matched_categories}:
            matched_categories.insert(0, hinted_category)
        intent = (intent_override or "").strip() or cls._detect_intent(analysis_text, matched_categories)

        reporting_mode = str(settings.get("defaultTrackingView") or "calendar").casefold()
        use_pay_cycles = reporting_mode in {"pay_cycle", "pay-cycle", "paycycle"} and bool(reports.get("payCycles"))
        if use_pay_cycles:
            periods = (reports.get("payCycles") or [])[-cls.MAX_PERIODS :]
            complete_periods = [item for item in periods if item.get("complete")]
            reporting_mode = "pay_cycle"
        else:
            periods = (reports.get("monthly") or [])[-cls.MAX_PERIODS :]
            complete_periods = [item for item in periods if item.get("coverageStatus") != "partial"]
            reporting_mode = "calendar"

        averaging_periods = complete_periods or periods
        snapshot = cls._snapshot(averaging_periods)
        category_averages = cls._category_averages(averaging_periods)
        latest_balance = cls._latest_known_balance(transactions)
        savings = reports.get("savings") or {}
        budget = reports.get("budget") or {}

        context: dict[str, Any] = {
            "intent": intent,
            "currency": "ZAR",
            "reportingMode": reporting_mode,
        }
        request_hints = {
            key: entity_hints.get(key)
            for key in (
                "subject", "purchasePrice", "recurringPayment", "paymentFrequency",
                "commitmentType", "targetAmount", "timeframeMonths",
                "existingSavings", "periodicContribution", "contributionFrequency",
                "category", "merchant", "period"
            )
            if entity_hints.get(key) is not None
        }
        if request_hints:
            context["request"] = request_hints
        # Only send data-quality caveats when they can change the answer.
        # This avoids paying tokens for the same healthy metadata every request.
        incomplete_labels = cls._incomplete_labels(periods, reporting_mode)
        uncategorised_pct = cls._number((reports.get("metrics") or {}).get("uncategorisedSpendingPct"))
        quality: dict[str, Any] = {}
        if len(averaging_periods) < 3:
            quality["completePeriods"] = len(averaging_periods)
        if incomplete_labels:
            quality["incompletePeriods"] = incomplete_labels
        if uncategorised_pct >= 10:
            quality["uncategorisedSpendingPct"] = uncategorised_pct
        if quality:
            context["dataQuality"] = quality

        if intent == "affordability":
            context["financialSnapshot"] = snapshot
            recurring_payment = cls._number(entity_hints.get("recurringPayment"))
            if recurring_payment > 0:
                avg_income = cls._number(snapshot.get("averageIncome"))
                avg_after_expenses = cls._number(snapshot.get("averageCashAfterExpenses"))
                avg_net_savings = cls._number(snapshot.get("averageNetSavingsMovement"))
                context["projectedCommitmentImpact"] = {
                    "monthlyCommitment": recurring_payment,
                    "cashAfterExpensesAfterCommitment": round(avg_after_expenses - recurring_payment, 2),
                    "netSavingsCapacityAfterCommitment": round(avg_net_savings - recurring_payment, 2),
                    "commitmentShareOfAverageIncomePct": (
                        round(recurring_payment / avg_income * 100, 1) if avg_income > 0 else None
                    ),
                    "note": "Projection assumes the recurring payment is monthly and other spending stays similar.",
                }
            if latest_balance is not None:
                context["latestKnownAccountBalance"] = {
                    "amount": latest_balance,
                    "note": "Latest imported balance; not confirmed total savings.",
                }
            context["budget"] = cls._compact_budget_summary(budget)
            context["topFlexibleCategories"] = cls._flexible_categories(category_averages)

        elif intent == "saving_goal":
            context["financialSnapshot"] = snapshot
            if latest_balance is not None:
                context["latestKnownAccountBalance"] = {
                    "amount": latest_balance,
                    "note": "Latest imported balance; not confirmed goal savings.",
                }
            context["savings"] = {
                "historicalNetSavingsMovement": cls._number(savings.get("netSavingsMovement")),
                "averageMonthlyNetSavingsMovement": snapshot["averageNetSavingsMovement"],
            }
            context["planningScenarios"] = cls._planning_scenarios(
                snapshot["averageCashAfterExpenses"],
                snapshot["averageNetSavingsMovement"],
            )
            context["topFlexibleCategories"] = cls._flexible_categories(category_averages)

        elif intent in {"spending_analysis", "category_analysis"}:
            context["comparison"] = cls._latest_comparison(periods, matched_categories)
            if matched_categories:
                context["requestedCategories"] = matched_categories[:2]
            else:
                context["topAverageCategories"] = category_averages[: cls.MAX_CATEGORY_ITEMS]

        elif intent == "savings_analysis":
            context["savings"] = {
                "historicalNetSavingsMovement": cls._number(savings.get("netSavingsMovement")),
                "historicalSavingsTransferRatePct": cls._number(savings.get("savingsTransferRatePct")),
                "averageNetSavingsMovement": snapshot["averageNetSavingsMovement"],
            }
            context["savingsTrend"] = [
                {
                    "period": cls._period_label(item),
                    "income": cls._number(item.get("income")),
                    "netSavingsMovement": cls._number(item.get("netSavingsMovement")),
                }
                for item in periods
            ]
            context["budgetSavingsTarget"] = cls._number(budget.get("savingsTarget"))

        elif intent == "budget_analysis":
            context["financialSnapshot"] = snapshot
            context["budget"] = cls._compact_budget(budget, periods)

        else:
            context["financialSnapshot"] = snapshot
            context["topAverageCategories"] = category_averages[: cls.MAX_CATEGORY_ITEMS]
            if periods:
                context["latestPeriod"] = cls._safe_period(periods[-1])

        # Verified Python aggregates are available to the LLM for complex
        # questions. Without them the model sees only two-month summaries,
        # which makes multi-period/category conclusions unreliable.
        if intent in {"spending_analysis", "category_analysis", "savings_analysis", "general_finance"}:
            context["verifiedEvidence"] = cls._verified_evidence(
                transactions=transactions, reports=reports,
                requested_categories=matched_categories,
            )

        relevant_transactions = cls._relevant_transactions(
            analysis_text,
            transactions,
            intent,
            matched_categories,
        )
        if relevant_transactions:
            context["relevantTransactions"] = relevant_transactions

        data_used = {
            "intent": intent,
            "periodsIncluded": len(periods),
            "monthsIncluded": len(periods),  # backwards-compatible frontend label
            "transactionDetailsIncluded": len(relevant_transactions),
            "rawDescriptionsSent": False,
            "statementPdfSent": False,
            "accountOrCardNumbersSent": False,
            "contextSections": [key for key in context.keys() if key not in {"intent", "currency", "reportingMode"}],
        }
        return context, data_used

    @classmethod
    def _verified_evidence(
        cls, *, transactions: list[dict], reports: dict,
        requested_categories: list[str]
    ) -> dict[str, Any]:
        """Reproducible month-by-month evidence; no PDFs/account identifiers.

        This is deliberately aggregates only, not a substitute for a full
        financial query executor. A cap is advertised as incomplete evidence.
        """
        months_all = sorted(reports.get("monthly") or [], key=lambda row: str(row.get("month") or ""))
        months = months_all[-cls.MAX_PERIODS :]
        category_totals: dict[str, float] = defaultdict(float)
        by_month: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        fee_by_month: dict[str, float] = defaultdict(float)
        for row in transactions:
            day = str(row.get("date") or "")
            if len(day) < 7 or day[4:5] != "-":
                continue
            month = day[:7]
            if not (row.get("financialRole") == "expense" and cls._number(row.get("amount")) < 0):
                continue
            amount = abs(cls._number(row.get("amount")))
            category = str(row.get("category") or "Uncategorised")
            category_totals[category] += amount
            by_month[month][category] += amount
        for row in transactions:
            day = str(row.get("date") or "")
            if len(day) < 7:
                continue
            month = day[:7]
            category = str(row.get("category") or "").casefold()
            is_expense_fee = (category == "bank fees" and row.get("financialRole") == "expense"
                              and cls._number(row.get("amount")) < 0)
            if not is_expense_fee and row.get("transactionType") != "closing_balance":
                fee = abs(cls._number(row.get("bankCharges")))
                if fee > 0:
                    fee_by_month[month] += fee

        requested = {category.casefold() for category in requested_categories}
        ordered = sorted(category_totals, key=lambda c: category_totals[c], reverse=True)
        chosen = ordered[:24]
        chosen.extend(name for name in ordered if name.casefold() in requested and name not in chosen)
        shown_months = {str(p.get("month")) for p in months}
        monthly = []
        for period in months:
            ym = str(period.get("month") or "")
            monthly.append({
                "month": ym,
                "coverageStatus": period.get("coverageStatus"),
                "coveragePct": period.get("coveragePct"),
                "income": cls._number(period.get("income")),
                "spending": cls._number(period.get("spending")),
                "netSavingsMovement": cls._number(period.get("netSavingsMovement")),
                "categories": {name: round(by_month[ym].get(name, 0.0), 2)
                               for name in chosen if by_month[ym].get(name, 0.0)},
                "extraBankChargeFields": round(fee_by_month.get(ym, 0.0), 2),
            })
        return {
            "source": "verified WalletWise Python reporting aggregates; currency ZAR",
            "monthScope": "calendar months regardless of preferred pay-cycle view",
            "monthly": monthly,
            "includedMonthCount": len(months),
            "olderMonthsOmitted": len(months_all) > len(months),
            "categoryCount": len(ordered),
            "categoriesOmittedFromMonthlyBreakdown": len(chosen) < len(ordered),
            "additionalBankChargesNote": "extraBankChargeFields are charges not already represented as Bank Fees expense rows; add them only to bank-fee totals, not to overall spending without confirming inclusion",
            "coverageNote": "Partial or missing statement months must not be treated as representative zeros; an omitted category is not necessarily absent from underlying data.",
        }

    @classmethod
    def _detect_intent(cls, text: str, matched_categories: list[str]) -> str:
        # Affordability takes priority so "Can I afford a car?" is not mistaken
        # for a savings goal merely because it mentions a car.
        if any(word in text for word in cls.AFFORDABILITY_WORDS):
            return "affordability"
        if any(word in text for word in cls.SAVING_GOAL_WORDS) or re.search(
            r"\b(?:\d+|one|two|three|four|five)\s+(?:months?|years?)\b", text
        ):
            return "saving_goal"
        if any(word in text for word in cls.BUDGET_WORDS):
            return "budget_analysis"
        if any(word in text for word in cls.SAVINGS_ANALYSIS_WORDS):
            return "savings_analysis"
        if matched_categories and any(word in text for word in cls.SPENDING_WORDS):
            return "category_analysis"
        if any(word in text for word in cls.SPENDING_WORDS):
            return "spending_analysis"
        return "general_finance"

    @classmethod
    def _snapshot(cls, periods: list[dict]) -> dict[str, float]:
        avg_income = cls._average([item.get("income") for item in periods])
        avg_spending = cls._average([item.get("spending") for item in periods])
        avg_refunds = cls._average([item.get("refunds") for item in periods])
        avg_savings = cls._average([item.get("netSavingsMovement") for item in periods])
        avg_flexible = cls._average([item.get("flexibleSpending") for item in periods])
        return {
            "averageIncome": avg_income,
            "averageSpending": avg_spending,
            "averageRefunds": avg_refunds,
            "averageCashAfterExpenses": round(avg_income + avg_refunds - avg_spending, 2),
            "averageNetSavingsMovement": avg_savings,
            "averageFlexibleSpending": avg_flexible,
        }

    @classmethod
    def _category_averages(cls, periods: list[dict]) -> list[dict]:
        values: dict[str, list[float]] = defaultdict(list)
        all_categories = sorted({
            str(item.get("name"))
            for period in periods
            for item in (period.get("categoryBreakdown") or [])
            if item.get("name")
        }, key=str.casefold)

        for period in periods:
            by_category = {
                str(item.get("name")): cls._number(item.get("amount"))
                for item in (period.get("categoryBreakdown") or [])
                if item.get("name")
            }
            for category in all_categories:
                values[category].append(by_category.get(category, 0.0))

        result = [
            {"category": category, "averageMonthlySpend": cls._average(month_values)}
            for category, month_values in values.items()
        ]
        result.sort(key=lambda item: item["averageMonthlySpend"], reverse=True)
        return result

    @classmethod
    def _flexible_categories(cls, category_averages: list[dict]) -> list[dict]:
        flexible_names = {name.casefold() for name in ReportingService.FLEXIBLE_CATEGORIES}
        flexible = [
            item for item in category_averages
            if str(item.get("category") or "").casefold() in flexible_names
        ]
        return flexible[:4]

    @classmethod
    def _latest_comparison(cls, periods: list[dict], categories: list[str]) -> dict:
        if not periods:
            return {}
        current = periods[-1]
        previous = periods[-2] if len(periods) > 1 else None
        result: dict[str, Any] = {
            "current": cls._safe_period(current),
            "previous": cls._safe_period(previous) if previous else None,
        }
        if previous:
            result["spendingChangeAmount"] = round(
                cls._number(current.get("spending")) - cls._number(previous.get("spending")), 2
            )
            result["spendingChangePct"] = cls._percent_change(
                cls._number(previous.get("spending")), cls._number(current.get("spending"))
            )
            category_changes = cls._category_changes(previous, current)
            if categories:
                wanted = {category.casefold() for category in categories}
                category_changes = [
                    item for item in category_changes
                    if str(item.get("category") or "").casefold() in wanted
                ]
            result["categoryChanges"] = category_changes[: cls.MAX_CATEGORY_ITEMS]
        return result

    @classmethod
    def _category_changes(cls, previous: dict, current: dict) -> list[dict]:
        previous_values = {
            str(item.get("name")): cls._number(item.get("amount"))
            for item in (previous.get("categoryBreakdown") or [])
            if item.get("name")
        }
        current_values = {
            str(item.get("name")): cls._number(item.get("amount"))
            for item in (current.get("categoryBreakdown") or [])
            if item.get("name")
        }
        result = []
        for category in set(previous_values) | set(current_values):
            old = previous_values.get(category, 0.0)
            new = current_values.get(category, 0.0)
            result.append({
                "category": category,
                "previous": old,
                "current": new,
                "changeAmount": round(new - old, 2),
                "changePct": cls._percent_change(old, new),
            })
        result.sort(key=lambda item: abs(item["changeAmount"]), reverse=True)
        return result

    @classmethod
    def _compact_budget_summary(cls, budget: dict) -> dict:
        if not budget:
            return {}
        return {
            "profileCompleted": bool(budget.get("profileCompleted")),
            "incomeBasis": cls._number(budget.get("incomeBasis")),
            "plannedExpenses": cls._number(budget.get("plannedExpenses")),
            "savingsTarget": cls._number(budget.get("savingsTarget")),
            "remainingAfterPlan": cls._number(budget.get("remainingAfterPlan")),
        }

    @classmethod
    def _compact_budget(cls, budget: dict, periods: list[dict]) -> dict:
        result = cls._compact_budget_summary(budget)
        latest_month = None
        if periods:
            last = periods[-1]
            latest_month = last.get("month")
        compact_items = []
        for item in (budget.get("items") or []):
            actual_by_month = item.get("actualByMonth") or {}
            compact_items.append({
                "category": item.get("category"),
                "group": item.get("group"),
                "budgeted": cls._number(item.get("budgeted")),
                "recentAverage": cls._number(item.get("recentAverage")),
                "latestActual": cls._number(actual_by_month.get(latest_month)) if latest_month else None,
            })
        result["items"] = compact_items[:8]
        return result

    @classmethod
    def _matched_categories(cls, text: str, transactions: list[dict]) -> list[str]:
        categories = sorted({
            str(item.get("category"))
            for item in transactions
            if item.get("category") and item.get("category") != "Uncategorised"
        }, key=len, reverse=True)
        return [category for category in categories if category.casefold() in text][:3]

    @classmethod
    def _relevant_transactions(
        cls,
        text: str,
        transactions: list[dict],
        intent: str,
        matched_categories: list[str],
    ) -> list[dict]:
        needs_detail = intent == "category_analysis" or any(
            phrase in text
            for phrase in (
                "transaction", "transactions", "why did", "what did i buy",
                "what did i spend", "which purchases", "which purchase", "merchant",
            )
        )

        merchants = sorted({
            str(item.get("displayName") or "").strip()
            for item in transactions
            if item.get("displayName")
        }, key=len, reverse=True)
        matched_merchants = [
            merchant for merchant in merchants
            if len(merchant) >= 4 and merchant.casefold() in text
        ][:2]
        if matched_merchants:
            needs_detail = True

        if not needs_detail:
            return []

        candidates = []
        for item in transactions:
            if item.get("transactionType") == "closing_balance" or item.get("financialRole") == "balance":
                continue
            if matched_categories and item.get("category") not in matched_categories:
                continue
            if matched_merchants and item.get("displayName") not in matched_merchants:
                continue
            if not matched_categories and not matched_merchants and intent == "spending_analysis":
                if item.get("financialRole") != "expense":
                    continue
            candidates.append(item)

        candidates.sort(
            key=lambda item: (
                cls._parse_date(item.get("date")) or date.min,
                abs(cls._number(item.get("amount"))),
            ),
            reverse=True,
        )
        return [cls._sanitise_transaction(item) for item in candidates[: cls.MAX_TRANSACTIONS]]

    @classmethod
    def _sanitise_transaction(cls, item: dict) -> dict:
        return {
            "date": item.get("date"),
            "merchant": item.get("displayName") or "Unknown",
            "category": item.get("category") or "Uncategorised",
            "financialRole": item.get("financialRole") or "unknown",
            "amount": cls._number(item.get("amount")),
        }

    @classmethod
    def _safe_period(cls, period: dict | None) -> dict | None:
        if not period:
            return None
        return {
            "label": cls._period_label(period),
            "income": cls._number(period.get("income")),
            "spending": cls._number(period.get("spending")),
            "refunds": cls._number(period.get("refunds")),
            "netCashFlow": cls._number(period.get("netCashFlow")),
            "flexibleSpending": cls._number(period.get("flexibleSpending")),
            "netSavingsMovement": cls._number(period.get("netSavingsMovement")),
            "complete": cls._is_complete_period(period),
        }

    @staticmethod
    def _period_label(period: dict) -> str | None:
        return period.get("label") or period.get("month") or period.get("id")

    @staticmethod
    def _is_complete_period(period: dict) -> bool:
        if "complete" in period:
            return bool(period.get("complete"))
        return period.get("coverageStatus") != "partial"

    @classmethod
    def _incomplete_labels(cls, periods: list[dict], reporting_mode: str) -> list[str]:
        labels = []
        for period in periods:
            incomplete = not cls._is_complete_period(period)
            if incomplete:
                labels.append(str(cls._period_label(period) or "Unknown period"))
        return labels[:3]

    @classmethod
    def _planning_scenarios(cls, monthly_cash_after_expenses: float, observed_savings: float) -> dict:
        available = max(0.0, monthly_cash_after_expenses)
        observed = max(0.0, observed_savings)
        baseline = observed if observed > 0 else available
        return {
            "monthlyBaseline": round(baseline, 2),
            "12Months": round(baseline * 12, 2),
            "24Months": round(baseline * 24, 2),
            "36Months": round(baseline * 36, 2),
        }

    @classmethod
    def _latest_known_balance(cls, transactions: list[dict]) -> float | None:
        dated = []
        for item in transactions:
            balance = item.get("balance")
            day = cls._parse_date(item.get("date"))
            if balance is None or day is None:
                continue
            dated.append((day, cls._number(balance)))
        if not dated:
            return None
        dated.sort(key=lambda pair: pair[0])
        return round(dated[-1][1], 2)

    @staticmethod
    def _number(value: Any) -> float:
        try:
            return round(float(value or 0), 2)
        except (TypeError, ValueError):
            return 0.0

    @classmethod
    def _average(cls, values: list[Any]) -> float:
        numeric = [cls._number(value) for value in values if value is not None]
        return round(sum(numeric) / len(numeric), 2) if numeric else 0.0

    @staticmethod
    def _percent_change(previous: float, current: float) -> float | None:
        if previous == 0:
            return None if current != 0 else 0.0
        return round(((current - previous) / previous) * 100, 1)

    @staticmethod
    def _parse_date(value: Any) -> date | None:
        if not value:
            return None
        if isinstance(value, date):
            return value
        text = str(value).strip()
        for fmt in ("%Y-%m-%d", "%d %b %Y", "%d %B %Y"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        try:
            return date.fromisoformat(text[:10])
        except (ValueError, TypeError):
            return None
