from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from .budgeting import BudgetService


class ReportingService:
    """Build bank-independent WalletWise reporting data.

    The frontend receives already-calculated reporting values. It does not decide
    what counts as income, spending, transfers, savings movement, or reviewable
    financial data.
    """

    FLEXIBLE_CATEGORIES = {
        "Eating Out",
        "Entertainment",
        "Shopping",
        "Subscriptions",
    }

    @classmethod
    def build(
        cls,
        transactions: list[dict],
        settings: dict | None = None,
        coverage: dict | None = None,
    ) -> dict:
        usable = [
            item
            for item in transactions
            if item.get("transactionType") != "closing_balance"
            and item.get("financialRole") != "balance"
        ]

        expenses = [
            item for item in usable
            if item.get("financialRole") == "expense"
            and cls._number(item.get("amount")) < 0
        ]
        income_items = [
            item for item in usable
            if item.get("financialRole") == "income"
            and cls._number(item.get("amount")) > 0
        ]
        refund_items = [
            item for item in usable
            if item.get("financialRole") == "refund"
            and cls._number(item.get("amount")) > 0
        ]
        transfer_items = [
            item for item in usable
            if item.get("financialRole") == "transfer"
        ]

        income = round(sum(cls._number(item.get("amount")) for item in income_items), 2)
        refunds = round(sum(cls._number(item.get("amount")) for item in refund_items), 2)
        spending = round(sum(abs(cls._number(item.get("amount"))) for item in expenses), 2)
        transfers = round(sum(abs(cls._number(item.get("amount"))) for item in transfer_items), 2)
        net_cash_flow = round(income + refunds - spending, 2)

        category_breakdown = cls._breakdown(expenses, "category")
        merchant_breakdown = cls._breakdown(expenses, "displayName", limit=10)

        months: dict[str, list[dict]] = defaultdict(list)
        for item in usable:
            month_key = cls._month_key(item.get("date"))
            if month_key:
                months[month_key].append(item)

        # Include every month represented by imported statement coverage, even
        # when the selected statement contains no reportable transactions in a
        # particular month. Missing gap months are not treated as zero-spend
        # months because that would be misleading.
        coverage = coverage or {}
        coverage_by_month = {
            item.get("month"): item
            for item in coverage.get("months", [])
            if item.get("month") and item.get("status") != "missing"
        }
        month_keys = set(months) | set(coverage_by_month)

        monthly = []
        previous = None
        for month_key in sorted(month_keys):
            report = cls._month_report(month_key, months.get(month_key, []))
            coverage_item = coverage_by_month.get(month_key)
            report["coverageStatus"] = coverage_item.get("status") if coverage_item else None
            report["coveragePct"] = coverage_item.get("coveragePct") if coverage_item else None
            report["changeFromPrevious"] = cls._month_change(previous, report)
            monthly.append(report)
            previous = report

        monthly_spending = [item["spending"] for item in monthly]
        monthly_income = [item["income"] for item in monthly]

        expense_values = [abs(cls._number(item.get("amount"))) for item in expenses]
        largest_expense = cls._largest_expense(expenses)
        average_expense = round(sum(expense_values) / len(expense_values), 2) if expense_values else 0.0

        dated_expenses = [item for item in expenses if cls._parse_date(item.get("date"))]
        average_daily_spending = cls._average_daily_spending(dated_expenses)

        uncategorised_spending = round(sum(
            abs(cls._number(item.get("amount")))
            for item in expenses
            if not item.get("category") or item.get("category") == "Uncategorised"
        ), 2)
        flexible_spending = round(sum(
            abs(cls._number(item.get("amount")))
            for item in expenses
            if item.get("category") in cls.FLEXIBLE_CATEGORIES
        ), 2)

        top3_total = sum(item["amount"] for item in category_breakdown[:3])

        savings = cls._savings_metrics(transfer_items, income)
        recurring_merchants = cls._recurring_merchants(expenses)
        confidence = cls._confidence_overview(usable)

        latest_comparison = monthly[-1] if monthly else None
        pay_cycles = cls._pay_cycles(usable)
        category_trend = cls._category_trend(monthly)
        all_months_complete = cls._multi_month_summary(monthly, include_partial=False)
        all_months_with_partial = cls._multi_month_summary(monthly, include_partial=True)
        settings = settings or {}
        budget = BudgetService.suggest(monthly, settings.get("budgetProfile"))

        return {
            "summary": {
                "income": income,
                "spending": spending,
                "refunds": refunds,
                "transfers": transfers,
                "netCashFlow": net_cash_flow,
                "transactionCount": len(usable),
                "expenseCount": len(expenses),
            },
            "savings": savings,
            "monthly": monthly,
            "availableMonths": [
                {
                    "month": item["month"],
                    "label": item["label"],
                    "coverageStatus": item.get("coverageStatus"),
                    "coveragePct": item.get("coveragePct"),
                }
                for item in monthly
            ],
            "latestMonth": latest_comparison,
            "payCycles": pay_cycles,
            "categoryTrend": category_trend,
            "allMonthsComparison": {
                "completeOnly": all_months_complete,
                "includingPartial": all_months_with_partial,
                "hasPartialMonths": any(item.get("coverageStatus") == "partial" for item in monthly),
            },
            "budget": budget,
            "tracking": {
                "defaultView": settings.get("defaultTrackingView", "calendar"),
                "expectedPayday": settings.get("expectedPayday"),
                "salaryAnchorCount": sum(1 for item in usable if item.get("isSalary")),
                "payCycleReady": len(pay_cycles) > 0,
            },
            "categoryBreakdown": category_breakdown,
            "merchantBreakdown": merchant_breakdown,
            "metrics": {
                "averageMonthlySpending": cls._average(monthly_spending),
                "averageMonthlyIncome": cls._average(monthly_income),
                "averageExpenseTransaction": average_expense,
                "averageDailySpending": average_daily_spending,
                "largestExpense": largest_expense,
                "topCategory": category_breakdown[0] if category_breakdown else None,
                "topMerchant": merchant_breakdown[0] if merchant_breakdown else None,
                "top3CategorySharePct": cls._pct(top3_total, spending),
                "uncategorisedSpending": uncategorised_spending,
                "uncategorisedSpendingPct": cls._pct(uncategorised_spending, spending),
                "flexibleSpending": flexible_spending,
                "flexibleSpendingPct": cls._pct(flexible_spending, spending),
                **savings,
            },
            "recurringMerchants": recurring_merchants,
            "confidence": confidence,
            "insights": cls._insights(
                monthly=monthly,
                category_breakdown=category_breakdown,
                spending=spending,
                flexible_spending=flexible_spending,
                uncategorised_spending=uncategorised_spending,
                savings=savings,
            ),
        }


    @classmethod
    def _pay_cycles(cls, items: list[dict]) -> list[dict]:
        dated = [(cls._parse_date(item.get("date")), item) for item in items]
        dated = [(day, item) for day, item in dated if day is not None]
        if not dated:
            return []

        anchors = sorted({day for day, item in dated if item.get("isSalary")})
        if not anchors:
            return []

        latest_date = max(day for day, _ in dated)
        cycles = []
        previous = None
        for index, start in enumerate(anchors):
            next_start = anchors[index + 1] if index + 1 < len(anchors) else None
            end = (next_start - timedelta(days=1)) if next_start else latest_date
            if end < start:
                continue
            cycle_items = [item for day, item in dated if start <= day <= end]
            # A paycheck received in June funds the period leading up to the
            # next paycheck in July, so WalletWise presents it as the July
            # period. This keeps the label aligned with the month the money is
            # primarily intended to carry the user into.
            if start.month == 12:
                period_month = date(start.year + 1, 1, 1)
            else:
                period_month = date(start.year, start.month + 1, 1)
            period_label = f"{period_month.strftime('%B %Y')} period"
            date_range_label = f"{start.strftime('%d %b %Y')} – {end.strftime('%d %b %Y')}"

            report = cls._period_report(
                period_id=f"pay-{start.isoformat()}",
                label=period_label,
                start=start,
                end=end,
                items=cycle_items,
            )
            report["dateRangeLabel"] = date_range_label
            report["periodMonth"] = period_month.strftime("%Y-%m")
            report["complete"] = next_start is not None
            report["salaryDate"] = start.isoformat()
            report["changeFromPrevious"] = cls._period_change(previous, report)
            cycles.append(report)
            previous = report
        return cycles

    @classmethod
    def build_explorer(
        cls,
        transactions: list[dict],
        *,
        start: date | None = None,
        end: date | None = None,
        category: str | None = None,
        merchant: str | None = None,
        financial_role: str | None = None,
    ) -> dict:
        usable = [
            item for item in transactions
            if item.get("transactionType") != "closing_balance"
            and item.get("financialRole") != "balance"
        ]
        selected = []
        for item in usable:
            day = cls._parse_date(item.get("date"))
            if start is not None and (day is None or day < start):
                continue
            if end is not None and (day is None or day > end):
                continue
            if category and item.get("category") != category:
                continue
            if merchant and item.get("displayName") != merchant:
                continue
            if financial_role and item.get("financialRole") != financial_role:
                continue
            selected.append(item)

        if start is None:
            dated = [cls._parse_date(item.get("date")) for item in selected]
            dated = [day for day in dated if day]
            start = min(dated) if dated else date.today()
        if end is None:
            dated = [cls._parse_date(item.get("date")) for item in selected]
            dated = [day for day in dated if day]
            end = max(dated) if dated else start

        report = cls._period_report(
            period_id=f"explore-{start.isoformat()}-{end.isoformat()}",
            label=f"{start.strftime('%d %b %Y')} – {end.strftime('%d %b %Y')}",
            start=start,
            end=end,
            items=selected,
        )
        report["filters"] = {
            "category": category,
            "merchant": merchant,
            "financialRole": financial_role,
        }
        report["transactions"] = selected
        report["availableCategories"] = sorted({
            item.get("category") for item in usable
            if item.get("category") and item.get("category") != "Uncategorised"
        }, key=str.casefold)
        report["availableMerchants"] = sorted({
            item.get("displayName") for item in usable
            if item.get("displayName")
        }, key=str.casefold)
        return report

    @classmethod
    def build_custom_period(cls, transactions: list[dict], start: date, end: date) -> dict:
        if end < start:
            raise ValueError("End date must be on or after the start date.")
        usable = [
            item for item in transactions
            if item.get("transactionType") != "closing_balance"
            and item.get("financialRole") != "balance"
        ]
        items = []
        for item in usable:
            day = cls._parse_date(item.get("date"))
            if day is not None and start <= day <= end:
                items.append(item)
        return cls._period_report(
            period_id=f"custom-{start.isoformat()}-{end.isoformat()}",
            label=f"{start.strftime('%d %b %Y')} – {end.strftime('%d %b %Y')}",
            start=start,
            end=end,
            items=items,
        )

    @classmethod
    def _period_report(
        cls,
        *,
        period_id: str,
        label: str,
        start: date,
        end: date,
        items: list[dict],
    ) -> dict:
        expenses = [
            item for item in items
            if item.get("financialRole") == "expense"
            and cls._number(item.get("amount")) < 0
        ]
        income_items = [
            item for item in items
            if item.get("financialRole") == "income"
            and cls._number(item.get("amount")) > 0
        ]
        refunds = [
            item for item in items
            if item.get("financialRole") == "refund"
            and cls._number(item.get("amount")) > 0
        ]
        transfers = [item for item in items if item.get("financialRole") == "transfer"]

        income = round(sum(cls._number(item.get("amount")) for item in income_items), 2)
        refund_total = round(sum(cls._number(item.get("amount")) for item in refunds), 2)
        spending = round(sum(abs(cls._number(item.get("amount"))) for item in expenses), 2)
        transfer_total = round(sum(abs(cls._number(item.get("amount"))) for item in transfers), 2)
        category_breakdown = cls._breakdown(expenses, "category")
        merchant_breakdown = cls._breakdown(expenses, "displayName", limit=5)
        uncategorised = round(sum(
            abs(cls._number(item.get("amount")))
            for item in expenses
            if not item.get("category") or item.get("category") == "Uncategorised"
        ), 2)
        flexible = round(sum(
            abs(cls._number(item.get("amount")))
            for item in expenses
            if item.get("category") in cls.FLEXIBLE_CATEGORIES
        ), 2)
        days = max((end - start).days + 1, 1)

        return {
            "id": period_id,
            "label": label,
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "days": days,
            "income": income,
            "refunds": refund_total,
            "spending": spending,
            "transfers": transfer_total,
            "netCashFlow": round(income + refund_total - spending, 2),
            "spendingToIncomePct": cls._pct(spending, income),
            "transactionCount": len(items),
            "expenseCount": len(expenses),
            "averageExpenseTransaction": round(spending / len(expenses), 2) if expenses else 0.0,
            "averageDailySpending": round(spending / days, 2),
            "largestExpense": cls._largest_expense(expenses),
            "topCategory": category_breakdown[0] if category_breakdown else None,
            "topMerchant": merchant_breakdown[0] if merchant_breakdown else None,
            "categoryBreakdown": category_breakdown,
            "merchantBreakdown": merchant_breakdown,
            "uncategorisedSpending": uncategorised,
            "uncategorisedSpendingPct": cls._pct(uncategorised, spending),
            "flexibleSpending": flexible,
            "flexibleSpendingPct": cls._pct(flexible, spending),
            **cls._savings_metrics(transfers, income),
        }

    @classmethod
    def _period_change(cls, previous: dict | None, current: dict) -> dict | None:
        if previous is None:
            return None
        return cls._comparison_payload(previous, current)

    @classmethod
    def _month_report(cls, month_key: str, items: list[dict]) -> dict:
        expenses = [
            item for item in items
            if item.get("financialRole") == "expense"
            and cls._number(item.get("amount")) < 0
        ]
        income_items = [
            item for item in items
            if item.get("financialRole") == "income"
            and cls._number(item.get("amount")) > 0
        ]
        refunds = [
            item for item in items
            if item.get("financialRole") == "refund"
            and cls._number(item.get("amount")) > 0
        ]
        transfers = [item for item in items if item.get("financialRole") == "transfer"]

        income = round(sum(cls._number(item.get("amount")) for item in income_items), 2)
        refund_total = round(sum(cls._number(item.get("amount")) for item in refunds), 2)
        spending = round(sum(abs(cls._number(item.get("amount"))) for item in expenses), 2)
        transfer_total = round(sum(abs(cls._number(item.get("amount"))) for item in transfers), 2)
        category_breakdown = cls._breakdown(expenses, "category")
        merchant_breakdown = cls._breakdown(expenses, "displayName", limit=5)
        uncategorised = round(sum(
            abs(cls._number(item.get("amount")))
            for item in expenses
            if not item.get("category") or item.get("category") == "Uncategorised"
        ), 2)
        flexible = round(sum(
            abs(cls._number(item.get("amount")))
            for item in expenses
            if item.get("category") in cls.FLEXIBLE_CATEGORIES
        ), 2)

        return {
            "month": month_key,
            "label": datetime.strptime(month_key, "%Y-%m").strftime("%B %Y"),
            "income": income,
            "refunds": refund_total,
            "spending": spending,
            "transfers": transfer_total,
            "netCashFlow": round(income + refund_total - spending, 2),
            "spendingToIncomePct": cls._pct(spending, income),
            "transactionCount": len(items),
            "expenseCount": len(expenses),
            "averageExpenseTransaction": round(
                spending / len(expenses), 2
            ) if expenses else 0.0,
            "largestExpense": cls._largest_expense(expenses),
            "topCategory": category_breakdown[0] if category_breakdown else None,
            "topMerchant": merchant_breakdown[0] if merchant_breakdown else None,
            "categoryBreakdown": category_breakdown,
            "merchantBreakdown": merchant_breakdown,
            "uncategorisedSpending": uncategorised,
            "uncategorisedSpendingPct": cls._pct(uncategorised, spending),
            "flexibleSpending": flexible,
            "flexibleSpendingPct": cls._pct(flexible, spending),
            **cls._savings_metrics(transfers, income),
        }

    @classmethod
    def _month_change(cls, previous: dict | None, current: dict) -> dict | None:
        if previous is None:
            return None
        previous_key = previous.get("month")
        current_key = current.get("month")
        try:
            previous_month = datetime.strptime(previous_key, "%Y-%m")
            current_month = datetime.strptime(current_key, "%Y-%m")
        except (TypeError, ValueError):
            return None
        expected_year = previous_month.year + (1 if previous_month.month == 12 else 0)
        expected_month = 1 if previous_month.month == 12 else previous_month.month + 1
        if (current_month.year, current_month.month) != (expected_year, expected_month):
            return None
        payload = cls._comparison_payload(previous, current)
        payload["previousMonth"] = previous.get("month")
        return payload

    @classmethod
    def _comparison_payload(cls, previous: dict, current: dict) -> dict:
        return {
            "previousId": previous.get("id") or previous.get("month"),
            "previousLabel": previous.get("label"),
            "currentId": current.get("id") or current.get("month"),
            "currentLabel": current.get("label"),
            "previous": {
                "spending": previous.get("spending", 0),
                "income": previous.get("income", 0),
                "refunds": previous.get("refunds", 0),
                "transfers": previous.get("transfers", 0),
                "netCashFlow": previous.get("netCashFlow", 0),
                "spendingToIncomePct": previous.get("spendingToIncomePct", 0),
                "savingsTransfersOut": previous.get("savingsTransfersOut", 0),
                "flexibleSpendingPct": previous.get("flexibleSpendingPct", 0),
            },
            "current": {
                "spending": current.get("spending", 0),
                "income": current.get("income", 0),
                "refunds": current.get("refunds", 0),
                "transfers": current.get("transfers", 0),
                "netCashFlow": current.get("netCashFlow", 0),
                "spendingToIncomePct": current.get("spendingToIncomePct", 0),
                "savingsTransfersOut": current.get("savingsTransfersOut", 0),
                "flexibleSpendingPct": current.get("flexibleSpendingPct", 0),
            },
            "spendingPct": cls._percent_change(previous.get("spending", 0), current.get("spending", 0)),
            "incomePct": cls._percent_change(previous.get("income", 0), current.get("income", 0)),
            "netCashFlowAmount": round(current.get("netCashFlow", 0) - previous.get("netCashFlow", 0), 2),
            "spendingAmount": round(current.get("spending", 0) - previous.get("spending", 0), 2),
            "incomeAmount": round(current.get("income", 0) - previous.get("income", 0), 2),
            "savingsAmount": round(current.get("savingsTransfersOut", 0) - previous.get("savingsTransfersOut", 0), 2),
            "categoryChanges": cls._category_changes(previous, current),
        }

    @classmethod
    def _category_changes(cls, previous: dict, current: dict) -> list[dict]:
        previous_values = {item.get("name"): float(item.get("amount") or 0) for item in previous.get("categoryBreakdown", []) if item.get("name")}
        current_values = {item.get("name"): float(item.get("amount") or 0) for item in current.get("categoryBreakdown", []) if item.get("name")}
        result = []
        for name in set(previous_values) | set(current_values):
            previous_amount = previous_values.get(name, 0.0)
            current_amount = current_values.get(name, 0.0)
            change_amount = round(current_amount - previous_amount, 2)
            result.append({
                "name": name,
                "previousAmount": round(previous_amount, 2),
                "currentAmount": round(current_amount, 2),
                "changeAmount": change_amount,
                "changePct": cls._percent_change(previous_amount, current_amount),
            })
        result.sort(key=lambda item: (-abs(item["changeAmount"]), item["name"].casefold()))
        return result

    @classmethod
    def _multi_month_summary(cls, monthly: list[dict], *, include_partial: bool) -> dict:
        selected = [
            item for item in monthly
            if include_partial or item.get("coverageStatus") != "partial"
        ]
        rows = []
        previous = None
        for item in selected:
            row = dict(item)
            row["changeFromPrevious"] = cls._month_change(previous, row)
            rows.append(row)
            previous = row

        spending_values = [float(item.get("spending") or 0) for item in rows]
        income_values = [float(item.get("income") or 0) for item in rows]
        net_values = [float(item.get("netCashFlow") or 0) for item in rows]
        highest = max(rows, key=lambda item: float(item.get("spending") or 0), default=None)
        lowest = min(rows, key=lambda item: float(item.get("spending") or 0), default=None)
        return {
            "includePartialMonths": include_partial,
            "monthCount": len(rows),
            "partialMonthCount": sum(1 for item in rows if item.get("coverageStatus") == "partial"),
            "months": rows,
            "categoryTrend": cls._category_trend(rows),
            "averageSpending": cls._average(spending_values),
            "averageIncome": cls._average(income_values),
            "averageNetCashFlow": cls._average(net_values),
            "highestSpendingMonth": {
                "month": highest.get("month"), "label": highest.get("label"), "spending": highest.get("spending", 0)
            } if highest else None,
            "lowestSpendingMonth": {
                "month": lowest.get("month"), "label": lowest.get("label"), "spending": lowest.get("spending", 0)
            } if lowest else None,
        }

    @classmethod
    def _category_trend(cls, monthly: list[dict]) -> dict:
        category_names = sorted({
            item.get("name")
            for month in monthly
            for item in month.get("categoryBreakdown", [])
            if item.get("name")
        }, key=str.casefold)

        points = []
        for month in monthly:
            values = {item["name"]: item["amount"] for item in month.get("categoryBreakdown", [])}
            points.append({
                "month": month.get("month"),
                "label": month.get("label"),
                "values": {name: round(float(values.get(name, 0)), 2) for name in category_names},
            })

        return {
            "categories": category_names,
            "points": points,
        }

    @classmethod
    def _breakdown(cls, items: list[dict], field: str, limit: int | None = None) -> list[dict]:
        totals: dict[str, float] = defaultdict(float)
        total_spending = 0.0
        for item in items:
            value = abs(cls._number(item.get("amount")))
            total_spending += value
            name = (item.get(field) or "Unknown").strip() or "Unknown"
            totals[name] += value

        ordered = sorted(totals.items(), key=lambda pair: (-pair[1], pair[0].casefold()))
        if limit is not None:
            ordered = ordered[:limit]
        max_value = ordered[0][1] if ordered else 0.0

        return [
            {
                "name": name,
                "amount": round(amount, 2),
                "sharePct": cls._pct(amount, total_spending),
                "barPct": cls._pct(amount, max_value),
            }
            for name, amount in ordered
        ]

    @classmethod
    def _recurring_merchants(cls, expenses: list[dict]) -> list[dict]:
        grouped: dict[str, dict] = {}
        for item in expenses:
            name = (item.get("displayName") or "Unknown").strip() or "Unknown"
            month = cls._month_key(item.get("date"))
            if not month:
                continue
            entry = grouped.setdefault(name, {"months": set(), "count": 0, "total": 0.0})
            entry["months"].add(month)
            entry["count"] += 1
            entry["total"] += abs(cls._number(item.get("amount")))

        result = []
        for name, entry in grouped.items():
            month_count = len(entry["months"])
            if month_count < 2:
                continue
            result.append({
                "name": name,
                "months": month_count,
                "transactionCount": entry["count"],
                "total": round(entry["total"], 2),
                "averagePerMonth": round(entry["total"] / month_count, 2),
            })

        result.sort(key=lambda item: (-item["total"], item["name"].casefold()))
        return result[:8]

    @classmethod
    def _savings_metrics(cls, transfers: list[dict], income: float) -> dict:
        savings_transfers = [item for item in transfers if cls._looks_like_savings(item)]
        outflow = round(sum(
            abs(cls._number(item.get("amount")))
            for item in savings_transfers
            if cls._number(item.get("amount")) < 0
        ), 2)
        inflow = round(sum(
            cls._number(item.get("amount"))
            for item in savings_transfers
            if cls._number(item.get("amount")) > 0
        ), 2)
        net = round(outflow - inflow, 2)
        return {
            "savingsTransfersOut": outflow,
            "savingsTransfersIn": inflow,
            "netSavingsMovement": net,
            "savingsTransferRatePct": cls._pct(outflow, income) if income > 0 else None,
        }

    @staticmethod
    def _looks_like_savings(item: dict) -> bool:
        text = " ".join(
            str(part or "")
            for part in (
                item.get("category"),
                item.get("displayName"),
                item.get("rawDescription"),
            )
        ).casefold()
        return "saving" in text or "savings" in text

    @classmethod
    def _confidence_overview(cls, items: list[dict]) -> dict:
        result = {}
        for field, key in (
            ("roleConfidence", "role"),
            ("categoryConfidence", "category"),
            ("namingConfidence", "naming"),
            ("extractionConfidence", "extraction"),
        ):
            counts = Counter((item.get(field) or "unknown") for item in items)
            result[key] = {
                "high": counts.get("high", 0),
                "medium": counts.get("medium", 0),
                "low": counts.get("low", 0),
                "unknown": counts.get("unknown", 0),
            }
        return result

    @classmethod
    def _insights(
        cls,
        *,
        monthly: list[dict],
        category_breakdown: list[dict],
        spending: float,
        flexible_spending: float,
        uncategorised_spending: float,
        savings: dict,
    ) -> list[dict]:
        insights: list[dict] = []

        if len(monthly) >= 2:
            latest = monthly[-1]
            change = latest.get("changeFromPrevious") or {}
            pct = change.get("spendingPct")
            if pct is not None:
                direction = "higher" if pct > 0 else "lower" if pct < 0 else "the same"
                insights.append({
                    "type": "monthly-change",
                    "title": "Monthly spending changed",
                    "value": f"{abs(pct):.1f}% {direction}",
                    "detail": (
                        f"{latest['label']} spending was {direction} than "
                        f"{change.get('previousLabel')}."
                    ),
                })

        if category_breakdown:
            top = category_breakdown[0]
            insights.append({
                "type": "top-category",
                "title": "Largest spending category",
                "value": top["name"],
                "detail": f"{top['sharePct']:.1f}% of all confirmed spending.",
            })

        if spending > 0:
            flexible_pct = cls._pct(flexible_spending, spending)
            insights.append({
                "type": "flexible-share",
                "title": "Flexible spending share",
                "value": f"{flexible_pct:.1f}%",
                "detail": "Eating Out, Entertainment, Shopping and Subscriptions combined.",
            })

            uncategorised_pct = cls._pct(uncategorised_spending, spending)
            if uncategorised_pct > 0:
                insights.append({
                    "type": "data-quality",
                    "title": "Spending still uncategorised",
                    "value": f"{uncategorised_pct:.1f}%",
                    "detail": "Reviewing these transactions will make category reporting more accurate.",
                })

        if savings.get("savingsTransfersOut", 0) > 0:
            rate = savings.get("savingsTransferRatePct")
            insights.append({
                "type": "savings",
                "title": "Moved to savings",
                "value": round(savings["savingsTransfersOut"], 2),
                "valueType": "currency",
                "detail": (
                    f"About {rate:.1f}% of confirmed income was moved to savings."
                    if rate is not None
                    else "Savings transfers are shown separately from spending."
                ),
            })

        return insights[:5]

    @classmethod
    def _largest_expense(cls, expenses: list[dict]) -> dict | None:
        if not expenses:
            return None
        item = max(expenses, key=lambda row: abs(cls._number(row.get("amount"))))
        return {
            "name": item.get("displayName") or item.get("rawDescription") or "Unknown",
            "amount": round(abs(cls._number(item.get("amount"))), 2),
            "date": item.get("date"),
            "category": item.get("category") or "Uncategorised",
        }

    @classmethod
    def _average_daily_spending(cls, expenses: list[dict]) -> float:
        if not expenses:
            return 0.0
        parsed_dates = [cls._parse_date(item.get("date")) for item in expenses]
        parsed_dates = [item for item in parsed_dates if item is not None]
        if not parsed_dates:
            return 0.0
        day_count = max((max(parsed_dates) - min(parsed_dates)).days + 1, 1)
        total = sum(abs(cls._number(item.get("amount"))) for item in expenses)
        return round(total / day_count, 2)

    @staticmethod
    def _average(values: list[float]) -> float:
        return round(sum(values) / len(values), 2) if values else 0.0

    @staticmethod
    def _number(value) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _parse_date(value: str | None) -> date | None:
        if not value:
            return None
        try:
            return date.fromisoformat(value[:10])
        except (TypeError, ValueError):
            return None

    @classmethod
    def _month_key(cls, value: str | None) -> str | None:
        parsed = cls._parse_date(value)
        return parsed.strftime("%Y-%m") if parsed else None

    @staticmethod
    def _pct(value: float, total: float) -> float:
        if not total:
            return 0.0
        return round((value / total) * 100, 1)

    @staticmethod
    def _percent_change(previous: float, current: float) -> float | None:
        if previous == 0:
            return None
        return round(((current - previous) / previous) * 100, 1)
